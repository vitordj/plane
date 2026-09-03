# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who executes the work an area owns.

Everything that changes a work item's executor goes through here — the app,
the public API, the sweeps, the management commands — for three reasons that
each cost a defect to learn:

* **One writer.** Two code paths deciding assignment means two rankings, and
  the second one is always the one nobody tested.
* **One record.** Every change writes an ``AssignmentDecision`` with the
  ranking it used (invariant I5), so "why them?" has an answer that does not
  depend on anyone's memory.
* **One lock.** Automatic allocation reads a load and then writes to it. Two
  requests reading the same load hand the same person both work items, which
  is exactly what least-loaded exists to prevent, so those runs serialize on a
  Postgres advisory lock per area.

Never writes ``ProjectMember`` (invariant I10): project access comes from the
reconcilers, and an assignment that granted access would be a way to get into
a project by being given a task.
"""

# Python imports
import logging
from contextlib import contextmanager
from datetime import timedelta
from dataclasses import dataclass, field
from typing import Optional

# Django imports
from django.db import connection, transaction
from django.utils import timezone

# Module imports
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionOutcome,
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    PolicySource,
    ProjectMember,
    QueueReason,
    RequestedAssignmentMode,
    RoutingState,
    StateGroup,
)

from . import metrics
from .availability import allocation_settings_for, unavailable_member_ids
from .coverage import unit_covers_project
from .errors import (
    AlreadyClaimed,
    AssignmentModeNotAllowed,
    DecisionStale,
    ExecutorNotEligible,
    InvalidTransition,
    UnitNotCoveringProject,
)

logger = logging.getLogger("plane.orca.assignment")

# Work in these states no longer counts toward anybody's load.
CLOSED_STATE_GROUPS = [StateGroup.COMPLETED.value, StateGroup.CANCELLED.value]

# The ranking this module implements. "lb-2" reads availability and the two
# kinds of ceiling on top of what "lb-1" did; decisions already written keep
# saying which one decided them, which is the point of recording it.
ALGORITHM_VERSION = "lb-2"

# Minimum project role that may be handed work.
PROJECT_MEMBER_ROLE = 15

# Which queue reason each mode parks a work item with, when nobody is chosen.
QUEUE_REASON_FOR_MODE = {
    AssignmentMode.MANUAL: QueueReason.AWAITING_COORDINATOR,
    AssignmentMode.SELF_CLAIM: QueueReason.AWAITING_CLAIM,
}

# States from which somebody may still take the work.
CLAIMABLE_STATES = (RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED)


@dataclass
class PolicyResolution:
    """The rules in force for one allocation, and where they came from."""

    effective_mode: str
    policy: Optional[OrganizationalUnitAssignmentPolicy]
    policy_source: str
    policy_version: Optional[int]
    assignment_sla_seconds: Optional[int] = None


@dataclass
class RankedCandidates:
    """
    The ranking, elected and excluded alike.

    @description The excluded half matters as much as the elected one: it is
    what turns "nobody was available" from an assertion into something a
    coordinator can check.
    """

    elected: list = field(default_factory=list)
    excluded: list = field(default_factory=list)

    @property
    def snapshot(self) -> list:
        """Both halves, in the shape ``AssignmentDecision`` stores."""
        return list(self.elected) + list(self.excluded)

    @property
    def best_user_id(self):
        return self.elected[0]["user_id"] if self.elected else None


@dataclass
class AllocationResult:
    """What an allocation did, in the terms the caller answers with."""

    link: IssueOrganizationalUnit
    decision: Optional[AssignmentDecision]
    outcome: str
    executor_id: Optional[str] = None


@dataclass
class TransferResult:
    """A change of responsible area, and the allocation that followed it."""

    link: IssueOrganizationalUnit
    event: IssueResponsibilityEvent
    allocation: Optional[AllocationResult] = None


@contextmanager
def unit_allocation_lock(unit_id):
    """
    Serialize automatic allocation within one area.

    @description ``least_loaded`` reads a load and then adds to it, so two
    concurrent requests that read the same load both pick the same person.
    A transaction-scoped advisory lock makes the second request read the load
    the first one produced. Scoped per area: allocations in different areas
    never wait on each other.

    Must be called inside ``transaction.atomic()`` — the lock is released when
    that transaction ends, and taking it outside one would hold it forever.
    @param unit_id: The area whose allocations serialize.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"orca-alloc-{unit_id}"])
    # Other backends: no lock. The suite and every deployment run Postgres;
    # this keeps an import-time failure from being the way that is discovered.
    yield


