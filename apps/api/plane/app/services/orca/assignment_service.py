# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Turning "this area owns this work item" into "this person is doing it".

Every path that changes who is on an item goes through here — the internal
API, the public API, a claim from the interface, a coordinator's
reassignment, a transfer between areas, a management command — because the
rules that make an allocation correct are not the kind you can restate at each
call site and expect to stay in step:

* the area has to cover the project (I2), and the person has to be an active
  member of both the area and the project **at the moment of the decision**
  (I4), since either can change between two requests;
* a mode the policy forbids is refused, never quietly degraded (I7): a caller
  that asked for ``least_loaded`` and got ``manual`` would believe the item was
  assigned while it sits in a queue;
* every change of executor or routing state leaves an ``AssignmentDecision``
  (I5), and every change of area an ``IssueResponsibilityEvent`` (I6) —
  append-only, because "why does this person have this?" is asked days later;
* automatic allocation for one area is serialized by an advisory lock, so the
  load the second request reads already includes the first. Without it, two
  simultaneous items both go to the person who was least loaded a moment ago;
* an existing ``IssueAssignee`` is never removed. Plane shows assignees to
  everyone, and silently taking a person off an item is not something an
  allocator should do — the previous executor stays as a collaborator until a
  human decides otherwise.

This module writes ``IssueAssignee``, ``IssueOrganizationalUnit``,
``AssignmentDecision`` and ``IssueResponsibilityEvent``. It writes no
``ProjectMember`` row (I10): access comes from the reconcilers, and an
allocator that could grant access would be an allocator that can widen who
sees a project.

See docs/orca-work-management-rfc.md §6.
"""

# Python imports
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

# Django imports
from django.db import connection, transaction
from django.utils import timezone

# Module imports
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionOutcome,
    DecisionTrigger,
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitMembership,
    PolicySource,
    ProjectMember,
    QueueReason,
    RequestedAssignmentMode,
    ResponsibilitySource,
    RoutingState,
    StateGroup,
)

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

# Work in these state groups is finished and stops counting toward load.
CLOSED_STATE_GROUPS = [StateGroup.COMPLETED.value, StateGroup.CANCELLED.value]

# Bumped when the ranking changes, and frozen into every decision, so an old
# decision is never read as if it had used today's rules.
ALGORITHM_VERSION = "lb-1"

# The minimum project role that can hold an assignment, matching Plane's own
# assignee validation.
ASSIGNABLE_ROLE = 15

# Modes a caller may ask for; `explicit` bypasses resolution (RFC §6.3).
RESOLVABLE_MODES = {AssignmentMode.MANUAL, AssignmentMode.SELF_CLAIM, AssignmentMode.LEAST_LOADED}

# What an area with no policy of its own permits.
FALLBACK_ALLOWED_MODES = tuple(sorted(mode.value for mode in RESOLVABLE_MODES))

# Why an item is queued, per mode, when nobody was assigned.
QUEUE_REASON_FOR_MODE = {
    AssignmentMode.MANUAL: QueueReason.AWAITING_COORDINATOR,
    AssignmentMode.SELF_CLAIM: QueueReason.AWAITING_CLAIM,
}


@dataclass(frozen=True)
class PolicyResolution:
    """Which policy governs one allocation, and what it decided."""

    effective_mode: str
    policy: Optional[OrganizationalUnitAssignmentPolicy]
    policy_source: str
    policy_version: Optional[int]
    sla_seconds: Optional[int] = None
    max_open_items_per_member: Optional[int] = None
    allowed_modes: tuple = ()


@dataclass(frozen=True)
class Candidate:
    """One person the ranking considered, and the numbers it considered them on."""

    user_id: object
    workspace_member_id: object = None
    total_open: int = 0
    unit_open: int = 0
    last_auto_at: Optional[datetime] = None
    excluded_reason: Optional[str] = None

    def as_snapshot(self) -> dict:
        """
        @description The row written into ``candidates_snapshot``. Ids and
        numbers only — the log has to be auditable without becoming a profile.
        @returns Plain JSON-serializable dict.
        """
        snapshot = {
            "user_id": str(self.user_id),
            "total_open": self.total_open,
            "unit_open": self.unit_open,
            "last_auto_at": self.last_auto_at.isoformat() if self.last_auto_at else None,
        }
        if self.excluded_reason:
            snapshot["excluded_reason"] = self.excluded_reason
        return snapshot


@dataclass(frozen=True)
class RankedCandidates:
    """The ranking's output: who is eligible, in order, and who was left out and why."""

    eligible: list = field(default_factory=list)
    excluded: list = field(default_factory=list)

    def snapshot(self) -> list:
        return [candidate.as_snapshot() for candidate in (*self.eligible, *self.excluded)]


