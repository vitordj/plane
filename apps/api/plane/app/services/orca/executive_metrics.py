# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What the areas of a workspace look like from above.

Every number here is defined once, in this module, and the definition is
written down beside it. That is not tidiness: an executive page whose numbers
cannot be reproduced by hand is a page people argue with instead of act on,
and the first argument is always about what the number means.

Three habits the definitions follow:

* **Open means not closed natively.** Completed and cancelled are both closed;
  a report that counted cancelled work as outstanding would make every tidy-up
  look like a problem.
* **Percentiles, not averages.** One work item that sat for three months moves
  an average enough to hide a queue that is otherwise healthy, and moves a p90
  exactly as much as it should.
* **A ratio says how many it is out of.** ``auto_assign_kept_ratio`` over four
  decisions is noise; the count travels with it so nobody reads 75% off a
  sample of four.

Read-only, cached for five minutes per (workspace, period, area). The cache is
short on purpose — the page is for deciding something this week, so five
minutes stale is fine and an hour is not.
"""

# Python imports
from datetime import timedelta

# Django imports
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone

# Module imports
from plane.db.models import (
    AssignmentDecision,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    ProcessInstanceReference,
    StateGroup,
)
from plane.db.models.organizational_assignment import AssignmentMode, DecisionOutcome
from plane.db.models.organizational_unit import RoutingState

# How long a page of aggregates stays warm.
CACHE_SECONDS = 300

# Windows the page offers. Anything else is refused rather than guessed at, so
# a typo does not silently produce a report of a period nobody asked for.
PERIODS = {"7d": 7, "30d": 30, "90d": 90}

# Work in these state groups is finished, one way or the other.
CLOSED_GROUPS = [StateGroup.COMPLETED.value, StateGroup.CANCELLED.value]
WAITING_STATES = [RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED]


def period_start(period, now=None):
    """
    @description The beginning of a named window.
    @param period: One of ``PERIODS``.
    @returns: A datetime.
    @raises ValueError: For a window that is not offered.
    """
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}")
    return (now or timezone.now()) - timedelta(days=PERIODS[period])


def _percentiles(values_sql, params, *, fractions=(0.5, 0.9)):
    """
    @description Percentiles from Postgres rather than from Python, so a large
    queue is not pulled into memory to sort it. Returns ``None`` per fraction
    when there is nothing to measure — a p50 of zero over an empty queue reads
    as "instant" and is a lie.
    @param values_sql: A SQL expression producing one numeric column.
    @returns: A list of floats or ``None``, one per fraction.
    """
    if connection.vendor != "postgresql":
        # Only the tests can reach this. Falling back to Python keeps a
        # non-Postgres run honest rather than making the module unimportable.
        with connection.cursor() as cursor:
            cursor.execute(values_sql, params)
            values = sorted(row[0] for row in cursor.fetchall() if row[0] is not None)
        if not values:
            return [None for _ in fractions]
        return [values[min(int(fraction * len(values)), len(values) - 1)] for fraction in fractions]

    selects = ", ".join(f"percentile_cont({fraction}) WITHIN GROUP (ORDER BY value)" for fraction in fractions)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {selects} FROM ({values_sql}) AS samples", params)
        row = cursor.fetchone()
    return [float(value) if value is not None else None for value in (row or [None] * len(fractions))]


def queue_age_percentiles(unit, now=None):
    """
    @description How long the work waiting in this area has been waiting, at
    the median and the ninetieth. In seconds.
    @param unit: The area.
    @returns: ``{"p50": float|None, "p90": float|None}``.
    """
    now = now or timezone.now()
    sql = """
        SELECT EXTRACT(EPOCH FROM (%s - queued_at)) AS value
        FROM issue_organizational_units
        WHERE organizational_unit_id = %s
          AND routing_state = ANY(%s)
          AND queued_at IS NOT NULL
          AND deleted_at IS NULL
    """
    p50, p90 = _percentiles(sql, [now, str(unit.id), list(WAITING_STATES)])
    return {"p50": p50, "p90": p90}


def cycle_time_percentiles(unit, since, now=None):
    """
    @description How long the work this area finished in the window took, from
    creation to completion. Uses Plane's own ``completed_at``, so it agrees
    with every other report in the product.
    @returns: ``{"p50": float|None, "p90": float|None}`` in seconds.
    """
    sql = """
        SELECT EXTRACT(EPOCH FROM (i.completed_at - i.created_at)) AS value
        FROM issues i
        JOIN issue_organizational_units link ON link.issue_id = i.id AND link.deleted_at IS NULL
        WHERE link.organizational_unit_id = %s
          AND i.completed_at IS NOT NULL
          AND i.completed_at >= %s
          AND i.deleted_at IS NULL
    """
    p50, p90 = _percentiles(sql, [str(unit.id), since])
    return {"p50": p50, "p90": p90}


def concentration_top3(unit):
    """
    @description What share of the area's open work its three busiest people
    are carrying. High is not automatically wrong — a small area of three is
    always 100% — but it is the number that finds the area where one person is
    quietly holding everything.
    @param unit: The area.
    @returns: ``{"ratio": float|None, "open_items": int, "executors": int}``.
    """
    counts = list(
        IssueOrganizationalUnit.objects.filter(organizational_unit=unit, routing_state=RoutingState.ASSIGNED)
        .exclude(issue__state__group__in=CLOSED_GROUPS)
        .values("primary_executor_id")
        .annotate(open_items=Count("id"))
        .order_by("-open_items")
    )
    total = sum(row["open_items"] for row in counts)
    if not total:
        return {"ratio": None, "open_items": 0, "executors": 0}

    top = sum(row["open_items"] for row in counts[:3])
    return {"ratio": round(top / total, 4), "open_items": total, "executors": len(counts)}


def auto_assign_kept_ratio(unit, since):
    """
    @description Of the automatic assignments this area made in the window, how
    many nobody overrode. A low ratio is the strongest available evidence that
    the ranking is wrong for this area — and the count is returned with it,
    because a ratio over four decisions is noise.
    @returns: ``{"ratio": float|None, "decisions": int, "kept": int}``.
    """
    decisions = AssignmentDecision.objects.filter(
        organizational_unit=unit,
        effective_mode=AssignmentMode.LEAST_LOADED,
        outcome=DecisionOutcome.ASSIGNED,
        created_at__gte=since,
    )
    total = decisions.count()
    if not total:
        return {"ratio": None, "decisions": 0, "kept": 0}

    # Overridden means a later decision names this one as what it replaced.
    overridden = AssignmentDecision.objects.filter(supersedes__in=decisions).values("supersedes_id").distinct().count()
    kept = total - overridden
    return {"ratio": round(kept / total, 4), "decisions": total, "kept": kept}


def unit_metrics(unit, period="30d", now=None):
    """
    @description Every indicator for one area over one window.
    @param unit: The area.
    @param period: One of ``PERIODS``.
    @returns: A dict of indicators, each defined in the module docstring of
        ``docs/orca-executive-metrics.md``.
    """
    now = now or timezone.now()
    since = period_start(period, now)
    today = now.date()

    links = IssueOrganizationalUnit.objects.filter(organizational_unit=unit)
    open_links = links.exclude(issue__state__group__in=CLOSED_GROUPS)

    counts = open_links.aggregate(
        backlog=Count("id"),
        queued=Count("id", filter=Q(routing_state__in=WAITING_STATES)),
        assignment_overdue=Count(
            "id",
            filter=Q(routing_state__in=WAITING_STATES, assignment_due_at__lt=now),
        ),
        target_overdue=Count("id", filter=Q(issue__target_date__lt=today)),
    )

    throughput = (
        Issue.objects.filter(
            organizational_unit_links__organizational_unit=unit,
            organizational_unit_links__deleted_at__isnull=True,
            completed_at__gte=since,
            state__group=StateGroup.COMPLETED.value,
        )
        .distinct()
        .count()
    )

    return {
        "unit": {"id": str(unit.id), "slug": unit.slug, "name": unit.name},
        "backlog": counts["backlog"],
        "queued": counts["queued"],
        "assignment_overdue": counts["assignment_overdue"],
        "target_overdue": counts["target_overdue"],
        "queue_age": queue_age_percentiles(unit, now=now),
        "throughput": throughput,
        "cycle_time": cycle_time_percentiles(unit, since, now=now),
        "concentration_top3": concentration_top3(unit),
        "auto_assign_kept": auto_assign_kept_ratio(unit, since),
    }


def process_metrics(workspace, period="30d", now=None):
    """
    @description How the workspace's process runs are going over the window:
    how many are still running, how many finished, how long the finished ones
    took, and which steps are latest.
    @returns: A dict.
    """
    now = now or timezone.now()
    since = period_start(period, now)

    instances = ProcessInstanceReference.objects.filter(workspace=workspace)
    running = instances.filter(status="running").count()
    completed = instances.filter(status="completed", completed_at__gte=since).count()

    sql = """
        SELECT EXTRACT(EPOCH FROM (completed_at - started_at)) AS value
        FROM orca_process_instances
        WHERE workspace_id = %s
          AND status = 'completed'
          AND completed_at IS NOT NULL
          AND started_at IS NOT NULL
          AND completed_at >= %s
          AND deleted_at IS NULL
    """
    p50, p90 = _percentiles(sql, [str(workspace.id), since])

    # The steps keeping runs open longest: what to look at first when a process
    # is slow, rather than which run happens to be oldest.
    latest_steps = list(
        IssueOrganizationalUnit.objects.filter(
            workspace=workspace,
            routing_state__in=WAITING_STATES,
            issue__orca_process_item__isnull=False,
        )
        .exclude(issue__state__group__in=CLOSED_GROUPS)
        .values("issue__orca_process_item__step_key")
        .annotate(waiting=Count("id"))
        .order_by("-waiting")[:5]
    )

    return {
        "running": running,
        "completed": completed,
        "lead_time": {"p50": p50, "p90": p90},
        "slowest_steps": [
            {"step_key": row["issue__orca_process_item__step_key"], "waiting": row["waiting"]} for row in latest_steps
        ],
    }


def executive_summary(workspace, period="30d", unit_id=None, now=None):
    """
    The whole page, cached.

    @description Cached for five minutes per (workspace, period, area): the
    page is for deciding something this week, so five minutes stale costs
    nothing and recomputing percentiles on every reload costs a lot.
    @param workspace: The workspace.
    @param period: One of ``PERIODS``.
    @param unit_id: Narrow to one area.
    @returns: A dict with ``period``, ``units`` and ``processes``.
    @raises ValueError: For a period that is not offered.
    """
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}")

    key = f"orca:executive:{workspace.id}:{period}:{unit_id or 'all'}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    units = OrganizationalUnit.objects.filter(workspace=workspace, is_active=True).order_by("name")
    if unit_id:
        units = units.filter(pk=unit_id)

    summary = {
        "period": period,
        "generated_at": (now or timezone.now()).isoformat(),
        "units": [unit_metrics(unit, period=period, now=now) for unit in units],
        "processes": process_metrics(workspace, period=period, now=now),
    }
    cache.set(key, summary, CACHE_SECONDS)
    return summary