# --- policy ------------------------------------------------------------------


def resolve_policy(unit, project_id, requested_mode=None) -> PolicyResolution:
    """
    Work out how this area assigns this work item.

    @description A policy for the area-and-project pair wins over the area's
    default; with neither, the fallback is manual, because handing work to
    somebody automatically is a decision an area has to opt into.

    A ``requested_mode`` outside the allowed list is refused rather than
    downgraded (invariant I7): an automation that asked for automatic
    allocation and silently got a manual queue looks like it worked, and the
    work sits there.
    @param unit: The responsible area.
    @param project_id: The work item's project.
    @param requested_mode: What the caller asked for, if anything.
    @returns: A ``PolicyResolution``.
    @raises AssignmentModeNotAllowed: When the requested mode is not allowed.
    """
    unit_project = OrganizationalUnitProject.objects.filter(organizational_unit=unit, project_id=project_id).first()

    policy_project = None
    if unit_project is not None:
        policy_project = OrganizationalUnitAssignmentPolicy.objects.filter(
            organizational_unit=unit, unit_project=unit_project, is_active=True
        ).first()
    policy_unit = OrganizationalUnitAssignmentPolicy.objects.filter(
        organizational_unit=unit, unit_project__isnull=True, is_active=True
    ).first()

    policy = policy_project or policy_unit
    allowed = (policy.allowed_modes if policy else None) or [AssignmentMode.MANUAL]
    sla = None
    if policy_project is not None and policy_project.assignment_sla_seconds is not None:
        sla = policy_project.assignment_sla_seconds
    elif policy_unit is not None:
        sla = policy_unit.assignment_sla_seconds

    named_modes = {AssignmentMode.MANUAL, AssignmentMode.SELF_CLAIM, AssignmentMode.LEAST_LOADED}
    if requested_mode in named_modes:
        if requested_mode not in allowed:
            raise AssignmentModeNotAllowed(
                f"{requested_mode} is not allowed for this area",
                requested_mode=requested_mode,
                allowed_modes=list(allowed),
            )
        return PolicyResolution(
            effective_mode=requested_mode,
            policy=policy,
            policy_source=PolicySource.REQUEST,
            policy_version=policy.version if policy else None,
            assignment_sla_seconds=sla,
        )

    if policy_project is not None:
        return PolicyResolution(
            effective_mode=policy_project.default_mode,
            policy=policy_project,
            policy_source=PolicySource.UNIT_PROJECT,
            policy_version=policy_project.version,
            assignment_sla_seconds=sla,
        )
    if policy_unit is not None:
        return PolicyResolution(
            effective_mode=policy_unit.default_mode,
            policy=policy_unit,
            policy_source=PolicySource.UNIT,
            policy_version=policy_unit.version,
            assignment_sla_seconds=sla,
        )
    return PolicyResolution(
        effective_mode=AssignmentMode.MANUAL,
        policy=None,
        policy_source=PolicySource.FALLBACK,
        policy_version=None,
        assignment_sla_seconds=None,
    )


# --- ranking -----------------------------------------------------------------


def eligible_user_ids(unit, project_id) -> list:
    """
    The people this area may hand work to on this project.

    @description Active in the area, active in the workspace, an active
    project member with a role that can be assigned, and not a bot. The same
    constraint the native assignee validation applies — checked before
    assigning rather than discovered after.
    @param unit: The area.
    @param project_id: The project.
    @returns: User ids, unordered.
    """
    member_user_ids = list(
        OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit,
            is_active=True,
            workspace_member__is_active=True,
        ).values_list("workspace_member__member_id", flat=True)
    )
    if not member_user_ids:
        return []

    return list(
        ProjectMember.objects.filter(
            project_id=project_id,
            member_id__in=member_user_ids,
            is_active=True,
            role__gte=PROJECT_MEMBER_ROLE,
            member__is_bot=False,
        ).values_list("member_id", flat=True)
    )


