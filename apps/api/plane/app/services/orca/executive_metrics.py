# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Workspace-admin aggregates by area and by process (RFC Fase 5, item 5.1).

The numbers a director reads — backlog, queue age, throughput, how often
``least_loaded`` stuck — live here rather than in the view, so the HTTP
layer can stay a permission and cache wrapper, and so each indicator has one
query a test (and ``docs/orca-executive-metrics.md``) can name.

Percentiles are Postgres ``percentile_cont`` (continuous interpolation).
``NOW()`` is never used: a frozen clock in tests, and a single ``now`` for
every indicator on one request, would disagree with the database clock.
"""

from datetime import datetime, timedelta
from uuid import UUID

from django.db.models import (
    Aggregate,
    Case,
    Count,
    DateTimeField,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    Func,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import TruncDate
from django.utils import timezone

from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionTrigger,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ProcessInstanceStatus,
    ProjectMember,
    RoutingState,
    StateGroup,
    Workspace,
)

# Waiting for a person: the coordinator's inbox, including the ones the
# allocator already failed on (they still need a human).
WAITING_STATES = (RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED)

# Native groups that mean the work is no longer open. Everything else —
# backlog, unstarted, started, triage, or a missing state — is open.
CLOSED_GROUPS = (StateGroup.COMPLETED, StateGroup.CANCELLED)

# A later decision with one of these triggers, and a person in ``decided_by``,
# is a human override of an automatic choice (item 5.1).
HUMAN_OVERRIDE_TRIGGERS = (
    DecisionTrigger.UI_COORDINATOR,
    DecisionTrigger.REASSIGN,
    DecisionTrigger.UI_CLAIM,
)

PERIODS = {"7d": 7, "30d": 30, "90d": 90}
DEFAULT_PERIOD = "30d"
CACHE_TTL_SECONDS = 300
CACHE_KEY_PREFIX = "orca:executive"

DRILL_METRICS = ("backlog", "queued", "assignment_overdue", "target_overdue", "throughput")

OPEN_ISSUE = Q(issue__state__isnull=True) | ~Q(issue__state__group__in=CLOSED_GROUPS)


class PercentileCont(Aggregate):
    """
    Postgres ``percentile_cont(p) WITHIN GROUP (ORDER BY expr)``.

    @description Django has no built-in ordered-set aggregate. The template
    is the Postgres spelling; ``percentile`` is bound as a query parameter
    so a caller cannot inject SQL through the fraction.
    """

    function = "percentile_cont"
    name = "percentile_cont"
    output_field = FloatField()
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    allow_distinct = False

    def __init__(self, expression, percentile: float, **extra):
        if not 0 <= percentile <= 1:
            raise ValueError("percentile must be between 0 and 1")
        super().__init__(expression, percentile=percentile, **extra)


class ExtractEpoch(Func):
    """``EXTRACT(EPOCH FROM interval_or_timestamp)`` as seconds (float)."""

    function = "EXTRACT"
    template = "EXTRACT(EPOCH FROM %(expressions)s)"
    output_field = FloatField()


def parse_period(raw: str | None) -> str | None:
    """
    @description Map the query string to a known window. Unknown values are
    ``None`` so the view can refuse them without this module inventing a 400.
    @param raw: The ``period`` query parameter, or ``None`` for the default.
    @returns: ``7d``, ``30d`` or ``90d``, or ``None`` when the value is unknown.
    """
    if raw is None or raw == "":
        return DEFAULT_PERIOD
    return raw if raw in PERIODS else None


def period_start_for(period: str, now: datetime) -> datetime:
    """@description Inclusive start of the window ending at ``now``."""
    return now - timedelta(days=PERIODS[period])


def cache_key_for(workspace_id, period: str, unit_id: UUID | None) -> str:
    """@description One cache entry per (workspace, period, optional area)."""
    unit_part = str(unit_id) if unit_id else "all"
    return f"{CACHE_KEY_PREFIX}:{workspace_id}:{period}:{unit_part}"


def reader_project_ids(user, workspace: Workspace) -> set:
    """
    Native projects this person belongs to.

    @description Drill-down uses this set, not workspace-admin unrestricted
    access: F18 says an executive route never *lists* an item whose project
    the reader is not a member of. Counts still include those items.
    @returns: A set of project UUIDs. Empty if they hold no ``ProjectMember``.
    """
    return set(
        ProjectMember.objects.filter(member=user, workspace=workspace, is_active=True).values_list(
            "project_id", flat=True
        )
    )


def _links(workspace: Workspace, unit: OrganizationalUnit | None = None):
    qs = IssueOrganizationalUnit.objects.filter(workspace=workspace, project__archived_at__isnull=True)
    if unit is not None:
        qs = qs.filter(organizational_unit=unit)
    return qs


def _age_seconds(now: datetime):
    """``now - queued_at`` in seconds, using the request clock rather than ``NOW()``."""
    delta = ExpressionWrapper(
        Value(now, output_field=DateTimeField()) - F("queued_at"),
        output_field=DurationField(),
    )
    return ExtractEpoch(delta)


def _cycle_seconds():
    delta = ExpressionWrapper(F("issue__completed_at") - F("issue__created_at"), output_field=DurationField())
    return ExtractEpoch(delta)


def _lead_seconds():
    delta = ExpressionWrapper(F("completed_at") - F("started_at"), output_field=DurationField())
    return ExtractEpoch(delta)


def _percentiles(queryset, expression) -> tuple[float | None, float | None]:
    """
    @description ``percentile_cont`` at 0.5 and 0.9. Empty input is
    ``(None, None)`` — a p50 of 0 would look like a real measurement.
    @returns: ``(p50, p90)`` in the same unit as ``expression``.
    """
    if not queryset.exists():
        return None, None
    result = queryset.aggregate(
        p50=PercentileCont(expression, 0.5),
        p90=PercentileCont(expression, 0.9),
    )
    return result["p50"], result["p90"]


def _throughput_sparkline(links, period_start: datetime, now: datetime) -> list[dict]:
    """
    @description One bar per calendar day in the window: how many of this
    area's items had ``Issue.completed_at`` that day. Missing days are zero
    so a CSS sparkline has a stable width.
    """
    completed = links.filter(issue__completed_at__gte=period_start, issue__completed_at__lte=now)
    counted = {
        row["day"]: row["n"]
        for row in completed.annotate(day=TruncDate("issue__completed_at")).values("day").annotate(n=Count("id"))
        if row["day"] is not None
    }
    bars = []
    day = period_start.date()
    end = now.date()
    while day <= end:
        bars.append({"date": day.isoformat(), "count": int(counted.get(day, 0))})
        day += timedelta(days=1)
    return bars


def _concentration_top3(open_assigned) -> float | None:
    """
    @description Share of open assigned items held by the three people with
    the most of them. No open assigned item → ``None``, not 0: there is no
    concentration to report.
    """
    rows = (
        open_assigned.filter(primary_executor_id__isnull=False)
        .values("primary_executor_id")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    counts = [row["n"] for row in rows]
    total = sum(counts)
    if total == 0:
        return None
    return sum(counts[:3]) / total


def _auto_assign_kept_ratio(
    workspace: Workspace, unit: OrganizationalUnit | None, period_start: datetime, now: datetime
):
    """
    @description ``least_loaded`` decisions in the window that were not later
    replaced by a human (``ui_coordinator`` / ``reassign`` / ``ui_claim``
    with ``decided_by`` set), over the total ``least_loaded`` in the window.
    Denominator 0 → ``None``.
    """
    later_human = AssignmentDecision.objects.filter(
        issue_id=OuterRef("issue_id"),
        created_at__gt=OuterRef("created_at"),
        trigger__in=HUMAN_OVERRIDE_TRIGGERS,
        decided_by_id__isnull=False,
    )
    decisions = AssignmentDecision.objects.filter(
        workspace=workspace,
        effective_mode=AssignmentMode.LEAST_LOADED,
        created_at__gte=period_start,
        created_at__lte=now,
    )
    if unit is not None:
        decisions = decisions.filter(organizational_unit=unit)
    stats = decisions.annotate(replaced=Exists(later_human)).aggregate(
        total=Count("id"),
        kept=Count("id", filter=Q(replaced=False)),
    )
    total = stats["total"] or 0
    if total == 0:
        return None
    return stats["kept"] / total


def _hidden_count(links, visible_project_ids: set) -> int:
    """Live links whose project the reader is not a member of."""
    return links.exclude(project_id__in=visible_project_ids).count()


def unit_metrics(
    unit: OrganizationalUnit,
    *,
    period_start: datetime,
    now: datetime,
    visible_project_ids: set,
) -> dict:
    """
    @description Every indicator for one area, plus how many of its items
    sit in projects the reader cannot open.
    @param unit: The area.
    @param period_start: Inclusive start of the throughput / cycle / auto-assign window.
    @param now: The clock every overdue and age calculation uses.
    @param visible_project_ids: Native ``ProjectMember`` projects for the reader.
    @returns: JSON-ready dict matching ``IExecutiveUnitMetrics``.
    """
    links = _links(unit.workspace, unit)
    queued = links.filter(routing_state__in=WAITING_STATES)
    open_links = links.filter(OPEN_ISSUE)
    open_assigned = open_links.filter(routing_state=RoutingState.ASSIGNED)
    completed = links.filter(issue__completed_at__gte=period_start, issue__completed_at__lte=now)

    queue_age_p50, queue_age_p90 = _percentiles(queued.filter(queued_at__isnull=False), _age_seconds(now))
    cycle_p50, cycle_p90 = _percentiles(
        completed.filter(issue__completed_at__isnull=False, issue__created_at__isnull=False),
        _cycle_seconds(),
    )

    return {
        "unit_id": str(unit.id),
        "name": unit.name,
        "slug": unit.slug,
        "backlog": open_links.count(),
        "queued": queued.count(),
        "assignment_overdue": queued.filter(assignment_due_at__lt=now).count(),
        "target_overdue": open_links.filter(issue__target_date__lt=now.date()).count(),
        "queue_age_p50": queue_age_p50,
        "queue_age_p90": queue_age_p90,
        "throughput": completed.count(),
        "cycle_time_p50": cycle_p50,
        "cycle_time_p90": cycle_p90,
        "concentration_top3": _concentration_top3(open_assigned),
        "auto_assign_kept_ratio": _auto_assign_kept_ratio(unit.workspace, unit, period_start, now),
        "throughput_sparkline": _throughput_sparkline(links, period_start, now),
        "hidden_count": _hidden_count(links, visible_project_ids),
    }


def _delayed_steps(
    instances,
    *,
    now: datetime,
    visible_project_ids: set,
    limit: int = 5,
) -> list[dict]:
    """
    @description Steps whose promised date has passed, still open, ranked by
    how late. Items in projects the reader cannot open are omitted — they
    would be a list of work the person cannot follow (F18).
    """
    due_at = Case(
        When(
            issue__orca_service_level__completion_due_at__lt=now,
            then=F("issue__orca_service_level__completion_due_at"),
        ),
        default=F("issue__orca_service_level__assignment_due_at"),
        output_field=DateTimeField(),
    )
    kind = Case(
        When(issue__orca_service_level__completion_due_at__lt=now, then=Value("completion")),
        default=Value("assignment"),
    )
    late = ExtractEpoch(
        ExpressionWrapper(Value(now, output_field=DateTimeField()) - due_at, output_field=DurationField())
    )
    overdue = Q(issue__orca_service_level__completion_due_at__lt=now) | Q(
        issue__orca_service_level__assignment_due_at__lt=now
    )
    rows = (
        ProcessInstanceItem.objects.filter(process_instance__in=instances)
        .filter(overdue)
        .filter(OPEN_ISSUE)
        .filter(issue__project_id__in=visible_project_ids)
        .annotate(due_at=due_at, kind=kind, late_seconds=late)
        .select_related("process_instance", "issue")
        .order_by("-late_seconds")[:limit]
    )
    return [
        {
            "process_instance_id": str(row.process_instance_id),
            "template_name": row.process_instance.template_name,
            "step_key": row.step_key,
            "issue_id": str(row.issue_id),
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "kind": row.kind,
            "late_seconds": row.late_seconds,
        }
        for row in rows
    ]


def process_metrics(
    workspace: Workspace,
    *,
    period_start: datetime,
    now: datetime,
    unit: OrganizationalUnit | None,
    visible_project_ids: set,
) -> dict:
    """
    @description Running instances plus those that completed in the window,
    with lead-time percentiles and the most delayed open steps.
    """
    in_period = Q(status=ProcessInstanceStatus.RUNNING) | Q(
        status=ProcessInstanceStatus.COMPLETED,
        completed_at__gte=period_start,
        completed_at__lte=now,
    )
    instances = ProcessInstanceReference.objects.filter(workspace=workspace).filter(in_period)
    if unit is not None:
        instances = instances.filter(items__issue__organizational_unit_links__organizational_unit=unit).distinct()

    running = instances.filter(status=ProcessInstanceStatus.RUNNING)
    completed = instances.filter(status=ProcessInstanceStatus.COMPLETED)
    lead_p50, lead_p90 = _percentiles(
        completed.filter(started_at__isnull=False, completed_at__isnull=False),
        _lead_seconds(),
    )

    instance_rows = []
    for row in instances.order_by("-started_at"):
        lead = None
        if row.started_at and row.completed_at:
            lead = (row.completed_at - row.started_at).total_seconds()
        instance_rows.append(
            {
                "process_instance_id": str(row.id),
                "template_name": row.template_name,
                "template_version": row.template_version,
                "status": row.status,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "lead_time_seconds": lead,
            }
        )

    return {
        "running": running.count(),
        "completed": completed.count(),
        "lead_time_p50": lead_p50,
        "lead_time_p90": lead_p90,
        "delayed_steps": _delayed_steps(instances, now=now, visible_project_ids=visible_project_ids),
        "instances": instance_rows,
    }


def build_executive_report(
    workspace: Workspace,
    *,
    period: str,
    unit: OrganizationalUnit | None,
    now: datetime | None = None,
    viewer=None,
) -> dict:
    """
    @description The payload ``GET .../executive/`` returns. Counts include
    items in projects the reader cannot open; ``hidden_count`` says how
    many, per area. Drill-down is a separate call.
    @param workspace: The workspace.
    @param period: ``7d``, ``30d`` or ``90d``.
    @param unit: Restrict to one area, or ``None`` for every area.
    @param now: Clock for overdue / age / the window end. Defaults to now.
    @param viewer: The requesting user, used only for ``hidden_count`` and
        delayed-step listing.
    @returns: JSON-ready dict matching ``IExecutiveReport``.
    """
    now = now or timezone.now()
    start = period_start_for(period, now)
    visible = reader_project_ids(viewer, workspace) if viewer is not None else set()

    units_qs = OrganizationalUnit.objects.filter(workspace=workspace)
    if unit is not None:
        units_qs = units_qs.filter(pk=unit.pk)
    units_qs = units_qs.order_by("name")

    return {
        "period": period,
        "period_start": start.isoformat(),
        "generated_at": now.isoformat(),
        "units": [unit_metrics(row, period_start=start, now=now, visible_project_ids=visible) for row in units_qs],
        "processes": process_metrics(
            workspace,
            period_start=start,
            now=now,
            unit=unit,
            visible_project_ids=visible,
        ),
    }


def drill_down_queryset(
    unit: OrganizationalUnit,
    metric: str,
    *,
    period: str,
    now: datetime,
    visible_project_ids: set,
):
    """
    @description Live links matching one indicator, restricted to projects
    the reader belongs to. The matching items in other projects are counted
    as ``hidden_count`` by the caller, not returned.
    @param metric: One of ``DRILL_METRICS``.
    @returns: A queryset of ``IssueOrganizationalUnit``.
    """
    start = period_start_for(period, now)
    links = _links(unit.workspace, unit)
    if metric == "backlog":
        links = links.filter(OPEN_ISSUE)
    elif metric == "queued":
        links = links.filter(routing_state__in=WAITING_STATES)
    elif metric == "assignment_overdue":
        links = links.filter(routing_state__in=WAITING_STATES, assignment_due_at__lt=now)
    elif metric == "target_overdue":
        links = links.filter(OPEN_ISSUE, issue__target_date__lt=now.date())
    elif metric == "throughput":
        links = links.filter(issue__completed_at__gte=start, issue__completed_at__lte=now)
    else:
        raise ValueError(metric)
    hidden = links.exclude(project_id__in=visible_project_ids).count()
    visible_links = (
        links.filter(project_id__in=visible_project_ids)
        .select_related("issue", "issue__state", "issue__project", "primary_executor")
        .order_by(F("queued_at").asc(nulls_last=True), "created_at")
    )
    return visible_links, hidden


def executive_drill_row(link, *, now: datetime) -> dict:
    """
    @description One drill-down row in the same shape as the coordinator
    queue, with every action flag off: this is a report, not a work board.
    """
    issue = link.issue
    state = issue.state
    executor = link.primary_executor
    overdue = bool(link.assignment_due_at and link.assignment_due_at < now)
    return {
        "issue_id": str(link.issue_id),
        "sequence_id": issue.sequence_id,
        "name": issue.name,
        "project": {
            "id": str(issue.project_id),
            "identifier": issue.project.identifier,
            "name": issue.project.name,
        },
        "state": (
            {"id": str(state.id), "name": state.name, "color": state.color, "group": state.group}
            if state is not None
            else None
        ),
        "priority": issue.priority,
        "target_date": issue.target_date.isoformat() if issue.target_date else None,
        "routing_state": link.routing_state,
        "queue_reason": link.queue_reason,
        "queued_at": link.queued_at.isoformat() if link.queued_at else None,
        "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
        "assignment_overdue": overdue,
        "age_seconds": int((now - link.queued_at).total_seconds()) if link.queued_at else 0,
        "primary_executor": (
            None
            if executor is None
            else {
                "id": str(executor.id),
                "display_name": executor.display_name,
                "email": executor.email,
                "avatar_url": executor.avatar_url,
            }
        ),
        "current_decision_id": (
            str(link.current_assignment_decision_id) if link.current_assignment_decision_id else None
        ),
        "permissions": {"can_claim": False, "can_assign": False, "can_return": False},
    }


def attach_reader_fields(
    workspace: Workspace,
    payload: dict,
    *,
    viewer,
    now: datetime,
    unit: OrganizationalUnit | None,
) -> dict:
    """
    @description Fill ``hidden_count`` and delayed steps on a cached report.
    Those fields depend on who is reading and must not be cached.
    @param payload: The cached body from ``build_executive_report``.
    @param viewer: The requesting user.
    @returns: A new dict; ``payload`` is not mutated.
    """
    visible = reader_project_ids(viewer, workspace)
    start = period_start_for(payload["period"], now)
    units = []
    for row in payload.get("units", []):
        area = OrganizationalUnit.objects.filter(pk=row["unit_id"], workspace=workspace).first()
        hidden = _hidden_count(_links(workspace, area), visible) if area is not None else 0
        units.append({**row, "hidden_count": hidden})
    processes = process_metrics(
        workspace,
        period_start=start,
        now=now,
        unit=unit,
        visible_project_ids=visible,
    )
    cached_processes = payload.get("processes") or {}
    return {
        **payload,
        "units": units,
        "processes": {**cached_processes, "delayed_steps": processes["delayed_steps"]},
    }


__all__ = [
    "CACHE_KEY_PREFIX",
    "CACHE_TTL_SECONDS",
    "DEFAULT_PERIOD",
    "DRILL_METRICS",
    "HUMAN_OVERRIDE_TRIGGERS",
    "PERIODS",
    "PercentileCont",
    "attach_reader_fields",
    "build_executive_report",
    "cache_key_for",
    "drill_down_queryset",
    "executive_drill_row",
    "parse_period",
    "period_start_for",
    "reader_project_ids",
]
