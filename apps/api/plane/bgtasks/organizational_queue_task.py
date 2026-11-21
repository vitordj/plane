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
    logged per link and the pass keeps going. Closed, archived, soft-deleted,
    and draft work items (or inactive units) are excluded (R2.06), and
    ``last_alerted_at`` is stamped immediately per link only when at least one
    recipient was notified (R2.19).
    """
    from plane.app.services.orca.alerts import ALERT_ASSIGNMENT_SLA, mark_alerted, notify
    from plane.app.services.orca.feature_flags import organizational_units_enabled
    from plane.app.services.orca.queue import open_work_links

    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the assignment SLA sweep.")
        return

    now = timezone.now()
    cooldown_before = now - ALERT_COOLDOWN
    overdue = (
        open_work_links(
            IssueOrganizationalUnit.objects.filter(
                routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED),
                assignment_due_at__lt=now,
                organizational_unit__is_active=True,
            )
        )
        .filter(Q(last_alerted_at__isnull=True) | Q(last_alerted_at__lt=cooldown_before))
        .select_related(
            "issue",
            "project",
            "organizational_unit",
            "workspace",
        )
    )

    notified = 0
    for link in overdue:
        try:
            if notify(link, ALERT_ASSIGNMENT_SLA) > 0:
                mark_alerted([link.id])
                notified += 1
        except Exception as exception:
            log_exception(exception)

    logger.info("Assignment SLA sweep notified %s overdue item(s).", notified)


@shared_task
def sweep_routing_drift():
    """
    Hourly safety net that repairs routing drift across workspaces (R2.05).

    @description Native bulk/queryset assignee edits bypass Django signals.
    ``IssueCreateSerializer.update`` and ``BulkOperationsEndpoint`` also
    reconcile immediately, and this beat task catches any remaining path
    (I3/I4) within the hour — independent of ``ORCA_AVAILABILITY_ENABLED``.
    @returns Summary dict with ``workspaces`` scanned and ``repaired`` count.
    """
    from plane.app.services.orca.feature_flags import organizational_units_enabled
    from plane.app.services.orca.routing_audit import audit_routing

    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the routing drift sweep.")
        return {"skipped": "organizational layer disabled"}

    workspace_ids = list(
        IssueOrganizationalUnit.objects.filter(routing_state=RoutingState.ASSIGNED)
        .values_list("workspace_id", flat=True)
        .distinct()
    )
    repaired = 0
    for workspace_id in workspace_ids:
        try:
            findings = audit_routing(workspace_id, write=True)
            repaired += sum(1 for finding in findings if finding.repaired)
        except Exception as exception:  # noqa: BLE001 - one workspace failing must not stop others
            log_exception(exception)

    logger.info("Routing drift sweep scanned %s workspace(s) and repaired %s link(s).", len(workspace_ids), repaired)
    return {"workspaces": len(workspace_ids), "repaired": repaired}