def rank_candidates(unit, project_id, policy=None) -> RankedCandidates:
    """
    Rank the area's members for one work item (algorithm ``lb-1``).

    @description Load is counted as primary executions, not as native
    assignments: somebody added to a work item as a collaborator has not been
    given the work, and counting them would make them look busy and quietly
    stop them receiving any. Counted workspace-wide first, then within the
    area, so an area does not load somebody who is already carrying another
    area's work.

    Order: fewest open items, then fewest in this area, then whoever was
    picked automatically longest ago (never, first), then user id — so the
    same inputs always produce the same choice.

    Four things take somebody out of the running, and each is reported by
    name: they are away (``unavailable``), they have switched off new work
    from this area (``opted_out``), they are at their own ceiling
    (``member_limit``), or at the area's (``policy_limit``). Being excluded is
    never the same as being removed — they keep what they are carrying, and a
    coordinator can still hand them something by name.
    @param unit: The area.
    @param project_id: The project the work item belongs to.
    @param policy: The resolved policy, for its per-member limit.
    @returns: ``RankedCandidates``, elected in order plus everyone excluded
        and why.
    """
    user_ids = eligible_user_ids(unit, project_id)
    if not user_ids:
        return RankedCandidates()

    # The area membership behind each candidate: availability is asked of the
    # person (a holiday is workspace-wide), the per-area settings of the
    # membership. Both are read in one query rather than one per candidate,
    # because this runs on the hot path of every automatic allocation.
    membership_rows = OrganizationalUnitMembership.objects.filter(
        organizational_unit=unit,
        is_active=True,
        workspace_member__member_id__in=user_ids,
    ).values_list("workspace_member__member_id", "id", "workspace_member_id")
    membership_of = {user_id: membership_id for user_id, membership_id, _ in membership_rows}
    workspace_member_of = {user_id: member_id for user_id, _, member_id in membership_rows}

    away_member_ids = unavailable_member_ids(workspace_member_of.values())
    settings_by_membership = allocation_settings_for(membership_of.values())

    open_links = IssueOrganizationalUnit.objects.filter(
        primary_executor_id__in=user_ids,
        routing_state=RoutingState.ASSIGNED,
        workspace_id=unit.workspace_id,
    ).exclude(issue__state__group__in=CLOSED_STATE_GROUPS)

    total_open = {}
    unit_open = {}
    for executor_id, unit_id in open_links.values_list("primary_executor_id", "organizational_unit_id"):
        total_open[executor_id] = total_open.get(executor_id, 0) + 1
        if unit_id == unit.id:
            unit_open[executor_id] = unit_open.get(executor_id, 0) + 1

    last_auto = {}
    automatic = (
        AssignmentDecision.objects.filter(
            chosen_assignee_id__in=user_ids,
            effective_mode=AssignmentMode.LEAST_LOADED,
            outcome=DecisionOutcome.ASSIGNED,
        )
        .order_by("chosen_assignee_id", "-created_at")
        .values_list("chosen_assignee_id", "created_at")
    )
    for user_id, created_at in automatic:
        last_auto.setdefault(user_id, created_at)

    policy_limit = policy.max_open_items_per_member if policy else None

    elected, excluded = [], []
    for user_id in user_ids:
        row = {
            "user_id": str(user_id),
            "total_open": total_open.get(user_id, 0),
            "unit_open": unit_open.get(user_id, 0),
            "last_auto_at": last_auto.get(user_id).isoformat() if last_auto.get(user_id) else None,
        }
        reason = _exclusion_reason(
            row["total_open"],
            away=workspace_member_of.get(user_id) in away_member_ids,
            member_settings=settings_by_membership.get(membership_of.get(user_id)),
            policy_limit=policy_limit,
        )
        if reason is not None:
            excluded.append({**row, "excluded_reason": reason})
            continue
        elected.append(row)

    # None sorts first: somebody who has never been picked automatically goes
    # ahead of somebody who was picked an hour ago, at equal load.
    elected.sort(key=lambda row: (row["total_open"], row["unit_open"], row["last_auto_at"] or "", row["user_id"]))
    return RankedCandidates(elected=elected, excluded=excluded)


# --- helpers -----------------------------------------------------------------


