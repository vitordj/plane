# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Orca (fork): the pass that notices an assignment deadline came and went.

``assignment_due_at`` (RFC §6.6) is written when an item enters ``queued`` or
``allocation_failed``, and until now nothing read it except the queue view's
ordering. A deadline nobody is watching is a comment, so this is the watcher:
every quarter of an hour it looks for items whose deadline has passed and
whose area has not been told recently, and tells them (item 2.4).

**Why fifteen minutes.** The SLA is the promise; the sweep is how late an
alert can be against it. A promise measured in hours cannot be policed hourly
without spending a sixth of the window on the check itself, and a minute-level
tick would scan the whole queue ninety-six times more often than anything
changes. Fifteen minutes is small against any SLA a human would set and cheap
against the index the queue already has.

**Why ``last_alerted_at`` and not a separate ledger.** Without a stamp this
task alerts the same people about the same item every fifteen minutes until
somebody picks it up, which trains them to ignore it. The stamp lives on
``IssueOrganizationalUnit`` and is written with ``update()``: that table is
mutable state — the item's current routing — unlike ``AssignmentDecision`` and
``IssueResponsibilityEvent``, which are append-only ledgers where an update
would erase history. The four-hour window is the re-alert interval: long
enough that a coordinator who saw the first one is not nagged, short enough
that an item forgotten overnight is raised again in the morning.

**The kill switch reaches the beat, not just the API.** With
``ORCA_ORG_UNITS_ENABLED`` off the layer is supposed to be invisible, and a
task that keeps writing notifications about areas nobody can see is the one
door left open — the same reasoning as ``organizational_directory_task``.
"""

# Python imports
from datetime import timedelta
import logging

# Django imports
from django.db.models import Q
from django.utils import timezone

# Third party imports
from celery import shared_task

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")

# How long an area is left alone after being told about one item. Not a policy
# knob on purpose: it is the alert's own repeat interval, not the SLA, and the
# SLA is already configurable per unit and per project.
REALERT_AFTER = timedelta(hours=4)

# The alert says "past its deadline with nobody on it", which is only true in
# the two waiting states. Imported rather than restated so a new waiting state
# is swept without anybody remembering to come back here.
#
# (``WAITING_STATES`` is ``queued`` + ``allocation_failed``; see
# ``services/orca/queue.py``, which holds what "the queue" means.)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sweep_assignment_sla(self):
    """
    Alert the areas whose queued work has passed its assignment deadline.

    @description Runs on the beat schedule every fifteen minutes. One item
    failing must not abort the rest, so failures are logged per item and the
    pass keeps going; the task only retries when something fails outside the
    loop — a database that is gone, say, rather than one area with no
    coordinator.
    @returns How many items were alerted about, which is what the tests read
        and what a log pipeline can graph.
    """
    from plane.app.services.orca import organizational_units_enabled
    from plane.app.services.orca.alerts import KIND_ASSIGNMENT_OVERDUE, notify
    from plane.app.services.orca.queue import WAITING_STATES
    from plane.db.models import IssueOrganizationalUnit

    # Off means off, including here. Leaving this running while the layer is
    # disabled would keep writing notifications about a feature the workspace
    # cannot open — noise nobody can act on and nobody asked for.
    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the assignment SLA sweep.")
        return 0

    alerted = 0
    try:
        now = timezone.now()
        cutoff = now - REALERT_AFTER

        # Evaluated against one ``now`` for the whole pass, so an item does not
        # fall on the wrong side of the deadline because the loop took a second.
        overdue = (
            IssueOrganizationalUnit.objects.filter(
                routing_state__in=WAITING_STATES,
                assignment_due_at__isnull=False,
                assignment_due_at__lt=now,
            )
            # "Nobody has been told, or the last telling is old enough."
            .filter(Q(last_alerted_at__isnull=True) | Q(last_alerted_at__lt=cutoff))
            .select_related("issue", "issue__project", "issue__state", "organizational_unit")
            .order_by("assignment_due_at")
        )

        for link in overdue.iterator(chunk_size=200):
            try:
                receivers = notify(link, KIND_ASSIGNMENT_OVERDUE)
            except Exception as exception:
                # One area's bad configuration is not the sweep's failure.
                log_exception(exception)
                continue

            if not receivers:
                # Nobody to tell. Deliberately *not* stamped: stamping would
                # start a four-hour silence for an alert that never went out,
                # so the moment a coordinator is appointed the next tick
                # reaches them instead of waiting out a window nobody heard.
                continue

            # ``update()`` on the queryset rather than ``link.save()``: this
            # writes one column and must not fire the model's ``save()``, which
            # stamps ``updated_by`` from the current user — there is no user
            # here, and the item did not change, only what we told people about
            # it.
            IssueOrganizationalUnit.objects.filter(pk=link.pk).update(last_alerted_at=now)
            alerted += 1

        if alerted:
            logger.info(
                "Assignment SLA sweep alerted on %s work item(s).",
                alerted,
                extra={"metric": "orca.queue.overdue", "alerted": alerted},
            )
    except Exception as exception:
        log_exception(exception)
        raise self.retry(exc=exception)

    return alerted
