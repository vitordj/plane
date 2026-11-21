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


DRIFT_SWEEP_LIMIT = 200


@shared_task
def sweep_routing_drift(limit: int | None = None):
    """
    Hourly safety net that repairs routing drift across workspaces (R2.05).

    @description Native bulk/queryset assignee edits bypass Django signals.
    ``IssueCreateSerializer.update`` (app and public API) and
    ``BulkOperationsEndpoint`` also reconcile immediately, and this beat task
    catches any remaining path (I3/I4) within the hour — independent of
    ``ORCA_AVAILABILITY_ENABLED``.

    What it does: for every ``assigned`` item whose executor is no longer on
    the work item (I3) or no longer eligible — left the area or the project
    (I4) — it returns the item to the queue and raises the coordinators'
    ``executor_unavailable`` alert, once per item. Eligibility is the
    service's own check (active area membership in an active workspace
    membership, assignable project member), so a departed workspace member is
    caught too. It does not look at availability windows (vacations): those
    belong to ``sweep_unavailable_executors``, which is gated by
    ``ORCA_AVAILABILITY_ENABLED`` and also returns and alerts for the
    workspace/area/project departures. An item both sweeps see is returned by
    whichever runs first and alerted once, because the second finds it already queued.

    Bounded to ``limit`` repairs per run (defaults to ``DRIFT_SWEEP_LIMIT``) so
    a mass import or bulk deactivation cannot monopolize the worker; any
    remainder is picked up on the next run.
    @param limit: Optional override for the per-run repair cap.
    @returns Summary dict with ``workspaces`` scanned, ``repaired`` count, and
        ``capped`` boolean.
    """
    from django.conf import settings

    from plane.app.services.orca.feature_flags import organizational_units_enabled
    from plane.app.services.orca.routing_audit import audit_routing

    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the routing drift sweep.")
        return {"skipped": "organizational layer disabled"}

    budget = limit if limit is not None else int(getattr(settings, "ORCA_DRIFT_SWEEP_LIMIT", DRIFT_SWEEP_LIMIT))
    workspace_ids = list(
        IssueOrganizationalUnit.objects.filter(routing_state=RoutingState.ASSIGNED)
        .values_list("workspace_id", flat=True)
        .distinct()
    )
    repaired = 0
    capped = False
    for workspace_id in workspace_ids:
        remaining = budget - repaired
        if remaining <= 0:
            capped = True
            break
        try:
            findings = audit_routing(workspace_id, write=True, alert=True, limit=remaining)
            repaired += sum(1 for finding in findings if finding.repaired)
            if repaired >= budget:
                capped = True
                break
        except Exception as exception:  # noqa: BLE001 - one workspace failing must not stop others
            log_exception(exception)

    logger.info(
        "Routing drift sweep scanned %s workspace(s) and repaired %s link(s)%s.",
        len(workspace_ids),
        repaired,
        " (cap reached)" if capped else "",
    )
    return {"workspaces": len(workspace_ids), "repaired": repaired, "capped": capped}