@dataclass(frozen=True)
class AllocationResult:
    """What one call to the service did."""

    link: IssueOrganizationalUnit
    decision: Optional[AssignmentDecision]
    outcome: str
    chosen_user_id: object = None
    queue_reason: str = ""


@dataclass(frozen=True)
class TransferResult:
    """A change of responsible area, and the allocation that followed it."""

    event: IssueResponsibilityEvent
    allocation: Optional[AllocationResult]


def unit_allocation_lock(unit_id) -> None:
    """
    @description Serialize automatic allocation within one area for the rest of
    the transaction. Two requests arriving together would otherwise both read
    the load as it was before either of them, and both pick the same person.
    A transaction-level advisory lock releases itself on commit or rollback, so
    a crashed request cannot leave the area wedged.
    @param unit_id: Id of the area to lock.
    @returns None. Outside PostgreSQL this is a no-op; the deployed and test
        databases are PostgreSQL, and a no-op here only removes the
        serialization, never correctness of a single request.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"orca-alloc-{unit_id}"])


def resolve_policy(unit, project_id, requested_mode: Optional[str] = None) -> PolicyResolution:
    """
    @description Decide which mode governs this allocation (RFC §6.3). The
    project's own policy wins over the area's; a caller's explicit request wins
    over both, but only if the effective policy allows it.
    @param unit: The responsible area.
    @param project_id: Project of the work item.
    @param requested_mode: What the caller asked for, or ``None``/``default``.
    @returns The resolved policy.
    @raises AssignmentModeNotAllowed: When the requested mode is outside
        ``allowed_modes``. Never degraded to the default (I7).
    """
    policies = OrganizationalUnitAssignmentPolicy.objects.filter(organizational_unit=unit, is_active=True)
    policy_project = policies.filter(unit_project__project_id=project_id).first()
    policy_unit = policies.filter(unit_project__isnull=True).first()

    governing = policy_project or policy_unit
    # An area with no policy has decided nothing, so it forbids nothing: a
    # caller may ask for any of the three modes and gets what it asked for.
    # What the absent policy does decide is the *default* — manual, below —
    # so an unconfigured area still never hands work out on its own. Once a
    # policy exists its `allowed_modes` is the whole list, and I7 applies.
    allowed = tuple((governing.allowed_modes if governing else None) or FALLBACK_ALLOWED_MODES)

    # SLA and the load cap fall back independently: a project policy that says
    # nothing about the SLA should inherit the area's, not silently drop it.
    sla_seconds = next(
        (
            policy.assignment_sla_seconds
            for policy in (policy_project, policy_unit)
            if policy is not None and policy.assignment_sla_seconds is not None
        ),
        None,
    )
    max_open = next(
        (
            policy.max_open_items_per_member
            for policy in (policy_project, policy_unit)
            if policy is not None and policy.max_open_items_per_member is not None
        ),
        None,
    )

    if requested_mode and requested_mode != RequestedAssignmentMode.DEFAULT:
        if requested_mode not in RESOLVABLE_MODES:
            raise AssignmentModeNotAllowed(requested_mode=requested_mode, allowed_modes=list(allowed))
        if requested_mode not in allowed:
            raise AssignmentModeNotAllowed(requested_mode=requested_mode, allowed_modes=list(allowed))
        return PolicyResolution(
            effective_mode=requested_mode,
            policy=governing,
            policy_source=PolicySource.REQUEST,
            policy_version=governing.version if governing else None,
            sla_seconds=sla_seconds,
            max_open_items_per_member=max_open,
            allowed_modes=allowed,
        )

    if policy_project:
        source, effective = PolicySource.UNIT_PROJECT, policy_project.default_mode
    elif policy_unit:
        source, effective = PolicySource.UNIT, policy_unit.default_mode
    else:
        source, effective = PolicySource.FALLBACK, AssignmentMode.MANUAL.value

    return PolicyResolution(
        effective_mode=effective,
        policy=governing,
        policy_source=source,
        policy_version=governing.version if governing else None,
        sla_seconds=sla_seconds,
        max_open_items_per_member=max_open,
        allowed_modes=allowed,
    )


def _membership_map(unit) -> dict:
    """@description Active area memberships by user id. @returns dict user_id -> workspace_member_id."""
    return {
        membership.workspace_member.member_id: membership.workspace_member_id
        for membership in OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit, is_active=True, workspace_member__is_active=True
        ).select_related("workspace_member")
    }


def _load_counts(unit, workspace_id, user_ids) -> tuple:
    """
    @description Open work per person, counted over the whole workspace and
    then within the area, as ``lb-1`` requires. Only the **primary executor**
    counts: a collaborator left on an item from an earlier assignment is not
    the person answerable for it, and counting them would keep pushing them
    down the ranking for work they no longer own.
    @returns ``(total_open_by_user, unit_open_by_user)``.
    """
    if not user_ids:
        return {}, {}

    open_links = IssueOrganizationalUnit.objects.filter(
        primary_executor_id__in=user_ids,
        routing_state=RoutingState.ASSIGNED,
        workspace_id=workspace_id,
    ).exclude(issue__state__group__in=CLOSED_STATE_GROUPS)

    total_open, unit_open = {}, {}
    for executor_id, unit_id in open_links.values_list("primary_executor_id", "organizational_unit_id"):
        total_open[executor_id] = total_open.get(executor_id, 0) + 1
        if unit_id == unit.id:
            unit_open[executor_id] = unit_open.get(executor_id, 0) + 1
    return total_open, unit_open


def _last_automatic_assignment(user_ids) -> dict:
    """
    @description When each person last won an **automatic** allocation. Manual
    choices are excluded on purpose: a coordinator handing someone an item is
    not the round-robin's turn being taken.
    @returns dict user_id -> datetime.
    """
    if not user_ids:
        return {}
    rows = (
        AssignmentDecision.objects.filter(
            chosen_assignee_id__in=user_ids,
            effective_mode=AssignmentMode.LEAST_LOADED,
            outcome=DecisionOutcome.ASSIGNED,
        )
        .order_by("chosen_assignee_id", "-created_at")
        .values_list("chosen_assignee_id", "created_at")
    )
    last = {}
    for user_id, created_at in rows:
        last.setdefault(user_id, created_at)
    return last


def rank_candidates(
    unit, project_id, policy: Optional[PolicyResolution] = None, exclude_user_ids: Iterable = ()
) -> RankedCandidates:
    """
    @description The ``lb-1`` ranking (RFC §6.4): least total open work first,
    then least open work in this area, then whoever went longest without an
    automatic assignment (never, first), then user id so two runs over the same
    data always agree.
    @param unit: The responsible area.
    @param project_id: Project of the work item.
    @param policy: Resolved policy, for ``max_open_items_per_member``.
    @param exclude_user_ids: People to leave out of the ranking — used when the
        caller wants somebody *other* than whoever is already on the item.
    @returns Eligible candidates in order, plus those excluded and why.
    """
    memberships = _membership_map(unit)
    if not memberships:
        return RankedCandidates()

    project_members = {
        member.member_id: member
        for member in ProjectMember.objects.filter(
            project_id=project_id, member_id__in=list(memberships), is_active=True
        ).select_related("member")
    }

    skip = {str(user_id) for user_id in exclude_user_ids}
    excluded = []
    eligible_ids = []
    for user_id in memberships:
        member = project_members.get(user_id)
        if str(user_id) in skip:
            excluded.append(Candidate(user_id=user_id, excluded_reason="already_assigned"))
        elif member is None:
            excluded.append(Candidate(user_id=user_id, excluded_reason="not_a_project_member"))
        elif member.role < ASSIGNABLE_ROLE:
            excluded.append(Candidate(user_id=user_id, excluded_reason="project_role_too_low"))
        elif getattr(member.member, "is_bot", False):
            excluded.append(Candidate(user_id=user_id, excluded_reason="bot"))
        else:
            eligible_ids.append(user_id)

    total_open, unit_open = _load_counts(unit, unit.workspace_id, eligible_ids)
    last_auto = _last_automatic_assignment(eligible_ids)
    cap = policy.max_open_items_per_member if policy else None

    eligible = []
    for user_id in eligible_ids:
        candidate = Candidate(
            user_id=user_id,
            workspace_member_id=memberships[user_id],
            total_open=total_open.get(user_id, 0),
            unit_open=unit_open.get(user_id, 0),
            last_auto_at=last_auto.get(user_id),
        )
        if cap is not None and candidate.total_open >= cap:
            excluded.append(
                Candidate(
                    user_id=user_id,
                    workspace_member_id=candidate.workspace_member_id,
                    total_open=candidate.total_open,
                    unit_open=candidate.unit_open,
                    last_auto_at=candidate.last_auto_at,
                    excluded_reason="at_max_open_items",
                )
            )
        else:
            eligible.append(candidate)

    eligible.sort(
        key=lambda candidate: (
            candidate.total_open,
            candidate.unit_open,
            candidate.last_auto_at is not None,
            candidate.last_auto_at or timezone.now(),
            str(candidate.user_id),
        )
    )
    return RankedCandidates(eligible=eligible, excluded=excluded)


def _assert_eligible(unit, project_id, user_id) -> None:
    """
    @description Invariant I4, checked at decision time rather than trusted
    from whatever the caller read earlier: the person is an active member of
    the area and an active project member who can hold an assignment.
    @raises ExecutorNotEligible: When either half fails.
    """
    in_unit = OrganizationalUnitMembership.objects.filter(
        organizational_unit=unit,
        workspace_member__member_id=user_id,
        is_active=True,
        workspace_member__is_active=True,
    ).exists()
    if not in_unit:
        raise ExecutorNotEligible(user_id=str(user_id), reason="not_a_unit_member")

    can_hold = ProjectMember.objects.filter(
        project_id=project_id, member_id=user_id, is_active=True, role__gte=ASSIGNABLE_ROLE, member__is_bot=False
    ).exists()
    if not can_hold:
        raise ExecutorNotEligible(user_id=str(user_id), reason="not_an_assignable_project_member")


def _ensure_assignee(issue, user_id) -> None:
    """@description Attach the person to the item if they are not on it already. Never removes anyone."""
    IssueAssignee.objects.get_or_create(
        issue=issue,
        assignee_id=user_id,
        defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
    )


def _record(
    link,
    *,
    trigger,
    requested_mode,
    resolution: Optional[PolicyResolution],
    outcome,
    snapshot,
    chosen_user_id=None,
    previous_executor_id=None,
    decided_by=None,
    reason="",
) -> AssignmentDecision:
    """@description Write the decision and point the link at it (I5). @returns The new decision."""
    decision = AssignmentDecision.objects.create(
        issue_id=link.issue_id,
        organizational_unit_id=link.organizational_unit_id,
        project_id=link.project_id,
        workspace_id=link.workspace_id,
        trigger=trigger,
        requested_mode=requested_mode or None,
        effective_mode=(resolution.effective_mode if resolution else AssignmentMode.EXPLICIT),
        policy_source=(resolution.policy_source if resolution else PolicySource.REQUEST),
        policy=(resolution.policy if resolution else None),
        policy_version=(resolution.policy_version if resolution else None),
        algorithm_version=ALGORITHM_VERSION,
        outcome=outcome,
        candidates_snapshot=snapshot,
        chosen_assignee_id=chosen_user_id,
        previous_primary_executor_id=previous_executor_id,
        decided_by=decided_by,
        supersedes_id=link.current_assignment_decision_id,
        reason=reason or "",
    )
    logger.info(
        "orca assignment decision",
        extra={
            "workspace_id": str(link.workspace_id),
            "unit_id": str(link.organizational_unit_id),
            "issue_id": str(link.issue_id),
            "decision_id": str(decision.id),
            "mode": decision.effective_mode,
            "outcome": outcome,
            "trigger": trigger,
        },
    )
    return decision


def _apply_assigned(link, decision, user_id) -> None:
    """@description Move the link into ``assigned``, clearing the queue fields."""
    link.routing_state = RoutingState.ASSIGNED
    link.primary_executor_id = user_id
    link.queue_reason = ""
    link.queued_at = None
    link.current_assignment_decision = decision
    link.save(
        update_fields=[
            "routing_state",
            "primary_executor",
            "queue_reason",
            "queued_at",
            "current_assignment_decision",
            "updated_at",
        ]
    )


def _apply_queued(link, decision, *, state, queue_reason, sla_seconds=None, assignment_due_at=None) -> None:
    """@description Move the link into ``queued`` or ``allocation_failed``, and set the SLA."""
    link.routing_state = state
    link.primary_executor_id = None
    link.queue_reason = queue_reason
    link.queued_at = link.queued_at or timezone.now()
    if assignment_due_at is not None:
        link.assignment_due_at = assignment_due_at
    elif sla_seconds:
        link.assignment_due_at = timezone.now() + timedelta(seconds=sla_seconds)
    link.current_assignment_decision = decision
    link.save(
        update_fields=[
            "routing_state",
            "primary_executor",
            "queue_reason",
            "queued_at",
            "assignment_due_at",
            "current_assignment_decision",
            "updated_at",
        ]
    )


def _locked_link(issue):
    """@description The link for this item, locked for the rest of the transaction."""
    link = IssueOrganizationalUnit.objects.select_for_update().filter(issue=issue).first()
    if link is None:
        raise InvalidTransition("work item has no responsible unit", issue_id=str(issue.id))
    return link


def allocate(
    issue,
    unit,
    *,
    requested_mode: Optional[str] = None,
    explicit_executor=None,
    collaborators: Iterable = (),
    exclude_user_ids: Iterable = (),
    actor=None,
    trigger: str = DecisionTrigger.INTERNAL_API,
    assignment_due_at=None,
) -> AllocationResult:
    """
    @description Decide who does this work item, and record why (RFC §6.3-6.5).
    Existing assignees are never removed; ``collaborators`` are added alongside
    whoever ends up primary.
    @param issue: The work item, already linked to ``unit``.
    @param unit: The responsible area.
    @param requested_mode: ``manual``, ``self_claim``, ``least_loaded``,
        ``default`` or ``None``. Ignored when ``explicit_executor`` is given.
    @param explicit_executor: A person the caller names. Skips policy
        resolution and is validated against I4.
    @param collaborators: Extra people to attach, not answerable for the item.
    @param exclude_user_ids: People the ranking must not choose, for a caller
        that wants somebody other than whoever is already on the item.
    @param actor: Who is acting; ``None`` means the system decided.
    @param trigger: Member of ``DecisionTrigger``.
    @param assignment_due_at: Explicit SLA deadline, which wins over policy.
    @returns What happened, including the decision that was written.
    @raises UnitNotCoveringProject, AssignmentModeNotAllowed, ExecutorNotEligible
    """
    if not unit_covers_project(unit, issue.project_id):
        raise UnitNotCoveringProject(unit_id=str(unit.id), project_id=str(issue.project_id))

    resolution = None
    if explicit_executor is None:
        resolution = resolve_policy(unit, issue.project_id, requested_mode)

    needs_lock = resolution is not None and resolution.effective_mode == AssignmentMode.LEAST_LOADED

    with transaction.atomic():
        if needs_lock:
            unit_allocation_lock(unit.id)
        link = _locked_link(issue)
        previous_executor_id = link.primary_executor_id

        for collaborator in collaborators:
            collaborator_id = getattr(collaborator, "id", collaborator)
            _assert_eligible(unit, issue.project_id, collaborator_id)
            _ensure_assignee(issue, collaborator_id)

        # --- explicit: the caller names the person -------------------------
        if explicit_executor is not None:
            executor_id = getattr(explicit_executor, "id", explicit_executor)
            _assert_eligible(unit, issue.project_id, executor_id)
            _ensure_assignee(issue, executor_id)
            decision = _record(
                link,
                trigger=trigger,
                requested_mode=RequestedAssignmentMode.EXPLICIT,
                resolution=None,
                outcome=DecisionOutcome.ASSIGNED,
                snapshot=[Candidate(user_id=executor_id).as_snapshot()],
                chosen_user_id=executor_id,
                previous_executor_id=previous_executor_id,
                decided_by=actor,
            )
            _apply_assigned(link, decision, executor_id)
            return AllocationResult(link, decision, DecisionOutcome.ASSIGNED, executor_id)

        # --- least_loaded: the ranking decides -----------------------------
        if resolution.effective_mode == AssignmentMode.LEAST_LOADED:
            ranked = rank_candidates(unit, issue.project_id, resolution, exclude_user_ids=exclude_user_ids)
            if ranked.eligible:
                chosen = ranked.eligible[0]
                _ensure_assignee(issue, chosen.user_id)
                decision = _record(
                    link,
                    trigger=trigger,
                    requested_mode=requested_mode,
                    resolution=resolution,
                    outcome=DecisionOutcome.ASSIGNED,
                    snapshot=ranked.snapshot(),
                    chosen_user_id=chosen.user_id,
                    previous_executor_id=previous_executor_id,
                    decided_by=actor,
                )
                _apply_assigned(link, decision, chosen.user_id)
                return AllocationResult(link, decision, DecisionOutcome.ASSIGNED, chosen.user_id)

            decision = _record(
                link,
                trigger=trigger,
                requested_mode=requested_mode,
                resolution=resolution,
                outcome=DecisionOutcome.ALLOCATION_FAILED,
                snapshot=ranked.snapshot(),
                previous_executor_id=previous_executor_id,
                decided_by=actor,
            )
            _apply_queued(
                link,
                decision,
                state=RoutingState.ALLOCATION_FAILED,
                queue_reason=QueueReason.NO_ELIGIBLE_MEMBER,
                sla_seconds=resolution.sla_seconds,
                assignment_due_at=assignment_due_at,
            )
            return AllocationResult(
                link, decision, DecisionOutcome.ALLOCATION_FAILED, None, QueueReason.NO_ELIGIBLE_MEMBER
            )

        # --- manual / self_claim: the item waits ---------------------------
        queue_reason = QUEUE_REASON_FOR_MODE.get(resolution.effective_mode, QueueReason.AWAITING_COORDINATOR)
        decision = _record(
            link,
            trigger=trigger,
            requested_mode=requested_mode,
            resolution=resolution,
            outcome=DecisionOutcome.QUEUED,
            snapshot=[],
            previous_executor_id=previous_executor_id,
            decided_by=actor,
        )
        _apply_queued(
            link,
            decision,
            state=RoutingState.QUEUED,
            queue_reason=queue_reason,
            sla_seconds=resolution.sla_seconds,
            assignment_due_at=assignment_due_at,
        )
        return AllocationResult(link, decision, DecisionOutcome.QUEUED, None, queue_reason)


def claim(issue, user, *, actor=None) -> AllocationResult:
    """
    @description Take a queued item for yourself. The row lock is the whole
    mechanism: the second claimer waits for the first to commit and then sees
    an item that is already assigned, rather than both writing.
    @param issue: The work item.
    @param user: The person taking it.
    @param actor: Who acted, when it is not the person themselves.
    @returns The allocation.
    @raises AlreadyClaimed: Someone got there first; carries the winner.
    @raises AssignmentModeNotAllowed: The policy does not allow self-claim.
    @raises ExecutorNotEligible: The claimer cannot hold this work.
    """
    user_id = getattr(user, "id", user)

    with transaction.atomic():
        link = _locked_link(issue)
        unit = link.organizational_unit

        if link.routing_state == RoutingState.ASSIGNED:
            raise AlreadyClaimed(primary_executor_id=str(link.primary_executor_id))
        if link.routing_state not in (RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED):
            raise InvalidTransition(routing_state=link.routing_state)

        resolution = resolve_policy(unit, link.project_id, AssignmentMode.SELF_CLAIM)
        _assert_eligible(unit, link.project_id, user_id)
        _ensure_assignee(issue, user_id)

        decision = _record(
            link,
            trigger=DecisionTrigger.UI_CLAIM,
            requested_mode=RequestedAssignmentMode.SELF_CLAIM,
            resolution=resolution,
            outcome=DecisionOutcome.ASSIGNED,
            snapshot=[Candidate(user_id=user_id).as_snapshot()],
            chosen_user_id=user_id,
            decided_by=actor or user,
        )
        _apply_assigned(link, decision, user_id)
        return AllocationResult(link, decision, DecisionOutcome.ASSIGNED, user_id)


def reassign(issue, new_executor, *, actor=None, reason="", expected_decision_id=None) -> AllocationResult:
    """
    @description Hand the item to somebody else. ``expected_decision_id`` is
    this layer's If-Match: two coordinators reassigning at once must not have
    the second silently overwrite the first.
    @raises DecisionStale: The item moved since the caller read it.
    @raises ExecutorNotEligible: The new person cannot hold this work.
    """
    executor_id = getattr(new_executor, "id", new_executor)

    with transaction.atomic():
        link = _locked_link(issue)
        if expected_decision_id is not None and str(link.current_assignment_decision_id) != str(expected_decision_id):
            raise DecisionStale(current_decision_id=str(link.current_assignment_decision_id))

        unit = link.organizational_unit
        _assert_eligible(unit, link.project_id, executor_id)
        previous_executor_id = link.primary_executor_id
        # The previous executor keeps their IssueAssignee row: they stay a
        # collaborator until a human removes them (RFC §6.8).
        _ensure_assignee(issue, executor_id)

        decision = _record(
            link,
            trigger=DecisionTrigger.REASSIGN,
            requested_mode=RequestedAssignmentMode.EXPLICIT,
            resolution=None,
            outcome=DecisionOutcome.ASSIGNED,
            snapshot=[Candidate(user_id=executor_id).as_snapshot()],
            chosen_user_id=executor_id,
            previous_executor_id=previous_executor_id,
            decided_by=actor,
            reason=reason,
        )
        _apply_assigned(link, decision, executor_id)
        return AllocationResult(link, decision, DecisionOutcome.ASSIGNED, executor_id)


def return_to_queue(issue, *, actor=None, reason="", queue_reason=QueueReason.MANUALLY_RETURNED) -> AllocationResult:
    """
    @description Put an assigned item back in the queue. The person keeps their
    ``IssueAssignee`` row — Plane shows assignees to everyone, and quietly
    detaching somebody is a human's call, not the allocator's.
    """
    with transaction.atomic():
        link = _locked_link(issue)
        if link.routing_state not in (RoutingState.ASSIGNED, RoutingState.SUSPENDED):
            raise InvalidTransition(routing_state=link.routing_state)

        previous_executor_id = link.primary_executor_id
        decision = _record(
            link,
            trigger=DecisionTrigger.RETURN_TO_QUEUE,
            requested_mode=None,
            resolution=None,
            outcome=DecisionOutcome.QUEUED,
            snapshot=[],
            previous_executor_id=previous_executor_id,
            decided_by=actor,
            reason=reason,
        )
        link.queued_at = None  # the wait starts now, not when it was first queued
        _apply_queued(link, decision, state=RoutingState.QUEUED, queue_reason=queue_reason)
        return AllocationResult(link, decision, DecisionOutcome.QUEUED, None, queue_reason)


def transfer_unit(issue, to_unit, *, actor=None, source=ResponsibilitySource.INTERNAL_API, reason="") -> TransferResult:
    """
    @description Move responsibility to another area (RFC §6.8): record the
    event, drop the executor if they do not belong to the new area, then apply
    the new area's policy as if the item had just arrived.
    @raises UnitNotCoveringProject: The new area does not cover the project (I2).
    """
    if not unit_covers_project(to_unit, issue.project_id):
        raise UnitNotCoveringProject(unit_id=str(to_unit.id), project_id=str(issue.project_id))

    with transaction.atomic():
        link = _locked_link(issue)
        from_unit = link.organizational_unit
        if from_unit.id == to_unit.id:
            raise InvalidTransition("work item already belongs to that unit", unit_id=str(to_unit.id))

        event = IssueResponsibilityEvent.objects.create(
            issue_id=link.issue_id,
            workspace_id=link.workspace_id,
            from_unit=from_unit,
            to_unit=to_unit,
            actor=actor,
            source=source,
            reason=reason or "",
        )

        link.organizational_unit = to_unit
        link.save(update_fields=["organizational_unit", "updated_at"])

        executor_id = link.primary_executor_id
        keeps_executor = (
            executor_id is not None
            and OrganizationalUnitMembership.objects.filter(
                organizational_unit=to_unit,
                workspace_member__member_id=executor_id,
                is_active=True,
                workspace_member__is_active=True,
            ).exists()
        )

        if executor_id is not None and not keeps_executor:
            # Back to the queue under the new area, with the old executor kept
            # as a collaborator so the item does not silently lose its history.
            allocation = return_to_queue(issue, actor=actor, reason=reason, queue_reason=QueueReason.MANUALLY_RETURNED)
            return TransferResult(event=event, allocation=allocation)

        if executor_id is not None:
            return TransferResult(event=event, allocation=None)

        allocation = allocate(issue, to_unit, actor=actor, trigger=DecisionTrigger.INTERNAL_API)
        return TransferResult(event=event, allocation=allocation)


def set_responsibility(
    issue,
    unit,
    *,
    actor=None,
    source=ResponsibilitySource.INTERNAL_API,
    requested_mode: Optional[str] = None,
    explicit_executor=None,
    exclude_user_ids: Iterable = (),
    reason="",
    assignment_due_at=None,
) -> AllocationResult:
    """
    @description The "mark this area responsible" path. Creates the link on
    first use (event with ``from_unit=None``), delegates to ``transfer_unit``
    when another area already owns the item, and then allocates under the
    area's policy.
    @param exclude_user_ids: People the ranking must not choose, for a caller
        adding somebody alongside whoever is already on the item.
    @raises UnitNotCoveringProject: The area does not cover the project (I2).
    """
    if not unit_covers_project(unit, issue.project_id):
        raise UnitNotCoveringProject(unit_id=str(unit.id), project_id=str(issue.project_id))

    existing = IssueOrganizationalUnit.objects.filter(issue=issue).first()
    if existing is not None and existing.organizational_unit_id != unit.id:
        transfer = transfer_unit(issue, unit, actor=actor, source=source, reason=reason)
        if transfer.allocation is not None:
            return transfer.allocation
        # The executor belongs to the new area too, so the transfer left the
        # item assigned and there was nothing to allocate.
        link = IssueOrganizationalUnit.objects.get(pk=existing.pk)
        return AllocationResult(
            link, link.current_assignment_decision, DecisionOutcome.ASSIGNED, link.primary_executor_id
        )

    if existing is None:
        with transaction.atomic():
            link = IssueOrganizationalUnit.objects.create(
                issue=issue, organizational_unit=unit, project_id=issue.project_id, workspace_id=issue.workspace_id
            )
            IssueResponsibilityEvent.objects.create(
                issue_id=link.issue_id,
                workspace_id=link.workspace_id,
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
        exclude_user_ids=exclude_user_ids,
        actor=actor,
        trigger=DecisionTrigger.INTERNAL_API,
        assignment_due_at=assignment_due_at,
    )
