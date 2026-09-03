# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Telling somebody when work has been waiting too long.

An area's queue is only a promise if somebody notices when it is broken. This
sweep finds work that nobody took by the time the area said it would, and
tells the people whose job that is — the coordinators, or the lead when the
area has none.

Two restraints matter more than the alert itself. It notifies the same work
item at most once every four hours, because a fifteen-minute sweep would
otherwise produce ninety-six notifications a day for one late item and teach
everybody to ignore all of them. And with the layer switched off it does
nothing at all: an operator who turned the feature off should not keep getting
its mail.
"""

# Python imports
import logging
from datetime import timedelta

# Django imports
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

# Third party imports
from celery import shared_task

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")

# How long before the same work item may be complained about again.
ALERT_COOLDOWN = timedelta(hours=4)

NOTIFICATION_SENDER = "orca.queue.assignment_overdue"


def not_alerted_since(cutoff):
    """@description Never complained about, or not since the cooldown began."""
    return Q(last_alerted_at__isnull=True) | Q(last_alerted_at__lt=cutoff)


def notify_overdue(link, now=None):
    """
    Tell whoever runs this area that a work item is late being taken.

    @description Coordinators first; the area's lead only when there is no
    coordinator, because a lead who is not running the queue should not be the
    default recipient of its problems. Returns how many notifications were
    written, so the sweep can report something meaningful.
    @param link: The ``IssueOrganizationalUnit`` that is overdue.
    @returns: Number of notifications created.
    """
    from plane.db.models import Notification, OrganizationalUnitCoordinator, OrganizationalUnitMembership

    now = now or timezone.now()
    unit = link.organizational_unit

    receivers = list(
        OrganizationalUnitCoordinator.objects.filter(
            organizational_unit=unit, is_active=True, workspace_member__is_active=True
        ).values_list("workspace_member__member_id", flat=True)
    )
    if not receivers:
        receivers = list(
            OrganizationalUnitMembership.objects.filter(
                organizational_unit=unit,
                is_active=True,
                role="lead",
                workspace_member__is_active=True,
            ).values_list("workspace_member__member_id", flat=True)
        )
    if not receivers:
        # Nobody to tell. Worth a log line: an area with work waiting and
        # nobody watching it is a configuration problem, not a quiet day.
        logger.warning(
            "orca.queue.nobody_to_alert",
            extra={"unit_id": str(unit.id), "issue_id": str(link.issue_id)},
        )
        return 0

    issue = link.issue
    title = f"{issue.name} has been waiting in {unit.name}"
    Notification.objects.bulk_create(
        [
            Notification(
                workspace_id=link.workspace_id,
                project_id=link.project_id,
                entity_identifier=link.issue_id,
                entity_name="issue",
                title=title,
                message={"reason": link.queue_reason, "unit": unit.name, "issue": issue.name},
                message_stripped=title,
                sender=NOTIFICATION_SENDER,
                receiver_id=receiver_id,
                data={
                    "issue": {"id": str(issue.id), "name": issue.name},
                    "organizational_unit": {"id": str(unit.id), "name": unit.name, "slug": unit.slug},
                    "queue_reason": link.queue_reason,
                    "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
                },
            )
            for receiver_id in receivers
        ],
        batch_size=100,
    )
    return len(receivers)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sweep_assignment_sla(self):
    """
    Find work nobody took in time, and say so.

    @description Runs on the beat schedule. One area failing must not stop the
    rest, so failures are logged per work item and the pass keeps going.
    @returns: A short summary, for the worker log.
    """
    from plane.db.models import IssueOrganizationalUnit
    from plane.db.models.organizational_unit import RoutingState

    if not getattr(settings, "ORCA_ORG_UNITS_ENABLED", True):
        # The layer is off. Its alerts are off with it.
        return {"skipped": "organizational layer disabled"}

    now = timezone.now()
    cutoff = now - ALERT_COOLDOWN

    overdue = (
        IssueOrganizationalUnit.objects.filter(
            routing_state__in=[RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED],
            assignment_due_at__lt=now,
        )
        .filter(not_alerted_since(cutoff))
        .select_related("issue", "organizational_unit")
    )

    alerted = 0
    for link in overdue.iterator(chunk_size=200):
        try:
            sent = notify_overdue(link, now=now)
            if sent:
                IssueOrganizationalUnit.objects.filter(pk=link.pk).update(last_alerted_at=now)
                alerted += 1
        except Exception as error:  # noqa: BLE001 - one bad row must not stop the sweep
            log_exception(error)

    logger.info("orca.queue.sla_sweep", extra={"alerted": alerted})
    return {"alerted": alerted}
