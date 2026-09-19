# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Periodic pass over the area queues: notice an assignment deadline that passed.

The allocator records ``assignment_due_at`` when an item enters ``queued``
or ``allocation_failed``. Nothing watched that timestamp until this task:
without it a coordinator only finds the overdue item by opening the inbox.
The beat runs every fifteen minutes; ``last_alerted_at`` keeps the same
people from being told about the same item more than once every four hours.

The kill switch has to reach the beat, not just the API. This pass writes
``Notification`` rows, so leaving it running while the layer is off would
keep paging people about a queue the UI is hiding.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from plane.db.models import IssueOrganizationalUnit, RoutingState
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")

ALERT_COOLDOWN = timedelta(hours=4)


@shared_task
def sweep_assignment_sla():
    """
    Notify coordinators (or the lead) about items waiting past their assignment SLA.

    @description One item failing must not abort the rest, so failures are
    logged per link and the pass keeps going.
    """
    from plane.app.services.orca.alerts import ALERT_ASSIGNMENT_SLA, mark_alerted, notify
    from plane.app.services.orca.feature_flags import organizational_units_enabled

    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the assignment SLA sweep.")
        return

    now = timezone.now()
    cooldown_before = now - ALERT_COOLDOWN
    overdue = (
        IssueOrganizationalUnit.objects.filter(
            routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED),
            assignment_due_at__lt=now,
        )
        .filter(Q(last_alerted_at__isnull=True) | Q(last_alerted_at__lt=cooldown_before))
        .select_related(
            "issue",
            "project",
            "organizational_unit",
            "workspace",
        )
    )

    alerted = []
    for link in overdue:
        try:
            notify(link, ALERT_ASSIGNMENT_SLA)
            alerted.append(link.id)
        except Exception as exception:
            log_exception(exception)

    mark_alerted(alerted)
    logger.info("Assignment SLA sweep notified %s overdue item(s).", len(alerted))
