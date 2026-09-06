# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The sweep that notices an area's queue has gone quiet for too long.

Every other alert in this layer has an event behind it: an allocation ran and
found nobody, somebody handed an item back. A breached assignment deadline has
none — nothing happens at the moment a deadline passes, so the only way to
notice is to look. That is this task, every fifteen minutes.

It writes ``Notification`` rows and ``last_alerted_at``, and nothing else. It
never allocates, never returns an item to the queue and never touches
``ProjectMember``: a sweep that could reassign work would be a scheduler
nobody asked for, and the whole design of this layer is that a person decides
who does what (RFC §1.2). What it changes is who knows.
"""

# Python imports
import logging

# Third party imports
from celery import shared_task

# Django imports
from django.utils import timezone

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")

# One pass alerts at most this many items. A queue that has hundreds of
# breached items has one problem, not two hundred notifications' worth of them;
# the cap keeps a misconfigured SLA from filling every coordinator's inbox in a
# single run, and the next run picks up where this one stopped.
MAX_ALERTS_PER_PASS = 200


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sweep_assignment_sla(self):
    """
    Alert the coordinators of every area holding an item past its deadline.

    @description Runs on the beat schedule (every 15 minutes). One area failing
    must not abort the pass, so failures are logged per item and the sweep
    keeps going; the task only retries when something fails outside the loop.

    Quiet for four hours per item after each alert, so an item nobody can place
    is mentioned a few times a day rather than ninety-six.
    @returns The number of items alerted about.
    """
    from plane.app.services.orca import organizational_units_enabled
    from plane.app.services.orca.alerts import ALERT_QUIET_PERIOD, alert_assignment_overdue
    from plane.db.models import IssueOrganizationalUnit, RoutingState, StateGroup

    # The kill switch reaches the beat, like the directory pass: with the layer
    # off there is no queue for anybody to coordinate, and alerting about one
    # would be the app insisting on a feature the operator switched off.
    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the assignment-SLA sweep.")
        return 0

    now = timezone.now()
    alerted = 0

    try:
        overdue = (
            IssueOrganizationalUnit.objects.filter(
                routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED),
                assignment_due_at__isnull=False,
                assignment_due_at__lt=now,
                organizational_unit__is_active=True,
            )
            # An item whose work is already finished is not waiting for
            # anybody, whatever its routing state says — the two can diverge
            # when somebody closes an item straight from the board.
            .exclude(issue__state__group__in=[StateGroup.COMPLETED.value, StateGroup.CANCELLED.value])
            .filter(models_q_quiet(now))
            .select_related("issue__project", "issue__state", "organizational_unit")
            .order_by("assignment_due_at")[:MAX_ALERTS_PER_PASS]
        )

        for link in overdue:
            try:
                if alert_assignment_overdue(link) == 0:
                    # Nobody to tell: no coordinator and no lead. Leaving
                    # ``last_alerted_at`` untouched means the item is picked up
                    # again as soon as the area has somebody to notify.
                    continue
                link.last_alerted_at = now
                link.save(update_fields=["last_alerted_at", "updated_at"])
                alerted += 1
            except Exception as exception:
                log_exception(exception)
    except Exception as exception:
        log_exception(exception)
        raise self.retry(exc=exception)

    if alerted:
        logger.info(
            "Assignment-SLA sweep alerted about %s work item(s) (quiet period %s).", alerted, ALERT_QUIET_PERIOD
        )
    return alerted


def models_q_quiet(now):
    """
    @description The "not alerted recently" half of the sweep's filter, as a
    ``Q`` so it stays inside the one query rather than being re-checked in
    Python per row.
    @param now: The instant the whole pass is judged against.
    @returns A ``Q`` matching items never alerted about, or alerted about
        longer ago than the quiet period.
    """
    from django.db.models import Q

    from plane.app.services.orca.alerts import ALERT_QUIET_PERIOD

    return Q(last_alerted_at__isnull=True) | Q(last_alerted_at__lt=now - ALERT_QUIET_PERIOD)