def _exclusion_reason(total_open, *, away, member_settings, policy_limit):
    """
    Why the ranking will not pick somebody, or ``None`` when it will.

    @description Named reasons rather than one "not eligible", because they
    call for completely different actions: an area where everybody is away
    needs cover, and an area where everybody is at their ceiling needs the
    ceiling looked at. The order is most-specific-first, so the reason shown
    is the one closest to the person.
    @param total_open: Their open work, counted workspace-wide.
    @param away: Whether an availability interval covers now.
    @param member_settings: Their ``MembershipAllocationSettings``, or None.
    @param policy_limit: The area's per-member ceiling, or None.
    @returns: ``unavailable`` / ``opted_out`` / ``member_limit`` /
        ``policy_limit``, or ``None``.
    """
    if away:
        return "unavailable"
    if member_settings is not None and not member_settings.accepts_new_work:
        return "opted_out"

    personal_limit = getattr(member_settings, "max_open_items", None)
    if personal_limit is not None and total_open >= personal_limit:
        return "member_limit"
    if policy_limit is not None and total_open >= policy_limit:
        return "policy_limit"
    return None


def _locked_link(issue):
    """
    @description Take the row lock for a work item's area link. Every state
    change starts here, so two callers acting on the same work item queue
    behind each other instead of interleaving.
    @param issue: The work item.
    @returns: The locked ``IssueOrganizationalUnit``.
    @raises InvalidTransition: When the work item has no area.
    """
    link = IssueOrganizationalUnit.objects.select_for_update().filter(issue=issue).first()
    if link is None:
        raise InvalidTransition("this work item has no responsible area", issue_id=str(issue.id))
    return link


def _ensure_assignee(issue, user_id, actor=None):
    """
    @description Make somebody a native assignee, idempotently. Never removes
    anyone: a person who was on the work item before stays on it, as a
    collaborator, so reassignment does not silently drop the context somebody
    already has.
    """
    IssueAssignee.objects.get_or_create(
        issue=issue,
        assignee_id=user_id,
        defaults={
            "project_id": issue.project_id,
            "workspace_id": issue.workspace_id,
            "created_by": actor,
        },
    )


def _due_at(resolution, assignment_due_at=None):
    """@description Explicit due date wins over the policy's SLA; neither means no clock."""
    if assignment_due_at is not None:
        return assignment_due_at
    if resolution.assignment_sla_seconds:
        return timezone.now() + timedelta(seconds=resolution.assignment_sla_seconds)
    return None


def _record(
    *,
    link,
    issue,
    unit,
    trigger,
    requested_mode,
    resolution,
    outcome,
    ranked=None,
    chosen_id=None,
    previous_executor_id=None,
    actor=None,
    reason="",
    supersedes=None,
    operation=None,
):
    """
    @description Write the decision and point the link at it. Every change of
    executor or routing state goes through here — that is invariant I5, and
    the reason a queue can be explained after the fact.
    @returns: The new ``AssignmentDecision``.
    """
    # `operation` is the idempotent automation operation that caused this. The
    # column arrives with the public API in Phase 1 (migration 0138); until
    # then it is accepted and not stored, so callers written against the final
    # signature do not have to change.
    decision = AssignmentDecision.objects.create(
        issue=issue,
        organizational_unit=unit,
        project_id=issue.project_id,
        workspace_id=issue.workspace_id,
        trigger=trigger,
        requested_mode=requested_mode or RequestedAssignmentMode.DEFAULT,
        effective_mode=resolution.effective_mode,
        policy_source=resolution.policy_source,
        policy=resolution.policy,
        policy_version=resolution.policy_version,
        algorithm_version=ALGORITHM_VERSION,
        outcome=outcome,
        candidates_snapshot=ranked.snapshot if ranked else [],
        chosen_assignee_id=chosen_id,
        previous_primary_executor_id=previous_executor_id,
        decided_by=actor,
        supersedes=supersedes,
        reason=reason or "",
    )
    link.current_assignment_decision = decision
    link.save(
        update_fields=[
            "routing_state",
            "queue_reason",
            "queued_at",
            "assignment_due_at",
            "primary_executor",
            "current_assignment_decision",
            "updated_at",
        ]
    )

    metrics.record_assignment_outcome(
        resolution.effective_mode,
        outcome,
        trigger,
        workspace_id=str(issue.workspace_id),
        unit_id=str(unit.id),
        issue_id=str(issue.id),
        decision_id=str(decision.id),
    )
    logger.info(
        "orca.assignment.decided",
        extra={
            "workspace_id": str(issue.workspace_id),
            "unit_id": str(unit.id),
            "issue_id": str(issue.id),
            "decision_id": str(decision.id),
            "mode": str(resolution.effective_mode),
            "outcome": str(outcome),
            "trigger": str(trigger),
        },
    )
    if supersedes is not None:
        metrics.record_decision_superseded(unit.id, supersedes.effective_mode, issue_id=str(issue.id))

    if outcome == DecisionOutcome.ALLOCATION_FAILED:
        # Not something to leave for the next sweep: automatic allocation
        # finding nobody usually means the area's membership or its project
        # links are wrong, and the work sits still until a person looks.
        _alert_allocation_failed(link, issue)

    return decision


