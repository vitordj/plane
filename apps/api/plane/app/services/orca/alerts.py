# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who hears that an area's work is stuck, and how that message is written.

Two events share this module: a least-loaded allocation that found nobody
(``allocation_failed``), and the assignment SLA passing while the item is
still waiting (the 15-minute sweep). Both write the native ``Notification``
row the rest of Plane already shows, rather than a sidecar inbox: the
coordinator is a person using the product, not a machine.

Recipients are the area's active coordinators. If the area has none, the
lead. If it has neither, nothing is written and the miss is logged — an
empty inbox is a configuration problem, not a reason to invent a
destination.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional
from uuid import UUID, uuid4

from django.utils import timezone

from plane.app.permissions.base import ROLE
from plane.db.models import (
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMemberRole,
    OrganizationalUnitMembership,
    RoutingState,
)

logger = logging.getLogger("plane.worker")

ALERT_ALLOCATION_FAILED = "allocation_failed"
ALERT_ASSIGNMENT_SLA = "assignment_sla"
ALERT_EXECUTOR_UNAVAILABLE = "executor_unavailable"


def recipients_for(unit) -> list:
    """
    User ids who should hear about this area's queue.

    @description Coordinators first, because that is the role the queue is
    for. The lead is a fallback for areas that have not named a coordinator
    yet (the Gate 2-minimum pilot may be in that state). Deactivated workspace
    members, inactive users, and coordinators demoted to Guest are excluded
    (R2.19). An empty list is a valid answer: it means "do not invent a
    recipient".
    @param unit: The ``OrganizationalUnit``.
    @returns Distinct user ids, coordinators first, otherwise the lead.
    """
    coordinator_ids = list(
        OrganizationalUnitCoordinator.objects.filter(
            organizational_unit=unit,
            is_active=True,
            workspace_member__is_active=True,
            workspace_member__member__is_active=True,
            workspace_member__role__gte=ROLE.MEMBER.value,
        ).values_list("workspace_member__member_id", flat=True)
    )
    if coordinator_ids:
        return list(dict.fromkeys(coordinator_ids))

    lead = (
        OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit,
            is_active=True,
            workspace_member__is_active=True,
            workspace_member__member__is_active=True,
            role=OrganizationalUnitMemberRole.LEAD,
        )
        .values_list("workspace_member__member_id", flat=True)
        .first()
    )
    if lead:
        return [lead]

    logger.info("Organizational unit %s has no coordinator and no lead; skipping the alert.", unit.id)
    return []


def build_notification_data(
    issue,
    project,
    *,
    field: str,
    verb: str,
    new_value: str = "",
    old_value: str = "",
    activity_id=None,
    actor_id=None,
    new_identifier=None,
    old_identifier=None,
    extra: Optional[dict] = None,
) -> dict:
    """
    Build the ``Notification.data`` payload Plane's notification inbox expects.

    @description Mirrors what ``plane/bgtasks/notification_task.py`` writes for
    a native activity: an ``issue`` block (``id``, ``name``, ``identifier``,
    ``sequence_id``, ``state_name``, ``state_group``) and an ``issue_activity``
    block (``id``, ``verb``, ``field``, ``actor``, ``new_value``, ``old_value``,
    ``issue_comment``, ``old_identifier``, ``new_identifier``).

    The web inbox (``workspace-notifications/sidebar/notification-card/item.tsx``
    and ``content.tsx``) dereferences ``data.issue_activity.field``,
    ``.new_value``, ``.old_value`` and ``.verb`` unguarded, and hides any card
    whose ``field`` is empty. A payload without ``issue_activity`` therefore
    crashes the whole ``/notifications`` page, which is why every notification
    the Orca layer writes by hand goes through this one builder. The card never
    reads ``Notification.title``; the sentence it renders is
    ``<triggered_by> <action> [to] <value>.`` from ``field``/``verb``/values, and
    it copes with a null ``triggered_by`` (it renders no name and no avatar).
    @param issue: The work item; ``issue.state`` is read (may be ``None``).
    @param project: The work item's project (for ``identifier``).
    @param field: The activity field the card keys its sentence on. Use
        ``assignees`` for assignments; ``queue_alert`` for area alerts (no
        branch in the card, so it renders ``<verb> queue alert to <new_value>``).
    @param verb: ``created``, ``updated`` or ``deleted`` (``deleted`` hides the
        value in the card).
    @param new_value: Text after the action in the card.
    @param old_value: Previous value (used by the card for removals).
    @param activity_id: Id of the ``IssueActivity`` row when there is one;
        otherwise a fresh uuid, because nothing dereferences it.
    @param actor_id: Who did it, or ``None`` for the system.
    @param extra: Extra top-level keys (e.g. the Orca routing context) merged
        beside ``issue`` and ``issue_activity``.
    @returns The ``data`` dict.
    """
    state = getattr(issue, "state", None)
    data = {
        "issue": {
            "id": str(issue.id),
            "name": str(issue.name),
            "identifier": str(project.identifier),
            "sequence_id": issue.sequence_id,
            "state_name": state.name if state is not None else None,
            "state_group": state.group if state is not None else None,
        },
        "issue_activity": {
            "id": str(activity_id or uuid4()),
            "verb": verb,
            "field": field,
            "actor": str(actor_id) if actor_id else None,
            "new_value": str(new_value),
            "old_value": str(old_value),
            "issue_comment": "",
            "old_identifier": str(old_identifier) if old_identifier else None,
            "new_identifier": str(new_identifier) if new_identifier else None,
        },
    }
    if extra:
        data.update(extra)
    return data


