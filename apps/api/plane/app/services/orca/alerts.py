# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who hears that an area's work is stuck, and how they hear it.

The queue can hold an item nobody is on for two different reasons, and both
are silent by default. ``allocation_failed`` means the allocator ran and found
nobody eligible: the caller — often a robot through the automation API —
believes it handed the work over, and the work is sitting there with no owner.
An item past its ``assignment_due_at`` (RFC §6.6) means a human was supposed
to pick it up and did not. Neither state raises anything on its own, so an
area only learns about it if somebody happens to open the queue.

**Who is told, and why in this order.** Coordination, not membership, is the
tie that makes somebody answerable for an area's work (RFC §5.2), so the
active coordinators are the recipients. An area with no coordinator yet still
has to be reachable, and the closest thing to an owner it has is its lead — so
the lead is the fallback, not an additional recipient: alerting both would
mean that appointing a coordinator quietly leaves the lead on the list.
An area with neither is a configuration gap, and it is logged rather than
silently swallowed, because the alternative is work that never reaches anybody.

**Why a native ``Notification`` and nothing else.** Plane already renders the
in-app inbox, and the fork's rule is to use the core surfaces rather than grow
a parallel one (FORK.md). ``sender`` is namespaced ``in_app:orca:<kind>`` so
these rows are filterable and distinguishable from core activity, and
``triggered_by`` is null on purpose: nobody did this, a deadline passed or an
allocator failed.

Nothing here talks to a broker or opens a transaction. The two callers — the
sweep (``bgtasks/organizational_queue_task.py``) and the immediate hook in
``assignment_service._apply_queued`` — decide when; this module only decides
who and what.
"""

# Python imports
import logging

# Module imports
from plane.db.models import (
    Notification,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMemberRole,
    OrganizationalUnitMembership,
)

logger = logging.getLogger("plane.orca.alerts")

# The two things an area can be told. Part of ``sender``, so they are values a
# client filters on: renaming one is a breaking change, not a copy edit.
KIND_ALLOCATION_FAILED = "allocation_failed"
KIND_ASSIGNMENT_OVERDUE = "assignment_overdue"

# Plain English, because the in-app inbox renders whatever it is given and the
# locale files for these strings are item 2.5. Everything a client would
# branch on lives in ``data`` instead, so translating the title later changes
# no behaviour.
_TITLES = {
    KIND_ALLOCATION_FAILED: "Nobody could be assigned to this work item",
    KIND_ASSIGNMENT_OVERDUE: "This work item passed its assignment deadline",
}

_MESSAGES = {
    KIND_ALLOCATION_FAILED: (
        "The area is responsible for this work item, and the assignment could not find anybody eligible to do it."
    ),
    KIND_ASSIGNMENT_OVERDUE: (
        "The area is responsible for this work item, and its assignment deadline passed with nobody on it."
    ),
}


def recipients_for(unit) -> list:
    """
    @description Who answers for this area's stuck work: the active
    coordinators, or the lead when the area has no coordinator yet.
    @param unit: The ``OrganizationalUnit`` whose work is stuck.
    @returns A list of ``User`` ids, possibly empty. Empty is a real answer —
        an area with no coordinator and no lead — and is logged here so the
        callers do not each have to.
    """
    coordinator_ids = [
        user_id
        for user_id in OrganizationalUnitCoordinator.objects.filter(
            organizational_unit=unit,
            is_active=True,
            workspace_member__is_active=True,
        )
        .values_list("workspace_member__member_id", flat=True)
        .distinct()
        if user_id is not None
    ]
    if coordinator_ids:
        return coordinator_ids

    # ``role="lead"`` is unique per active membership by constraint, so this is
    # at most one person; kept as a list because the caller does not care which
    # of the two rules produced the recipients.
    lead_ids = [
        user_id
        for user_id in OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit,
            role=OrganizationalUnitMemberRole.LEAD,
            is_active=True,
            workspace_member__is_active=True,
        )
        .values_list("workspace_member__member_id", flat=True)
        .distinct()
        if user_id is not None
    ]
    if lead_ids:
        return lead_ids

    # Ids only, never a name or an e-mail (RFC §11).
    logger.warning(
        "Orca alert has nobody to reach: area has no active coordinator and no active lead.",
        extra={"unit_id": str(unit.id), "workspace_id": str(unit.workspace_id)},
    )
    return []


def notify(link, kind: str) -> int:
    """
    @description Tell whoever answers for the area that this item is stuck.
    @param link: The ``IssueOrganizationalUnit`` row. Read, never written —
        ``last_alerted_at`` belongs to the sweep that owns the de-duplication
        window, not to the act of notifying.
    @param kind: ``KIND_ALLOCATION_FAILED`` or ``KIND_ASSIGNMENT_OVERDUE``.
    @returns How many notifications were written; ``0`` when the area has
        nobody to tell.
    """
    unit = link.organizational_unit
    receiver_ids = recipients_for(unit)
    if not receiver_ids:
        return 0

    payload = _payload(link, kind)
    title = _TITLES.get(kind, _TITLES[KIND_ASSIGNMENT_OVERDUE])
    message = _MESSAGES.get(kind, _MESSAGES[KIND_ASSIGNMENT_OVERDUE])

    # bulk_create, as ``notification_task`` does: one statement for an area
    # with several coordinators, and no per-row ``save()`` to audit — nobody
    # triggered these, so there is no current user to stamp.
    Notification.objects.bulk_create(
        [
            Notification(
                workspace_id=link.workspace_id,
                project_id=link.project_id,
                sender=f"in_app:orca:{kind}",
                triggered_by=None,
                receiver_id=receiver_id,
                entity_identifier=link.issue_id,
                entity_name="issue",
                title=title,
                message=message,
                data=payload,
            )
            for receiver_id in receiver_ids
        ],
        batch_size=100,
    )

    logger.info(
        "Orca alert written.",
        extra={
            "metric": "orca.queue.alert",
            "kind": kind,
            "unit_id": str(unit.id),
            "workspace_id": str(link.workspace_id),
            "issue_id": str(link.issue_id),
            "recipients": len(receiver_ids),
        },
    )
    return len(receiver_ids)


def _payload(link, kind: str) -> dict:
    """
    @description What a client needs to render the alert and act on it, with
    the same shape ``notification_task`` uses for the ``issue`` block so the
    existing inbox card has the fields it already reads.
    """
    issue = link.issue
    return {
        "kind": kind,
        "issue": {
            "id": str(link.issue_id),
            "name": str(issue.name),
            "identifier": str(issue.project.identifier),
            "sequence_id": issue.sequence_id,
            "state_name": str(issue.state.name) if issue.state_id else None,
            "state_group": str(issue.state.group) if issue.state_id else None,
        },
        "organizational_unit": {
            "id": str(link.organizational_unit_id),
            "name": str(link.organizational_unit.name),
        },
        "routing_state": str(link.routing_state),
        "queue_reason": str(link.queue_reason or ""),
        "assignment_due_at": (link.assignment_due_at.isoformat() if link.assignment_due_at else None),
        "queued_at": link.queued_at.isoformat() if link.queued_at else None,
    }


__all__ = [
    "KIND_ALLOCATION_FAILED",
    "KIND_ASSIGNMENT_OVERDUE",
    "notify",
    "recipients_for",
]