def _alert_allocation_failed(link, issue):
    """
    @description Tell whoever runs the area, immediately. Failures here are
    swallowed on purpose: an allocation that succeeded must not be rolled back
    because a notification could not be written.
    """
    try:
        from plane.bgtasks.organizational_queue_task import notify_overdue

        # The link is in memory with the state just written; give the alert the
        # issue it belongs to so it does not go back to the database for it.
        link.issue = issue
        notify_overdue(link)
    except Exception:  # noqa: BLE001 - never fail an allocation over an alert
        logger.warning(
            "orca.assignment.alert_failed",
            extra={"issue_id": str(issue.id), "unit_id": str(link.organizational_unit_id)},
        )


def _park(link, *, queue_reason, due_at, state=RoutingState.QUEUED):
    """@description Put the work item in the queue, keeping the executor slot empty."""
    link.routing_state = state
    link.queue_reason = queue_reason
    link.queued_at = timezone.now()
    link.assignment_due_at = due_at
    link.primary_executor = None


def _hand_over(link, user_id, *, due_at=None):
    """@description Give the work to somebody: assigned, nobody waiting, no queue reason."""
    link.routing_state = RoutingState.ASSIGNED
    link.queue_reason = ""
    link.queued_at = None
    link.assignment_due_at = due_at
    link.primary_executor_id = user_id


# --- operations --------------------------------------------------------------


@transaction.atomic
def allocate(
    issue,
    unit,
    *,
    requested_mode=None,
    explicit_executor=None,
    collaborators=(),
    actor=None,
    trigger,
    operation=None,
    assignment_due_at=None,
    reason="",
) -> AllocationResult:
    """
    Decide who executes this work item, and record why.

    @description The one path that assigns. Resolves the area's policy, ranks
    when the policy says to, writes the native assignee, moves the link's
    queue state and records the decision — all inside one transaction, with
    the area's advisory lock held for automatic allocation so two concurrent
    calls cannot hand the same person both work items.
    @param issue: The work item.
    @param unit: The responsible area.
    @param requested_mode: manual, self_claim, least_loaded, or None for the
        area's default. ``explicit_executor`` bypasses this.
    @param explicit_executor: A named person; still checked for eligibility.
    @param collaborators: People to add as native assignees alongside the
        executor. They carry no responsibility and no load.
    @param actor: Who is doing this; ``None`` means the system.
    @param trigger: What set it off (``DecisionTrigger``).
    @param operation: The idempotent automation operation, when there is one.
        Accepted now, recorded from Phase 1 (the column arrives with 0138).
    @param assignment_due_at: Explicit SLA, overriding the policy's.
    @returns: An ``AllocationResult``.
    @raises UnitNotCoveringProject, AssignmentModeNotAllowed, ExecutorNotEligible
    """
    if not unit_covers_project(unit, issue.project_id):
        raise UnitNotCoveringProject("the area does not cover this project", unit_id=str(unit.id))

    if explicit_executor is not None:
        resolution = PolicyResolution(
            effective_mode=AssignmentMode.EXPLICIT,
            policy=None,
            policy_source=PolicySource.REQUEST,
            policy_version=None,
        )
        requested_mode = RequestedAssignmentMode.EXPLICIT
    else:
        resolution = resolve_policy(unit, issue.project_id, requested_mode)

    # Only automatic allocation reads a load it is about to change, so only it
    # needs the area-wide lock; manual and claim paths take the row lock alone.
    if resolution.effective_mode == AssignmentMode.LEAST_LOADED:
        with unit_allocation_lock(unit.id):
            return _allocate_locked(
                issue,
                unit,
                resolution=resolution,
                requested_mode=requested_mode,
                explicit_executor=explicit_executor,
                collaborators=collaborators,
                actor=actor,
                trigger=trigger,
                operation=operation,
                assignment_due_at=assignment_due_at,
                reason=reason,
            )

    return _allocate_locked(
        issue,
        unit,
        resolution=resolution,
        requested_mode=requested_mode,
        explicit_executor=explicit_executor,
        collaborators=collaborators,
        actor=actor,
        trigger=trigger,
        operation=operation,
        assignment_due_at=assignment_due_at,
        reason=reason,
    )


