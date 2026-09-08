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
from typing import Iterable
from uuid import UUID

from django.utils import timezone

from plane.db.models import (
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMemberRole,
    OrganizationalUnitMembership,
)

logger = logging.getLogger("plane.worker")

ALERT_ALLOCATION_FAILED = "allocation_failed"
ALERT_ASSIGNMENT_SLA = "assignment_sla"


def recipients_for(unit) -> list:
    """
    User ids who should hear about this area's queue.

    @description Coordinators first, because that is the role the queue is
    for. The lead is a fallback for areas that have not named a coordinator
    yet (the Gate 2-minimum pilot may be in that state). An empty list is a
    valid answer: it means "do not invent a recipient".
    @param unit: The ``OrganizationalUnit``.
    @returns Distinct user ids, coordinators first, otherwise the lead.
    """
    coordinator_ids = list(
        OrganizationalUnitCoordinator.objects.filter(organizational_unit=unit, is_active=True).values_list(
            "workspace_member__member_id", flat=True
        )
    )
    if coordinator_ids:
        return list(dict.fromkeys(coordinator_ids))

    lead = (
        OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit,
            is_active=True,
            role=OrganizationalUnitMemberRole.LEAD,
        )
        .values_list("workspace_member__member_id", flat=True)
        .first()
    )
    if lead:
        return [lead]

    logger.info("Organizational unit %s has no coordinator and no lead; skipping the alert.", unit.id)
    return []


def notify(link: IssueOrganizationalUnit, kind: str) -> int:
    """
    Write one in-app notification per recipient for this queued item.

    @description The ``data`` shape matches ``notification_task.py`` so the
    existing notification UI can render the issue without a second fetch.
    ``triggered_by`` is null: these alerts are the system's, not a person's.
    @param link: The item-to-area link that is waiting.
    @param kind: ``allocation_failed`` or ``assignment_sla``.
    @returns How many notifications were created.
    """
    issue = link.issue
    project = link.project
    unit = link.organizational_unit
    identifier = f"{project.identifier}-{issue.sequence_id}"
    if kind == ALERT_ALLOCATION_FAILED:
        title = f"Allocation failed: nobody in {unit.name} could take {identifier}"
    else:
        title = f"Assignment overdue: {identifier} has been waiting in {unit.name} past its deadline"

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
            data={
                "issue": {
                    "id": str(issue.id),
                    "name": issue.name,
                    "identifier": project.identifier,
                    "sequence_id": issue.sequence_id,
                },
                "organizational_unit": {
                    "id": str(unit.id),
                    "name": unit.name,
                    "slug": unit.slug,
                },
                "routing_state": link.routing_state,
                "queue_reason": link.queue_reason,
            },
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