def notify(link: IssueOrganizationalUnit, kind: str) -> int:
    """
    Write one in-app notification per recipient for this queued item.

    @description The ``data`` shape comes from ``build_notification_data`` and
    matches ``notification_task.py``, so the existing notification UI renders
    the card without crashing (an alert without ``issue_activity`` used to
    break the whole inbox). ``triggered_by`` is null: these alerts are the
    system's, not a person's. The card ignores ``title``, so the alert text is
    also carried as the activity's ``new_value``. The Orca keys
    (``organizational_unit``, ``routing_state``, ``queue_reason``) stay beside
    the native ones.
    @param link: The item-to-area link that is waiting.
    @param kind: ``allocation_failed``, ``assignment_sla``, or
        ``executor_unavailable``.
    @returns How many notifications were created.
    """
    issue = link.issue
    project = link.project
    unit = link.organizational_unit
    identifier = f"{project.identifier}-{issue.sequence_id}"
    if kind == ALERT_ALLOCATION_FAILED:
        title = f"Allocation failed: nobody in {unit.name} could take {identifier}"
    elif kind == ALERT_EXECUTOR_UNAVAILABLE:
        title = f"Executor unavailable: {identifier} is waiting again in {unit.name}"
    else:
        title = f"Assignment overdue: {identifier} has been waiting in {unit.name} past its deadline"

    data = build_notification_data(
        issue,
        project,
        field="queue_alert",
        verb="updated",
        new_value=title,
        extra={
            "organizational_unit": {
                "id": str(unit.id),
                "name": unit.name,
                "slug": unit.slug,
            },
            "routing_state": link.routing_state,
            "queue_reason": link.queue_reason,
        },
    )

    created = 0
    for user_id in recipients_for(unit):
        Notification.objects.create(
            workspace=link.workspace,
            project=project,
            sender=f"in_app:orca:{kind}",
            triggered_by_id=None,
            receiver_id=user_id,
            entity_identifier=issue.id,
            entity_name="issue",
            title=title,
            data=data,
        )
        created += 1
    return created


def notify_allocation_failed_safely(link_id: UUID | str) -> None:
    """
    Alert the area that least-loaded found nobody, without ever failing the allocation.

    @description Called from ``transaction.on_commit`` after
    ``_apply_queued(..., allocation_failed)``. A broker or database hiccup
    here must not turn a recorded ``allocation_failed`` into a 500: the item
    is already waiting, and the sweep will notice the SLA if this alert is
    lost. Any exception is logged and swallowed.
    @param link_id: Primary key of the ``IssueOrganizationalUnit``.
    """
    try:
        link = IssueOrganizationalUnit.objects.select_related(
            "issue",
            "project",
            "organizational_unit",
            "workspace",
        ).get(pk=link_id)
        notify(link, ALERT_ALLOCATION_FAILED)
    except Exception:
        logger.exception("Failed to alert that allocation failed for link %s", link_id)


def notify_executor_unavailable_safely(link_id: UUID | str) -> None:
    """
    Tell the area's coordinators an item came back to the queue, without ever failing the caller.

    @description Used by the paths that return work because its executor is gone
    (the hourly drift sweep and workspace-member removal), so they raise the
    same ``executor_unavailable`` alert ``sweep_unavailable_executors`` raises.
    Run it from ``transaction.on_commit``; any exception is logged and swallowed.
    @param link_id: Primary key of the ``IssueOrganizationalUnit`` just returned.
    """
    try:
        link = IssueOrganizationalUnit.objects.select_related(
            "issue",
            "issue__state",
            "project",
            "organizational_unit",
            "workspace",
        ).get(pk=link_id)
        # Only an item that is still waiting is worth an alert; somebody may have
        # picked it up between the return and this callback.
        if link.routing_state in (RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED):
            notify(link, ALERT_EXECUTOR_UNAVAILABLE)
    except Exception:
        logger.exception("Failed to alert that the executor of link %s is unavailable", link_id)


def mark_alerted(link_ids: Iterable) -> None:
    """
    Record that the SLA sweep has told the area about these items.

    @description A write on ``IssueOrganizationalUnit`` is legitimate —
    that table is not append-only. ``last_alerted_at`` is what stops the
    next 15-minute tick from repeating the same notification.
    """
    ids = list(link_ids)
    if not ids:
        return
    IssueOrganizationalUnit.objects.filter(pk__in=ids).update(last_alerted_at=timezone.now())