def _allocate_locked(
    issue,
    unit,
    *,
    resolution,
    requested_mode,
    explicit_executor,
    collaborators,
    actor,
    trigger,
    operation,
    assignment_due_at,
    reason,
) -> AllocationResult:
    """@description The body of ``allocate``, with the locks already held."""
    link = _locked_link(issue)
    previous_executor_id = link.primary_executor_id
    due_at = _due_at(resolution, assignment_due_at)
    ranked = None
    chosen_id = None

    if explicit_executor is not None:
        executor_id = getattr(explicit_executor, "id", explicit_executor)
        if executor_id not in eligible_user_ids(unit, issue.project_id):
            raise ExecutorNotEligible(
                "that person is not an eligible member of this area on this project",
                user_id=str(executor_id),
            )
        chosen_id = executor_id

    elif resolution.effective_mode == AssignmentMode.LEAST_LOADED:
        ranked = rank_candidates(unit, issue.project_id, resolution.policy)
        chosen_id = ranked.best_user_id
        if chosen_id is None:
            metrics.record_no_candidate(unit.id, issue_id=str(issue.id))

    if chosen_id is not None:
        _ensure_assignee(issue, chosen_id, actor)
        _hand_over(link, chosen_id, due_at=due_at)
        outcome = DecisionOutcome.ASSIGNED
    elif resolution.effective_mode == AssignmentMode.LEAST_LOADED:
        # Ran, found nobody: a different thing from "waiting for a human", and
        # the queue says so, because it needs a different fix.
        _park(link, queue_reason=QueueReason.NO_ELIGIBLE_MEMBER, due_at=due_at, state=RoutingState.ALLOCATION_FAILED)
        outcome = DecisionOutcome.ALLOCATION_FAILED
    else:
        _park(
            link,
            queue_reason=QUEUE_REASON_FOR_MODE.get(resolution.effective_mode, QueueReason.NEW_ITEM),
            due_at=due_at,
        )
        outcome = DecisionOutcome.QUEUED

    for collaborator in collaborators or ():
        collaborator_id = getattr(collaborator, "id", collaborator)
        if collaborator_id != chosen_id:
            _ensure_assignee(issue, collaborator_id, actor)

    decision = _record(
        link=link,
        issue=issue,
        unit=unit,
        trigger=trigger,
        requested_mode=requested_mode,
        resolution=resolution,
        outcome=outcome,
        ranked=ranked,
        chosen_id=chosen_id,
        previous_executor_id=previous_executor_id,
        actor=actor,
        reason=reason,
        operation=operation,
    )
    return AllocationResult(link=link, decision=decision, outcome=outcome, executor_id=chosen_id)


