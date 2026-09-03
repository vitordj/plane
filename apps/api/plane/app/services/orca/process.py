# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Runs of a process, and steps closed by something other than a person.

The template is not here — it is a YAML file in the orchestrator's repository
(RFC F19). What is here is the projection: which work items make up one run,
which step each is, and how a step may be declared done.

Closing a step is the interesting part, and the design decision worth stating
is that "automatic" and "automatic with review" are per step, not per process.
Inside one onboarding, "the document was uploaded" is a fact a system can
assert and "the interview went well" is not, and a process that had to pick
one setting for both would be configured for the weaker of the two.
"""

# Django imports
from django.conf import settings
from django.utils import timezone

# Module imports
from plane.db.models import (
    CompletionMode,
    IssueLabel,
    Label,
    ProcessCompletionEvent,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ProcessInstanceStatus,
    State,
    StateGroup,
)

from .errors import InvalidTransition, ProcessProjectionDisabled

# The label a step gets when it is closed "with review" and the project has no
# review state to move it to. A label rather than nothing, because "somebody
# has to look at this" needs to be visible in a board somebody already reads.
REVIEW_LABEL_NAME = "aguardando-validacao"

# Groups that mean a step is no longer running. Cancelled counts: a run whose
# last step was cancelled is finished, not stuck.
CLOSED_GROUPS = [StateGroup.COMPLETED.value, StateGroup.CANCELLED.value]


def process_projection_enabled() -> bool:
    """@description Whether this instance projects process runs at all."""
    return bool(getattr(settings, "ORCA_PROCESS_PROJECTION_ENABLED", False))


def attach_to_process(issue, block):
    """
    Record that this work item is a step of a process run.

    @description ``get_or_create`` on both rows, so the same event arriving
    twice reconnects to the run rather than starting a second one. Called
    inside the create transaction, so a work item never exists as a step of
    nothing.
    @param issue: The work item.
    @param block: The request's ``process`` block.
    @returns: The ``ProcessInstanceItem``.
    @raises ProcessProjectionDisabled: The instance does not project processes.
    """
    if not process_projection_enabled():
        raise ProcessProjectionDisabled("process projection is not enabled on this instance")

    instance, _ = ProcessInstanceReference.objects.get_or_create(
        workspace_id=issue.workspace_id,
        external_source=block["source"],
        external_instance_id=block["instance_id"],
        defaults={
            "template_name": block.get("template_name", ""),
            "template_version": block["template_version"],
            "started_at": timezone.now(),
        },
    )

    item, created = ProcessInstanceItem.objects.get_or_create(
        issue=issue,
        defaults={
            "process_instance": instance,
            "workspace_id": issue.workspace_id,
            "step_key": block["step_key"],
            "completion_mode": block.get("completion_mode", CompletionMode.MANUAL),
        },
    )
    if not created and item.process_instance_id != instance.id:
        # One work item, two runs: whichever answer "is this done?" gave would
        # be wrong for the other. Refused rather than silently re-pointed.
        raise InvalidTransition(
            "this work item already belongs to another process run",
            issue_id=str(issue.id),
        )
    return item


def _completed_state(project, policy=None):
    """
    @description Where an automatically closed step lands: the area's named
    state when its policy has one, otherwise the first state in the project's
    completed group. Most projects have exactly one and never need the setting;
    the ones with three want to say which.
    @returns: A ``State``, or ``None`` when the project has no completed group.
    """
    named = getattr(policy, "completed_state", None) if policy else None
    if named is not None and named.project_id == project.id:
        return named
    return State.objects.filter(project=project, group=StateGroup.COMPLETED.value).order_by("sequence").first()


def _review_label(issue):
    """@description The review label for this project, created the first time it is needed."""
    label, _ = Label.objects.get_or_create(
        project_id=issue.project_id,
        name=REVIEW_LABEL_NAME,
        defaults={"workspace_id": issue.workspace_id, "color": "#F59E0B"},
    )
    return label


def complete_step(issue, *, source, event_id="", rule_version="", evidence=None, actor=None, policy=None):
    """
    Declare a step done, in the way its completion mode allows.

    @description Three outcomes, and only the first one closes anything:

    * ``automatic`` moves the work item to the project's completed state.
    * ``automatic_with_review`` leaves the state alone and marks it for a
      person — the area's review state when it has one, otherwise a label. It
      must not move to done: flagging for review and closing are the two
      things this mode exists to keep apart.
    * ``manual`` is refused. A step whose template says a person decides is not
      something an API key gets to decide instead.

    Every call writes a ``ProcessCompletionEvent`` carrying the evidence, even
    the ones that only flag for review. The first time somebody disputes an
    automatic closure, the answer has to be the event, not a recollection.
    @param issue: The step.
    @param source: The system asserting it.
    @param event_id: That system's id for the assertion.
    @param rule_version: Which version of the closing rule applied.
    @param evidence: Whatever justifies it.
    @param policy: The area's resolved policy, for its configured states.
    @returns: ``(ProcessCompletionEvent, ProcessInstanceItem)``.
    @raises ProcessProjectionDisabled, InvalidTransition
    """
    if not process_projection_enabled():
        raise ProcessProjectionDisabled("process projection is not enabled on this instance")

    item = ProcessInstanceItem.objects.select_related("process_instance", "issue__project").filter(issue=issue).first()
    if item is None:
        raise InvalidTransition("this work item is not a step of any process run", issue_id=str(issue.id))

    if item.completion_mode == CompletionMode.MANUAL:
        raise InvalidTransition(
            "this step is closed by a person, not by the system",
            issue_id=str(issue.id),
        )

    # The same assertion arriving twice must not close the step twice: the
    # first event is the record, the replay returns it.
    if event_id:
        existing = ProcessCompletionEvent.objects.filter(issue=issue, source=source, event_id=event_id).first()
        if existing is not None:
            return existing, item

    if item.completion_mode == CompletionMode.AUTOMATIC:
        state = _completed_state(issue.project, policy)
        if state is None:
            raise InvalidTransition(
                "this project has no completed state to move the step to",
                issue_id=str(issue.id),
            )
        issue.state = state
        # `completed_at` is not set here: Issue.save syncs it from the state's
        # group and adds it to update_fields itself, and writing both would be
        # two rules for one column.
        issue.save(update_fields=["state", "updated_at"])
    else:
        review_state = getattr(policy, "review_state", None) if policy else None
        if review_state is not None and review_state.project_id == issue.project_id:
            issue.state = review_state
            issue.save(update_fields=["state", "updated_at"])
        else:
            IssueLabel.objects.get_or_create(
                issue=issue,
                label=_review_label(issue),
                defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
            )

    event = ProcessCompletionEvent.objects.create(
        issue=issue,
        workspace_id=issue.workspace_id,
        source=source,
        event_id=event_id or "",
        rule_version=rule_version or "",
        evidence=evidence or {},
        mode=item.completion_mode,
        created_by=actor,
    )

    refresh_instance_status(item.process_instance)
    return event, item


def refresh_instance_status(instance):
    """
    @description Mark a run finished when none of its steps is still running.
    Derived rather than counted up as steps close, so a step reopened by hand
    in the app reopens the run too — the app is allowed to be right.
    @param instance: The ``ProcessInstanceReference``.
    @returns: The instance.
    """
    items = ProcessInstanceItem.objects.filter(process_instance=instance).select_related("issue__state")
    if not items:
        return instance

    all_closed = all(item.issue.state_id and item.issue.state.group in CLOSED_GROUPS for item in items)

    if all_closed and instance.status != ProcessInstanceStatus.COMPLETED:
        instance.status = ProcessInstanceStatus.COMPLETED
        instance.completed_at = timezone.now()
        instance.save(update_fields=["status", "completed_at", "updated_at"])
    elif not all_closed and instance.status == ProcessInstanceStatus.COMPLETED:
        instance.status = ProcessInstanceStatus.RUNNING
        instance.completed_at = None
        instance.save(update_fields=["status", "completed_at", "updated_at"])

    return instance


def instance_progress(instance):
    """
    @description How far along a run is, as the read endpoint and the queue's
    grouping both need it.
    @param instance: The ``ProcessInstanceReference``.
    @returns: ``{"done": int, "total": int}``.
    """
    items = ProcessInstanceItem.objects.filter(process_instance=instance).select_related("issue__state")
    total = len(items)
    done = sum(1 for item in items if item.issue.state_id and item.issue.state.group in CLOSED_GROUPS)
    return {"done": done, "total": total}
