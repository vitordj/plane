# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The workspace's areas, counted (RFC §9 Fase 5, F18, F23).

Everything here is an aggregate over rows the earlier phases already write. No
new table, no new column, nothing recorded specially for a dashboard: a number
that exists only to be shown on a dashboard is a number nobody can check, and
the first time it disagrees with the queue, the queue is right.

That constraint is what shapes the module. Each indicator has **one** operative
definition, written next to it and repeated in
``docs/orca-executive-metrics.md`` as SQL somebody can run by hand. "Backlog"
means the area's items whose native state is not completed or cancelled — not
"open items", not "items in the queue", and not whatever a reader assumes. The
point of the page is to end an argument, and a page whose numbers cannot be
reproduced starts one.

Two decisions worth stating:

**Percentiles are computed by Postgres**, with ``percentile_cont`` — not in
Python over a fetched list, which would mean pulling every row of a quarter to
draw two numbers, and not as an average, which is not a percentile and hides
exactly the tail a director is looking for.

**The cache is five minutes and the answer says when it was computed.** These
are numbers for a conversation about a quarter, not a live console; a reader
who sees ``generated_at`` knows whether to refresh, and a reader who does not
see it assumes the page is live and reads a stale number as news.
"""

# Python imports
from datetime import timedelta

# Django imports
from django.core.cache import cache
from django.db.models import (
    Aggregate,
    Count,
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    Func,
    Q,
    Value,
)
from django.db.models.fields import FloatField
from django.utils import timezone

# Module imports
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionOutcome,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    ProcessInstanceItem,
    ProcessInstanceReference,
    RoutingState,
    StateGroup,
)

# The three windows the page offers. A free-form range would be a fourth way to
# get a different number for the same question, and nobody asked for one.
PERIODS = {"7d": 7, "30d": 30, "90d": 90}
DEFAULT_PERIOD = "30d"

# Five minutes. Long enough that a director clicking between areas does not
# re-run nine aggregates each time, short enough that "it was like that five
# minutes ago" is never the explanation for a number somebody disputes.
CACHE_TTL_SECONDS = 300

# How many executors "concentration" is about. Three, because the question
# behind it is "is this area actually one person?" and one, two or three people
# carrying everything is the shape that answers it.
CONCENTRATION_TOP_N = 3

# How many late steps the process block names. A list, not a report: the point
# is to see which step of which template is habitually late.
LATE_STEPS_LIMIT = 5

# States that mean the work is over, whichever way it ended. Cancelled counts
# as over everywhere in this module: an item nobody will do is not backlog.
CLOSED_GROUPS = (StateGroup.COMPLETED.value, StateGroup.CANCELLED.value)


class EpochSeconds(Func):
    """
    @description Seconds of an interval, as a float, for Postgres to rank.
    Written as a ``Func`` rather than ``Extract`` because ``Extract``'s output
    field is integral: rounding a queue age to whole seconds is harmless, and
    rounding a percentile's *input* is a habit that stops being harmless the
    day somebody measures something in milliseconds.
    """

    template = "EXTRACT(EPOCH FROM %(expressions)s)"
    output_field = FloatField()


class PercentileCont(Aggregate):
    """
    @description ``percentile_cont(p) WITHIN GROUP (ORDER BY expr)`` — an
    ordered-set aggregate Django has no shortcut for, and the reason the whole
    module can group by area in one query per family instead of fetching rows.

    It has to be an ``Aggregate`` and not a ``Func``: a ``Func`` over a column
    lands in the ``GROUP BY``, which silently turns "the median age of this
    area's queue" into "one row per distinct age" — a bug that reads as a
    plausible number rather than as an error.
    """

    function = "PERCENTILE_CONT"
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(self, expression, percentile, **extra):
        super().__init__(expression, percentile=percentile, **extra)


def _duration(start, end):
    """@description ``end - start`` as an interval Postgres can order. @returns An expression."""
    return ExpressionWrapper(end - start, output_field=DurationField())


def _seconds_since(field, now):
    """@description Seconds between ``field`` and ``now``. @returns An expression."""
    return EpochSeconds(_duration(F(field), Value(now, output_field=DateTimeField())))


def _rounded(value):
    """@description Seconds, to the second. @returns ``None`` or an int — a percentile of nothing is not zero."""
    return None if value is None else int(round(value))


def period_start(period, now):
    """
    @description The instant a period begins.
    @param period: One of ``PERIODS``; anything else falls back to the default
        rather than erroring, because a bad query parameter should not be able
        to make the page unreachable.
    @param now: The instant the whole answer is computed against.
    @returns A datetime.
    """
    return now - timedelta(days=PERIODS.get(period, PERIODS[DEFAULT_PERIOD]))


def cache_key(workspace_id, period, unit_id):
    """@description The key one answer is stored under. @returns A string."""
    return f"orca:executive:{workspace_id}:{period}:{unit_id or 'all'}"


def executive_metrics(workspace, *, period=DEFAULT_PERIOD, unit_id=None, now=None, use_cache=True):
    """
    @description Every area of the workspace, counted, plus the processes
    running through them (item 5.1).
    @param workspace: The ``Workspace``.
    @param period: ``7d``, ``30d`` or ``90d``. Unknown values become the default.
    @param unit_id: One area, or ``None`` for all of them.
    @param now: The instant to compute against. Passed in by the tests, and by
        anything that needs two calls to agree.
    @param use_cache: ``False`` reads through — what the tests and a person
        chasing a disputed number want.
    @returns A dict with ``period``, ``generated_at``, ``units`` and ``processes``.
    """
    period = period if period in PERIODS else DEFAULT_PERIOD
    now = now or timezone.now()
    key = cache_key(workspace.id, period, unit_id)

    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached

    units = OrganizationalUnit.objects.filter(workspace=workspace, is_active=True)
    if unit_id:
        units = units.filter(id=unit_id)
    units = list(units.order_by("name"))

    payload = {
        "period": period,
        "period_start": period_start(period, now).isoformat(),
        "generated_at": now.isoformat(),
        "units": unit_metrics(workspace, units, period=period, now=now),
        "processes": process_metrics(workspace, period=period, now=now),
    }

    if use_cache:
        cache.set(key, payload, CACHE_TTL_SECONDS)
    return payload


def unit_metrics(workspace, units, *, period, now):
    """
    @description The nine indicators, per area (item 5.1).
    @param workspace: The ``Workspace``.
    @param units: The areas to report on, already filtered and ordered.
    @param period: The window throughput and cycle time are measured over.
    @param now: The instant everything is judged against.
    @returns A list of dicts, one per area, in the order given.
    """
    if not units:
        return []

    unit_ids = [unit.id for unit in units]
    since = period_start(period, now)
    today = now.date()

    counts = {
        row["organizational_unit"]: row
        for row in IssueOrganizationalUnit.objects.filter(organizational_unit_id__in=unit_ids)
        .values("organizational_unit")
        .annotate(
            # Backlog: the area's work that is not over. Not "queued", not
            # "assigned" — both of those are subsets, and a page that calls one
            # of them the backlog is a page that under-reports the area.
            backlog=Count("id", filter=~Q(issue__state__group__in=CLOSED_GROUPS)),
            # Waiting for a person. ``allocation_failed`` belongs here because
            # it is an item waiting for a human precisely because the machine
            # could not place it.
            queued=Count(
                "id",
                filter=Q(routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED))
                & ~Q(issue__state__group__in=CLOSED_GROUPS),
            ),
            allocation_failed=Count("id", filter=Q(routing_state=RoutingState.ALLOCATION_FAILED)),
            # Waiting past the moment somebody was supposed to be on it. This
            # is the area's own promise breached, and it is a different failure
            # from the work being late.
            assignment_overdue=Count(
                "id",
                filter=Q(routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED))
                & Q(assignment_due_at__lt=now)
                & ~Q(issue__state__group__in=CLOSED_GROUPS),
            ),
            # The work itself late, whoever is on it.
            target_overdue=Count(
                "id",
                filter=Q(issue__target_date__lt=today) & ~Q(issue__state__group__in=CLOSED_GROUPS),
            ),
            assigned_open=Count(
                "id",
                filter=Q(routing_state=RoutingState.ASSIGNED) & ~Q(issue__state__group__in=CLOSED_GROUPS),
            ),
        )
    }

    ages = {
        row["organizational_unit"]: row
        for row in IssueOrganizationalUnit.objects.filter(
            organizational_unit_id__in=unit_ids,
            routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED),
            queued_at__isnull=False,
        )
        .exclude(issue__state__group__in=CLOSED_GROUPS)
        .values("organizational_unit")
        .annotate(
            queue_age_p50=PercentileCont(_seconds_since("queued_at", now), 0.5),
            queue_age_p90=PercentileCont(_seconds_since("queued_at", now), 0.9),
        )
    }

    # Throughput and cycle time are the same population — the area's items that
    # finished inside the window — so they are one query. ``completed_at`` is
    # Plane's own column, maintained by ``Issue.save``, which is why a step
    # closed by a person counts exactly like one closed by the API.
    finished = {
        row["organizational_unit"]: row
        for row in IssueOrganizationalUnit.objects.filter(
            organizational_unit_id__in=unit_ids,
            issue__completed_at__gte=since,
            issue__completed_at__lte=now,
        )
        .values("organizational_unit")
        .annotate(
            throughput=Count("id"),
            cycle_time_p50=PercentileCont(
                EpochSeconds(_duration(F("issue__created_at"), F("issue__completed_at"))), 0.5
            ),
            cycle_time_p90=PercentileCont(
                EpochSeconds(_duration(F("issue__created_at"), F("issue__completed_at"))), 0.9
            ),
        )
    }

    concentration = _concentration(unit_ids)
    kept = _auto_assign_kept(unit_ids, since=since, now=now)

    results = []
    for unit in units:
        count = counts.get(unit.id, {})
        age = ages.get(unit.id, {})
        done = finished.get(unit.id, {})
        results.append(
            {
                "unit": {"id": str(unit.id), "name": unit.name, "slug": unit.slug},
                "backlog": count.get("backlog", 0),
                "queued": count.get("queued", 0),
                "allocation_failed": count.get("allocation_failed", 0),
                "assignment_overdue": count.get("assignment_overdue", 0),
                "target_overdue": count.get("target_overdue", 0),
                "assigned_open": count.get("assigned_open", 0),
                "queue_age_p50": _rounded(age.get("queue_age_p50")),
                "queue_age_p90": _rounded(age.get("queue_age_p90")),
                "throughput": done.get("throughput", 0),
                "cycle_time_p50": _rounded(done.get("cycle_time_p50")),
                "cycle_time_p90": _rounded(done.get("cycle_time_p90")),
                "concentration_top3": concentration.get(unit.id),
                "auto_assign_kept_ratio": kept.get(unit.id),
            }
        )
    return results


def _concentration(unit_ids):
    """
    @description What share of an area's open assigned work its three busiest
    people carry. One means the area is those three; a third means it is spread.
    @param unit_ids: The areas.
    @returns ``{unit_id: float | None}`` — ``None`` when the area holds nothing
        assigned, because a share of nothing is not zero and printing 0% for an
        empty area would read as "perfectly distributed".
    """
    rows = (
        IssueOrganizationalUnit.objects.filter(
            organizational_unit_id__in=unit_ids,
            routing_state=RoutingState.ASSIGNED,
            primary_executor__isnull=False,
        )
        .exclude(issue__state__group__in=CLOSED_GROUPS)
        .values("organizational_unit", "primary_executor")
        .annotate(held=Count("id"))
    )

    per_unit = {}
    for row in rows:
        per_unit.setdefault(row["organizational_unit"], []).append(row["held"])

    concentration = {}
    for unit_id, held in per_unit.items():
        total = sum(held)
        if not total:
            continue
        top = sorted(held, reverse=True)[:CONCENTRATION_TOP_N]
        concentration[unit_id] = round(sum(top) / total, 4)
    return concentration


def _auto_assign_kept(unit_ids, *, since, now):
    """
    @description How often the ranking's choice stood (item 5.1). The
    denominator is every ``least_loaded`` assignment the area made in the
    window; the numerator is those a person did not later overturn.

    "Overturned" means a decision that supersedes it and was made **by a
    person** (``decided_by`` set). A sweep returning work because somebody went
    on holiday is not the ranking being wrong, and counting it as such would
    make the number worse every time the availability feature did its job.
    @param unit_ids: The areas.
    @param since, now: The window.
    @returns ``{unit_id: float | None}`` — ``None`` when the area made no such
        decision in the window, because a ratio of nothing is not 1.0.
    """
    decisions = AssignmentDecision.objects.filter(
        organizational_unit_id__in=unit_ids,
        effective_mode=AssignmentMode.LEAST_LOADED,
        outcome=DecisionOutcome.ASSIGNED,
        created_at__gte=since,
        created_at__lte=now,
    ).values("id", "organizational_unit")

    by_unit = {}
    ids = []
    for row in decisions:
        by_unit.setdefault(row["organizational_unit"], []).append(row["id"])
        ids.append(row["id"])

    if not ids:
        return {}

    overturned = set(
        AssignmentDecision.objects.filter(supersedes_id__in=ids, decided_by__isnull=False).values_list(
            "supersedes_id", flat=True
        )
    )

    kept = {}
    for unit_id, decision_ids in by_unit.items():
        total = len(decision_ids)
        if not total:
            continue
        kept[unit_id] = round(sum(1 for decision_id in decision_ids if decision_id not in overturned) / total, 4)
    return kept


def process_metrics(workspace, *, period, now):
    """
    @description The runs going through the workspace in the window, and which
    steps are habitually late (item 5.1).
    @param workspace: The ``Workspace``.
    @param period: The window.
    @param now: The instant to judge lateness against.
    @returns A dict with ``running``, ``completed``, the two lead times and
        ``late_steps``. Empty of runs, it is zeros and an empty list — the
        block is still rendered, because "no processes" is an answer.
    """
    since = period_start(period, now)

    instances = ProcessInstanceReference.objects.filter(workspace=workspace)
    running = instances.filter(completed_at__isnull=True, started_at__lte=now).count()
    completed_in_period = instances.filter(completed_at__gte=since, completed_at__lte=now)

    lead = completed_in_period.filter(started_at__isnull=False).aggregate(
        p50=PercentileCont(EpochSeconds(_duration(F("started_at"), F("completed_at"))), 0.5),
        p90=PercentileCont(EpochSeconds(_duration(F("started_at"), F("completed_at"))), 0.9),
    )

    # Late steps are grouped by ``step_key``, not by instance: "the KYC step is
    # always late" is actionable and "instance 4471 is late" is not.
    late = (
        ProcessInstanceItem.objects.filter(workspace=workspace)
        .exclude(issue__state__group__in=CLOSED_GROUPS)
        .filter(
            Q(issue__orca_service_level__completion_due_at__lt=now)
            | Q(issue__organizational_unit_links__assignment_due_at__lt=now)
        )
        .values("step_key", "process_instance__template_name")
        .annotate(late_count=Count("id", distinct=True))
        .order_by("-late_count", "step_key")[:LATE_STEPS_LIMIT]
    )

    return {
        "running": running,
        "completed": completed_in_period.count(),
        "lead_time_p50": _rounded(lead.get("p50")),
        "lead_time_p90": _rounded(lead.get("p90")),
        "late_steps": [
            {
                "step_key": row["step_key"],
                "template_name": row["process_instance__template_name"],
                "late_count": row["late_count"],
            }
            for row in late
        ],
    }


def drilldown_queryset(unit, metric, *, now, since=None):
    """
    @description The rows behind one number (item 5.3). Every filter here is
    the same predicate the aggregate used — if the two ever disagree, the page
    is lying about one of them, so they are written next to each other on
    purpose.
    @param unit: The area.
    @param metric: Which number was clicked.
    @param now, since: The instant and the window the number was computed for.
    @returns A queryset of ``IssueOrganizationalUnit``, or ``None`` for a
        metric that has no row-level meaning (a percentile of a population is
        not a list, and a page offering to drill into one would be inventing a
        list).
    """
    base = IssueOrganizationalUnit.objects.filter(organizational_unit=unit)
    open_only = base.exclude(issue__state__group__in=CLOSED_GROUPS)

    if metric == "backlog":
        return open_only
    if metric == "queued":
        return open_only.filter(routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED))
    if metric == "allocation_failed":
        return open_only.filter(routing_state=RoutingState.ALLOCATION_FAILED)
    if metric == "assignment_overdue":
        return open_only.filter(
            routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED), assignment_due_at__lt=now
        )
    if metric == "target_overdue":
        return open_only.filter(issue__target_date__lt=now.date())
    if metric == "assigned_open":
        return open_only.filter(routing_state=RoutingState.ASSIGNED)
    if metric == "throughput" and since is not None:
        return base.filter(issue__completed_at__gte=since, issue__completed_at__lte=now)
    return None


def readable_projects(user, workspace):
    """
    @description The projects this reader is actually a member of.

    The counts above are the workspace's; the rows are not. A workspace Admin
    who is not on a project can be told how many items sit in it and must not
    be shown their titles — Plane's own project membership is the authority on
    that, and the dashboard does not get to widen it (F18).
    @param user: The reader.
    @param workspace: The workspace.
    @returns A set of project ids.
    """
    from plane.db.models import ProjectMember

    return set(
        ProjectMember.objects.filter(workspace=workspace, member=user, is_active=True).values_list(
            "project_id", flat=True
        )
    )


__all__ = [
    "CACHE_TTL_SECONDS",
    "DEFAULT_PERIOD",
    "PERIODS",
    "cache_key",
    "drilldown_queryset",
    "executive_metrics",
    "period_start",
    "process_metrics",
    "readable_projects",
    "unit_metrics",
]