@transaction.atomic
def claim(issue, user, *, actor=None) -> AllocationResult:
    """
    Take a queued work item for yourself.

    @description The row lock decides the race: whoever gets it first becomes
    the executor, and everyone else is told who won rather than silently
    overwriting them.
    @param issue: The work item.
    @param user: The person taking it.
    @param actor: Who made the call; defaults to ``user``.
    @returns: An ``AllocationResult``.
    @raises InvalidTransition: The work item is not claimable.
    @raises AlreadyClaimed: Somebody else already has it.
    @raises ExecutorNotEligible: The claimant is not eligible for this work.
    """
    actor = actor or user
    link = _locked_link(issue)
    unit = link.organizational_unit

    if link.routing_state == RoutingState.ASSIGNED:
        raise AlreadyClaimed("this work item already has an executor", executor_id=str(link.primary_executor_id))
    if link.routing_state not in CLAIMABLE_STATES:
        raise InvalidTransition(
            "this work item is not waiting to be claimed",
            routing_state=link.routing_state,
        )

    user_id = getattr(user, "id", user)
    if user_id not in eligible_user_ids(unit, issue.project_id):
        raise ExecutorNotEligible("you are not an eligible member of this area on this project")

    resolution = resolve_policy(unit, issue.project_id, AssignmentMode.SELF_CLAIM)
    previous_executor_id = link.primary_executor_id

    _ensure_assignee(issue, user_id, actor)
    _hand_over(link, user_id, due_at=link.assignment_due_at)
    decision = _record(
        link=link,
        issue=issue,
        unit=unit,
        trigger="ui_claim",
        requested_mode=RequestedAssignmentMode.SELF_CLAIM,
        resolution=resolution,
        outcome=DecisionOutcome.ASSIGNED,
        chosen_id=user_id,
        previous_executor_id=previous_executor_id,
        actor=actor,
    )
    return AllocationResult(link=link, decision=decision, outcome=DecisionOutcome.ASSIGNED, executor_id=user_id)


@transaction.atomic
def reassign(issue, new_executor, *, actor=None, reason="", expected_decision_id=None) -> AllocationResult:
    """
    Move a work item to a different executor.

    @description ``expected_decision_id`` is an If-Match: the caller says which
    decision they were looking at, and a caller working from a view that has
    since moved is refused rather than overwriting whatever happened in
    between. The previous executor stays on the work item as a collaborator —
    they have context somebody will want.
    @param issue: The work item.
    @param new_executor: Who takes it now.
    @param actor: Who is reassigning.
    @param reason: Free text, shown in the decision timeline.
    @param expected_decision_id: The decision the caller believes is current.
    @returns: An ``AllocationResult``.
    @raises DecisionStale, ExecutorNotEligible
    """
    link = _locked_link(issue)
    unit = link.organizational_unit

    if expected_decision_id is not None and str(link.current_assignment_decision_id) != str(expected_decision_id):
        raise DecisionStale(
            "this work item has moved since you read it",
            current_decision_id=str(link.current_assignment_decision_id),
        )

    executor_id = getattr(new_executor, "id", new_executor)
    if executor_id not in eligible_user_ids(unit, issue.project_id):
        raise ExecutorNotEligible("that person is not an eligible member of this area on this project")

    previous = link.current_assignment_decision
    previous_executor_id = link.primary_executor_id
    resolution = PolicyResolution(
        effective_mode=AssignmentMode.EXPLICIT,
        policy=None,
        policy_source=PolicySource.REQUEST,
        policy_version=None,
    )

    _ensure_assignee(issue, executor_id, actor)
    _hand_over(link, executor_id, due_at=link.assignment_due_at)
    decision = _record(
        link=link,
        issue=issue,
        unit=unit,
        trigger="reassign",
        requested_mode=RequestedAssignmentMode.EXPLICIT,
        resolution=resolution,
        outcome=DecisionOutcome.ASSIGNED,
        chosen_id=executor_id,
        previous_executor_id=previous_executor_id,
        actor=actor,
        reason=reason,
        supersedes=previous,
    )
    return AllocationResult(link=link, decision=decision, outcome=DecisionOutcome.ASSIGNED, executor_id=executor_id)


