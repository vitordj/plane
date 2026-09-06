# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Telling somebody that an area's queue needs a person (item 2.4).

Two moments deserve an alert, and they are different in kind. One is
immediate: an allocation that ends in ``allocation_failed`` is the machine
saying "I looked and found nobody", and the coordinator wants to know now
rather than at the next sweep. The other is a breach: an item that has sat in
the queue past its assignment deadline, which no single event marks — nothing
happens at the moment a deadline passes, so it has to be looked for.

Who hears about it: the area's coordinators, and the lead when there are no
coordinators. Not the area's members — a queue that nobody has picked up is a
coordination problem, and alerting twelve people about one item is how a team
learns to ignore notifications.

The alerts are native ``Notification`` rows, the same ones Plane already shows
in the inbox, rather than a channel of the fork's own. ``entity_name`` stays
``issue`` and ``entity_identifier`` the work item, so clicking the alert opens
the item the way every other notification does; ``sender`` is what marks it as
ours.
"""

# Python imports
import logging
from datetime import timedelta

# Django imports
from django.utils import timezone

# Module imports
from plane.db.models import (
    Notification,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMemberRole,
    OrganizationalUnitMembership,
)

logger = logging.getLogger("plane.orca.alerts")

# How long an unresolved breach stays quiet before it is mentioned again. Four
# hours is the plan's number (item 2.4): long enough that an item nobody can
# place does not alert every fifteen minutes, short enough that a breach in the
# morning is still mentioned in the afternoon.
ALERT_QUIET_PERIOD = timedelta(hours=4)

# What marks these notifications as the queue's. Two senders rather than one so
# a reader (and a future email digest) can tell "nobody could take this" from
# "this has been waiting too long" without parsing the title.
SENDER_ALLOCATION_FAILED = "in_app:orca_queue:allocation_failed"
SENDER_ASSIGNMENT_OVERDUE = "in_app:orca_queue:assignment_overdue"
# A third, for work the availability sweep put back (item 3.4). Its own sender
# because the coordinator's next move is different: this item had somebody, and
# what it needs is a new one rather than a first one.
SENDER_WORK_RETURNED = "in_app:orca_queue:work_returned"


def alert_recipients(unit) -> list:
    """
    @description Who to tell about this area's queue: its active coordinators,
    or its lead when it has none. A lead is the fallback rather than a second
    audience — an area with a coordinator has somebody whose job this is, and
    telling the lead as well makes the alert everybody's and nobody's.
    @param unit: The ``OrganizationalUnit``.
    @returns A list of user ids, possibly empty.
    """
    coordinators = list(
        OrganizationalUnitCoordinator.objects.filter(
            organizational_unit=unit, is_active=True, workspace_member__is_active=True
        ).values_list("workspace_member__member_id", flat=True)
    )
    if coordinators:
        return coordinators

    return list(
        OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit,
            role=OrganizationalUnitMemberRole.LEAD,
            is_active=True,
            workspace_member__is_active=True,
        ).values_list("workspace_member__member_id", flat=True)
    )


def _payload(link, issue) -> dict:
    """
    @description What the alert carries about the item, in the shape Plane's
    own notification list already reads. Ids and the item's own fields only —
    no names of people, since the recipient list is the audience and the
    payload is stored.
    @returns A JSON-serializable dict.
    """
    return {
        "issue": {
            "id": str(issue.id),
            "name": str(issue.name),
            "identifier": str(issue.project.identifier),
            "sequence_id": issue.sequence_id,
            "state_name": issue.state.name if issue.state_id else None,
            "state_group": issue.state.group if issue.state_id else None,
        },
        "orca": {
            "unit_id": str(link.organizational_unit_id),
            "unit_slug": link.organizational_unit.slug,
            "routing_state": link.routing_state,
            "queue_reason": link.queue_reason,
            "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
        },
    }


def _notify(link, *, sender, title, triggered_by=None) -> int:
    """
    @description Write one alert per recipient.
    @param link: The ``IssueOrganizationalUnit`` the alert is about, with its
        issue and unit already selected.
    @param sender: One of the module's ``SENDER_*`` values.
    @param title: The line shown in the notification list.
    @param triggered_by: The person whose action caused it, when there was one;
        ``None`` for the system.
    @returns How many notifications were written.
    """
    unit = link.organizational_unit
    recipients = alert_recipients(unit)
    if not recipients:
        logger.info(
            "orca queue alert has nobody to go to",
            extra={"workspace_id": str(link.workspace_id), "unit_id": str(unit.id), "issue_id": str(link.issue_id)},
        )
        return 0

    issue = link.issue
    data = _payload(link, issue)
    Notification.objects.bulk_create(
        [
            Notification(
                workspace_id=link.workspace_id,
                project_id=link.project_id,
                sender=sender,
                triggered_by=triggered_by,
                receiver_id=recipient,
                entity_identifier=link.issue_id,
                entity_name="issue",
                title=title,
                data=data,
            )
            for recipient in recipients
        ],
        batch_size=50,
    )
    return len(recipients)


def alert_allocation_failed(link, *, actor=None) -> int:
    """
    @description Tell the area, now, that an allocation found nobody (item
    2.4). Called from the service the moment the decision is written, because
    the coordinator's next action — widen the area, add somebody to the
    project, take it themselves — is one they would rather do immediately than
    at the next sweep.
    @param link: The ``IssueOrganizationalUnit`` that ended in
        ``allocation_failed``.
    @param actor: Whoever asked for the allocation, when a person did.
    @returns How many notifications were written.
    """
    return _notify(
        link,
        sender=SENDER_ALLOCATION_FAILED,
        title="Nobody in this area could take a work item",
        triggered_by=actor,
    )


def alert_assignment_overdue(link) -> int:
    """
    @description Tell the area that an item has waited past its assignment
    deadline. Written by the sweep, so it carries no actor: nobody did this,
    which is the point.
    @param link: The overdue ``IssueOrganizationalUnit``.
    @returns How many notifications were written.
    """
    return _notify(
        link,
        sender=SENDER_ASSIGNMENT_OVERDUE,
        title="A work item has been waiting for someone to take it",
    )


def may_alert_again(link, *, now=None) -> bool:
    """
    @description Whether this item's breach may be mentioned again, i.e. it has
    not been alerted about in the last four hours.
    @param link: The ``IssueOrganizationalUnit``.
    @param now: The instant to judge against, so one sweep judges every row
        against the same moment.
    @returns bool.
    """
    if link.last_alerted_at is None:
        return True
    return (now or timezone.now()) - link.last_alerted_at >= ALERT_QUIET_PERIOD


def alert_work_returned(link, *, reason="") -> int:
    """
    @description Tell the area that work came back because the person holding
    it is no longer there (RFC §6.9). Nobody clicked, so without this the item
    simply reappears in the queue with no explanation — and the explanation is
    the part a coordinator needs to act.
    @param link: The ``IssueOrganizationalUnit`` that was returned.
    @param reason: Why, in the sweep's vocabulary (``away``,
        ``left_the_area``, ``left_the_workspace``, ``lost_project_access``).
    @returns How many notifications were written.
    """
    title = "A work item came back to the queue: its executor is unavailable"
    if reason:
        title = f"A work item came back to the queue ({reason})"
    return _notify(link, sender=SENDER_WORK_RETURNED, title=title)
