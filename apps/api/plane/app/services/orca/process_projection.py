# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Projecting an external process into Plane, and closing a step of one.

The orchestrator owns the process: the template, the branching, the schedule,
which step comes next (F12, RFC §7.2). What it cannot own is what a person
sees inside Plane, and that is what this module writes — the instance, the
step, the completion rule — so that four work items read as four steps of one
onboarding rather than four unrelated items.

Two operations, and they fail differently:

* **projecting** happens inside the create-work-item transaction and is
  ``get_or_create`` all the way down, because the orchestrator retries and a
  retry must not produce a second instance;
* **completing** is a claim from outside that a step is finished. It is
  refused outright for a step whose mode is ``manual``, held for a person when
  the mode asks for review, and recorded either way — a claim that changed
  nothing is still a claim somebody made.
"""

# Python imports
import logging

# Django imports
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.db.models import (
    CompletionMode,
    IssueServiceLevel,
    Label,
    ProcessCompletionEvent,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ProcessInstanceStatus,
    State,
    StateGroup,
)

from .errors import CompletionManualOnly, ProcessProjectionDisabled
from .feature_flags import orca_process_projection_enabled

logger = logging.getLogger("plane.orca.process")

# The label an item gets when its step completed but wants a person's eye and
# the area named no review state. A label rather than nothing, because "done"
# and "somebody says it is done" have to be distinguishable on a board.
REVIEW_LABEL = "aguardando-validacao"
REVIEW_LABEL_COLOR = "#f59e0b"


def project_process(issue, block, *, workspace_id) -> ProcessInstanceItem:
    """
    @description Record that this work item is a step of a process instance
    (RFC §7.2). Called inside the creation transaction, and idempotent: the
    orchestrator retries, and a retry must find the same instance and the same
    step rather than making a second one.
    @param issue: The work item being created.
    @param block: The request's ``process`` block, already validated.
    @param workspace_id: The workspace, passed rather than read off the issue
        so the caller's transaction does not need another query.
    @returns The ``ProcessInstanceItem`` for this step.
    @raises ProcessProjectionDisabled: The instance has the projection off.
    """
    if not orca_process_projection_enabled():
        raise ProcessProjectionDisabled()

    instance, created = ProcessInstanceReference.objects.get_or_create(
        workspace_id=workspace_id,
        external_source=block["source"],
        external_instance_id=block["instance_id"],
        defaults={
            "template_name": block.get("template_name", ""),
            "template_version": block["template_version"],
            "started_at": timezone.now(),
        },
    )
    # A template that changed between two steps of the same run is worth
    # noticing, and worth not overwriting: the instance keeps the version it
    # started under, because that is the one it actually ran.
    if not created and instance.template_version != block["template_version"]:
        logger.info(
            "orca process step arrived under a different template version",
            extra={
                "workspace_id": str(workspace_id),
                "instance": f"{instance.external_source}:{instance.external_instance_id}",
                "instance_version": instance.template_version,
                "step_version": block["template_version"],
            },
        )

    item, _ = ProcessInstanceItem.objects.get_or_create(
        issue=issue,
        defaults={
            "process_instance": instance,
            "workspace_id": workspace_id,
            "step_key": block["step_key"],
            "completion_mode": block.get("completion_mode", CompletionMode.MANUAL),
        },
    )
    return item


def record_service_level(
    issue, *, assignment_due_at=None, completion_due_at=None, source, source_version="", actor=None, reason=""
):
    """
    @description Write the deadlines this item is held to, keeping the ones it
    started with (RFC §5.2). Called by the assignment service whenever a
    deadline is set, so the record exists whether the deadline came from an
    area's policy or from a process template.
    @param issue: The work item.
    @param assignment_due_at: By when somebody must be on it.
    @param completion_due_at: By when it must be done.
    @param source: A ``ServiceLevelSource`` value.
    @param source_version: Policy version or template version.
    @param actor: Who changed it, when a person did.
    @param reason: Why.
    @returns The ``IssueServiceLevel`` row.
    """
    level, created = IssueServiceLevel.objects.get_or_create(
        issue=issue,
        defaults={
            "workspace_id": issue.workspace_id,
            "assignment_due_at": assignment_due_at,
            "completion_due_at": completion_due_at,
            "source": source,
            "source_version": source_version or "",
            "changed_by": actor,
            "change_reason": reason or "",
        },
    )
    if created:
        return level

    level.assignment_due_at = assignment_due_at
    level.completion_due_at = completion_due_at
    level.source = source
    level.source_version = source_version or ""
    level.changed_by = actor
    level.change_reason = reason or ""
    # ``original_*`` is restored by the model's own save; passing them here
    # would be a caller trying to rewrite what was first promised.
    level.save()
    return level


def _completed_state(issue, policy):
    """
    @description Where a completed step lands: the area's configured state
    when it has one, otherwise the project's first completed state by
    sequence. ``None`` when the project has no completed state at all, which
    is a project nobody can finish work in and not this module's problem to
    invent one for.
    @returns A ``State`` or ``None``.
    """
    if policy is not None and policy.completed_state_id:
        return policy.completed_state
    return (
        State.objects.filter(project_id=issue.project_id, group=StateGroup.COMPLETED.value).order_by("sequence").first()
    )


def _review_state(issue, policy):
    """@description The state a step awaiting review lands in, if the area named one. @returns State or None."""
    if policy is not None and policy.review_state_id:
        return policy.review_state
    return None


def _apply_review_label(issue):
    """
    @description Mark an item as claimed-done-but-unreviewed with a label,
    created in the project on demand. The fallback for an area that named no
    review state: without it, ``automatic_with_review`` would be
    indistinguishable from nothing having happened.
    @param issue: The work item.
    @returns The label applied.
    """
    from plane.db.models import IssueLabel

    label, _ = Label.objects.get_or_create(
        project_id=issue.project_id,
        name=REVIEW_LABEL,
        defaults={"workspace_id": issue.workspace_id, "color": REVIEW_LABEL_COLOR},
    )
    IssueLabel.objects.get_or_create(
        issue=issue, label=label, defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id}
    )
    return label


def complete_step(issue, *, evidence=None, rule_version="", source="", event_id="", actor=None, policy=None):
    """
    @description Act on a claim that this step is finished (RFC §7.2).

    The step's ``completion_mode`` decides what happens: ``automatic`` moves
    the item to a completed state; ``automatic_with_review`` moves it to the
    area's review state, or labels it when there is none, and leaves it open;
    ``manual`` refuses, because some steps are only ever finished by the person
    doing them and a robot saying otherwise turns a checklist into a lie.

    Every claim is recorded, including the ones that changed nothing: "who said
    this was done, and on what evidence?" is asked afterwards.
    @param issue: The work item that carries the step.
    @param evidence: Whatever the caller sent to justify the claim; stored verbatim.
    @param rule_version: Which version of the caller's rule decided it.
    @param source, event_id: The caller and its id for this claim, so a replay
        is visible in the log rather than looking like a second claim.
    @param actor: The user behind the API token, for the audit trail.
    @param policy: The resolved area policy, for the configured states.
    @returns ``(ProcessCompletionEvent, ProcessInstanceItem)``.
    @raises ProcessProjectionDisabled: The instance has the projection off.
    @raises CompletionManualOnly: The step is finished by a person only.
    """
    if not orca_process_projection_enabled():
        raise ProcessProjectionDisabled()

    item = ProcessInstanceItem.objects.filter(issue=issue).select_related("process_instance").first()
    mode = item.completion_mode if item else CompletionMode.MANUAL

    if mode == CompletionMode.MANUAL:
        # Recorded before refusing: a claim somebody's robot keeps making
        # against a manual step is a fact worth being able to see.
        ProcessCompletionEvent.objects.create(
            issue=issue,
            workspace_id=issue.workspace_id,
            source=source or "",
            event_id=event_id or "",
            rule_version=rule_version or "",
            evidence=evidence or {},
            mode=mode,
            applied=False,
            created_by=actor,
        )
        raise CompletionManualOnly(issue_id=str(issue.id))

    with transaction.atomic():
        applied = False
        if mode == CompletionMode.AUTOMATIC:
            state = _completed_state(issue, policy)
            if state is not None:
                issue.state = state
                issue.completed_at = timezone.now()
                issue.save(update_fields=["state", "completed_at", "updated_at"])
                applied = True
        else:
            state = _review_state(issue, policy)
            if state is not None:
                issue.state = state
                issue.save(update_fields=["state", "updated_at"])
            else:
                _apply_review_label(issue)
            applied = True

        event = ProcessCompletionEvent.objects.create(
            issue=issue,
            workspace_id=issue.workspace_id,
            source=source or "",
            event_id=event_id or "",
            rule_version=rule_version or "",
            evidence=evidence or {},
            mode=mode,
            applied=applied,
            created_by=actor,
        )
        if item is not None:
            _close_instance_if_done(item.process_instance)
    return event, item


def _close_instance_if_done(instance) -> bool:
    """
    @description Mark the run finished when every one of its steps is in a
    completed or cancelled state (RFC §7.2). Read from the work items' own
    states rather than from the completion events, because a person closing a
    step in the interface finishes it just as truly as the orchestrator does.
    @param instance: The ``ProcessInstanceReference``.
    @returns Whether the instance was closed by this call.
    """
    if instance is None or instance.completed_at is not None:
        return False

    states = list(
        ProcessInstanceItem.objects.filter(process_instance=instance).values_list("issue__state__group", flat=True)
    )
    if not states or any(group not in (StateGroup.COMPLETED.value, StateGroup.CANCELLED.value) for group in states):
        return False

    instance.status = ProcessInstanceStatus.COMPLETED
    instance.completed_at = timezone.now()
    instance.save(update_fields=["status", "completed_at", "updated_at"])
    return True


def instance_payload(instance) -> dict:
    """
    @description One process instance as a client reads it: its steps with the
    native state, the routing state, the executor and the deadlines, plus a
    ``status`` derived from the items rather than trusted from the column —
    the column is a cache, and the items are the truth.
    @param instance: The ``ProcessInstanceReference``.
    @returns A JSON-serializable dict.
    """
    items = (
        ProcessInstanceItem.objects.filter(process_instance=instance)
        .select_related("issue__state", "issue__project", "issue__orca_service_level")
        .prefetch_related("issue__organizational_unit_links__organizational_unit")
        .order_by("created_at")
    )

    steps = []
    finished = True
    for item in items:
        issue = item.issue
        link = next(iter(issue.organizational_unit_links.all()), None)
        level = getattr(issue, "orca_service_level", None)
        group = issue.state.group if issue.state_id else None
        if group not in (StateGroup.COMPLETED.value, StateGroup.CANCELLED.value):
            finished = False
        steps.append(
            {
                "step_key": item.step_key,
                "completion_mode": item.completion_mode,
                "issue_id": str(issue.id),
                "sequence_id": issue.sequence_id,
                "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
                "name": issue.name,
                "state": {"name": issue.state.name, "group": group} if issue.state_id else None,
                "routing_state": link.routing_state if link else None,
                "unit": link.organizational_unit.slug if link else None,
                "primary_executor": str(link.primary_executor_id) if link and link.primary_executor_id else None,
                "assignment_due_at": (link.assignment_due_at.isoformat() if link and link.assignment_due_at else None),
                "completion_due_at": (
                    level.completion_due_at.isoformat() if level and level.completion_due_at else None
                ),
            }
        )

    return {
        "source": instance.external_source,
        "instance_id": instance.external_instance_id,
        "template": {"name": instance.template_name, "version": instance.template_version},
        "status": ProcessInstanceStatus.COMPLETED if steps and finished else instance.status,
        "started_at": instance.started_at.isoformat() if instance.started_at else None,
        "completed_at": instance.completed_at.isoformat() if instance.completed_at else None,
        "steps": steps,
    }