@transaction.atomic
def return_to_queue(
    issue, *, actor=None, reason="", queue_reason=QueueReason.MANUALLY_RETURNED, trigger="return_to_queue"
) -> AllocationResult:
    """
    Put a work item back in its area's queue.

    @description Clears the executor and nothing else: the native assignee
    stays, so the person who was carrying it keeps seeing it until somebody
    else picks it up. Used by a coordinator handing work back, and by the
    availability sweep when an executor goes away.
    @param issue: The work item.
    @param actor: Who returned it; ``None`` means the system did.
    @param reason: Free text for the timeline.
    @param queue_reason: Why it is waiting again.
    @param trigger: What set it off.
    @returns: An ``AllocationResult``.
    """
    link = _locked_link(issue)
    unit = link.organizational_unit
    previous = link.current_assignment_decision
    previous_executor_id = link.primary_executor_id

    resolution = resolve_policy(unit, issue.project_id)
    _park(link, queue_reason=queue_reason, due_at=_due_at(resolution))
    decision = _record(
        link=link,
        issue=issue,
        unit=unit,
        trigger=trigger,
        requested_mode=RequestedAssignmentMode.DEFAULT,
        resolution=resolution,
        outcome=DecisionOutcome.QUEUED,
        previous_executor_id=previous_executor_id,
        actor=actor,
        reason=reason,
        supersedes=previous,
    )
    return AllocationResult(link=link, decision=decision, outcome=DecisionOutcome.QUEUED, executor_id=None)


@transaction.atomic
def transfer_unit(issue, to_unit, *, actor=None, source="internal_api", reason="", trigger="internal_api"):
    """
    Move responsibility for a work item to another area.

    @description Two things happen and both are recorded: the responsibility
    event says the area changed, and an allocation decides who executes it
    under the new area's policy. An executor who does not belong to the new
    area loses the work item — they are not that area's to command — but stays
    on it as a collaborator.
    @param issue: The work item.
    @param to_unit: The area taking it over.
    @param actor: Who is transferring.
    @param source: Where the transfer came from.
    @param reason: Free text.
    @returns: A ``TransferResult``.
    @raises UnitNotCoveringProject: The destination does not cover the project.
    """
    if not unit_covers_project(to_unit, issue.project_id):
        raise UnitNotCoveringProject("the destination area does not cover this project", unit_id=str(to_unit.id))

    link = _locked_link(issue)
    from_unit = link.organizational_unit

    event = IssueResponsibilityEvent.objects.create(
        issue=issue,
        workspace_id=issue.workspace_id,
        from_unit=from_unit,
        to_unit=to_unit,
        actor=actor,
        source=source,
        reason=reason or "",
    )

    link.organizational_unit = to_unit
    link.save(update_fields=["organizational_unit", "updated_at"])

    allocation = allocate(
        issue,
        to_unit,
        actor=actor,
        trigger=trigger,
        reason=reason,
    )
    return TransferResult(link=allocation.link, event=event, allocation=allocation)


@transaction.atomic
def set_responsibility(
    issue,
    unit,
    *,
    actor=None,
    source="internal_api",
    requested_mode=None,
    explicit_executor=None,
    collaborators=(),
    trigger="internal_api",
    assignment_due_at=None,
    reason="",
):
    """
    Make an area responsible for a work item, and allocate under its policy.

    @description The entry point behind "mark this area responsible": creates
    the link when there is none (with the event that records the work item
    entering the layer), hands over to ``transfer_unit`` when another area
    already owns it, and then allocates either way.
    @param issue: The work item.
    @param unit: The area taking responsibility.
    @returns: An ``AllocationResult``.
    @raises UnitNotCoveringProject
    """
    if not unit_covers_project(unit, issue.project_id):
        raise UnitNotCoveringProject("the area does not cover this project", unit_id=str(unit.id))

    link = IssueOrganizationalUnit.objects.select_for_update().filter(issue=issue).first()

    if link is not None and link.organizational_unit_id != unit.id:
        transfer = transfer_unit(issue, unit, actor=actor, source=source, reason=reason, trigger=trigger)
        return transfer.allocation

    if link is None:
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project_id=issue.project_id,
            workspace_id=issue.workspace_id,
            queue_reason=QueueReason.NEW_ITEM,
            queued_at=timezone.now(),
        )
        IssueResponsibilityEvent.objects.create(
            issue=issue,
            workspace_id=issue.workspace_id,
            from_unit=None,
            to_unit=unit,
            actor=actor,
            source=source,
            reason=reason or "",
        )

    return allocate(
        issue,
        unit,
        requested_mode=requested_mode,
        explicit_executor=explicit_executor,
        collaborators=collaborators,
        actor=actor,
        trigger=trigger,
        assignment_due_at=assignment_due_at,
        reason=reason,
    )
