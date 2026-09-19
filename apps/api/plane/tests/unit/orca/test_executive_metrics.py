# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Fixed-dataset tests for the executive aggregates (item 5.4).

Every expected value is written next to the arithmetic that produces it.
The clock is frozen so ``now - queued_at`` is an exact number of days, and
``created_at`` / ``completed_at`` are set with ``QuerySet.update`` because
``auto_now_add`` would otherwise stamp the moment the fixture ran.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from rest_framework import status

from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionOutcome,
    DecisionTrigger,
    Issue,
    IssueOrganizationalUnit,
    IssueServiceLevel,
    OrganizationalUnit,
    PolicySource,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ProcessInstanceStatus,
    RoutingState,
    ServiceLevelSource,
    StateGroup,
    WorkspaceMember,
)

from .conftest import ROLE_MEMBER, make_user

# 2026-09-10 15:00 UTC. Every age, SLA and window in this file is relative to
# this instant so a percentile is an integer number of days, not a moving
# target.
FROZEN = datetime(2026, 9, 10, 15, 0, 0, tzinfo=dt_timezone.utc)
DAY = 86400  # seconds
YESTERDAY = (FROZEN - timedelta(days=1)).date()


def ago(**kwargs) -> datetime:
    return FROZEN - timedelta(**kwargs)


def executive_url(slug, **params):
    """@description ``GET .../executive/`` with optional query string."""
    path = f"/api/orca/workspaces/{slug}/executive/"
    return f"{path}?{urlencode(params)}" if params else path


def drill_url(slug, **params):
    """@description ``GET .../executive/drill-down/``."""
    path = f"/api/orca/workspaces/{slug}/executive/drill-down/"
    return f"{path}?{urlencode(params)}" if params else path


def percentile_cont(values, p: float) -> float | None:
    """
    Postgres continuous percentile (1-based index ``1 + (n-1)*p``).

    @description Same interpolation the database uses, so a failing
    assertion can be checked against this function without opening psql.
    """
    if not values:
        return None
    xs = sorted(values)
    n = len(xs)
    index = 1 + (n - 1) * p
    lower = int(index)
    if index == lower:
        return float(xs[lower - 1])
    frac = index - lower
    return xs[lower - 1] + frac * (xs[lower] - xs[lower - 1])


def _issue(make_issue, project, name, *, group=StateGroup.UNSTARTED.value, target=None):
    issue = make_issue(project, name=name, state_group=group)
    if target is not None:
        Issue.objects.filter(pk=issue.pk).update(target_date=target)
        issue.refresh_from_db()
    return issue


def _stamp(issue, *, created, completed=None):
    """Write timestamps the ORM would otherwise refuse to move."""
    Issue.objects.filter(pk=issue.pk).update(created_at=created, completed_at=completed)
    issue.refresh_from_db()
    return issue


def _link(issue, unit, *, routing=RoutingState.QUEUED, executor=None, queued_at=None, due=None):
    return IssueOrganizationalUnit.objects.create(
        issue=issue,
        organizational_unit=unit,
        project=issue.project,
        workspace=issue.workspace,
        routing_state=routing,
        primary_executor=executor,
        queued_at=queued_at,
        assignment_due_at=due,
    )


def _decision(issue, unit, *, mode, trigger, decided_by=None, created=None, supersedes=None):
    row = AssignmentDecision.objects.create(
        issue=issue,
        organizational_unit=unit,
        project=issue.project,
        workspace=issue.workspace,
        trigger=trigger,
        effective_mode=mode,
        policy_source=PolicySource.FALLBACK,
        outcome=DecisionOutcome.ASSIGNED if decided_by else DecisionOutcome.QUEUED,
        decided_by=decided_by,
        supersedes=supersedes,
    )
    if created is not None:
        AssignmentDecision.objects.filter(pk=row.pk).update(created_at=created)
        row.refresh_from_db()
    return row


def _by_slug(payload, slug):
    return next(row for row in payload["units"] if row["slug"] == slug)


@pytest.fixture
def extra_user(db, workspace_with_members):
    """Fourth executor, so concentration is not trivially 1.0."""
    user = make_user("extra@plane.so", "extra", "Extra")
    WorkspaceMember.objects.create(workspace=workspace_with_members, member=user, role=ROLE_MEMBER)
    return user


@pytest.fixture
def finance_unit(db, workspace_with_members):
    return OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Finance", slug="finance")


@pytest.fixture
def frozen_clock(monkeypatch):
    """
    Pin the request clock without patching ``datetime.date``.

    @description ``freezegun`` replaces ``date``'s metaclass, and the first
    URL resolution in a process then fails importing pydantic (via openai)
    with a metaclass conflict. Patching ``timezone.now`` is enough: every
    indicator takes its ``now`` from the view.
    """
    monkeypatch.setattr("plane.app.views.organizational_executive.timezone.now", lambda: FROZEN)
    monkeypatch.setattr("django.utils.timezone.now", lambda: FROZEN)
    yield FROZEN


@pytest.fixture
def world(
    workspace_with_members,
    unit,
    second_unit,
    finance_unit,
    project,
    second_project,
    admin_user,
    plain_user,
    second_user,
    extra_user,
    guest_user,
    make_issue,
    link_project,
    grant_manual_access,
):
    """
    3 areas, 2 processes, 40 work items.

    P1 (``project``) is the project the admin belongs to. P2 (``second_project``)
    is one they do not — counts include it, drill-down must not list it.
    """
    link_project(unit, project)
    link_project(unit, second_project)
    link_project(second_unit, project)
    link_project(second_unit, second_project)
    link_project(finance_unit, project)
    grant_manual_access(project, admin_user)

    # --- Area A / Compliance (22) ---------------------------------------
    # Queued on P1, ages 1, 2, 4, 8, 16 days. Three of five are assignment-overdue.
    a_q1 = _link(
        _issue(make_issue, project, "A-Q1"),
        unit,
        queued_at=ago(days=1),
        due=FROZEN + timedelta(days=1),
    )
    a_q2 = _link(
        _issue(make_issue, project, "A-Q2"),
        unit,
        queued_at=ago(days=2),
        due=ago(hours=1),
    )
    a_q3 = _link(_issue(make_issue, project, "A-Q3"), unit, queued_at=ago(days=4))
    a_q4 = _link(
        _issue(make_issue, project, "A-Q4", target=YESTERDAY),
        unit,
        queued_at=ago(days=8),
        due=ago(days=2),
    )
    a_q5 = _link(
        _issue(make_issue, project, "A-Q5"),
        unit,
        routing=RoutingState.ALLOCATION_FAILED,
        queued_at=ago(days=16),
        due=ago(days=3),
    )
    # Queued on P2 (hidden from the admin).
    a_h1 = _link(
        _issue(make_issue, second_project, "A-H1", target=YESTERDAY),
        unit,
        queued_at=ago(days=3),
        due=ago(days=1),
    )
    a_h2 = _link(
        _issue(make_issue, second_project, "A-H2"),
        unit,
        routing=RoutingState.ALLOCATION_FAILED,
        queued_at=ago(days=5),
    )
    # Assigned open on P1: plain×4, second×2, extra×1, guest×1.
    a_plain = [
        _link(
            _issue(make_issue, project, f"A-P{i}", target=YESTERDAY if i == 1 else None),
            unit,
            routing=RoutingState.ASSIGNED,
            executor=plain_user,
        )
        for i in range(1, 5)
    ]
    [
        _link(
            _issue(make_issue, project, f"A-S{i}"),
            unit,
            routing=RoutingState.ASSIGNED,
            executor=second_user,
        )
        for i in range(1, 3)
    ]
    a_extra = _link(
        _issue(make_issue, project, "A-E1"),
        unit,
        routing=RoutingState.ASSIGNED,
        executor=extra_user,
    )
    _link(
        _issue(make_issue, project, "A-G1"),
        unit,
        routing=RoutingState.ASSIGNED,
        executor=guest_user,
    )
    # Assigned open on P2 (hidden), counts toward concentration and target_overdue.
    a_h3 = _link(
        _issue(make_issue, second_project, "A-H3", target=YESTERDAY),
        unit,
        routing=RoutingState.ASSIGNED,
        executor=second_user,
    )
    # Completed in the 30-day window. Cycle times 2, 4, 6, 10 days (P1) + 3 days (P2).
    a_c = []
    for name, created_days, done_days, proj in (
        ("A-C1", 3, 1, project),
        ("A-C2", 9, 5, project),
        ("A-C3", 16, 10, project),
        ("A-C4", 30, 20, project),
        ("A-CH", 5, 2, second_project),
    ):
        issue = _issue(make_issue, proj, name, group=StateGroup.COMPLETED.value)
        _stamp(issue, created=ago(days=created_days), completed=ago(days=done_days))
        a_c.append(_link(issue, unit, routing=RoutingState.ASSIGNED, executor=plain_user))
    # Completed outside the window — must not land in throughput.
    outside = _issue(make_issue, project, "A-OUT", group=StateGroup.COMPLETED.value)
    _stamp(outside, created=ago(days=50), completed=ago(days=40))
    _link(outside, unit, routing=RoutingState.ASSIGNED, executor=plain_user)

    # 10 least_loaded in the window; 3 later replaced by a human reassign.
    auto_issues = (
        [a_q1.issue, a_q2.issue, a_q3.issue, a_q4.issue, a_q5.issue] + [row.issue for row in a_plain] + [a_extra.issue]
    )
    autos = []
    for issue in auto_issues:
        autos.append(
            _decision(
                issue,
                unit,
                mode=AssignmentMode.LEAST_LOADED,
                trigger=DecisionTrigger.INTERNAL_API,
                created=ago(days=10),
            )
        )
    for auto in autos[:3]:
        _decision(
            auto.issue,
            unit,
            mode=AssignmentMode.EXPLICIT,
            trigger=DecisionTrigger.REASSIGN,
            decided_by=admin_user,
            created=ago(days=1),
            supersedes=auto,
        )

    # --- Area B / Legal (12) ------------------------------------------
    for days in (2, 6, 10):
        _link(_issue(make_issue, project, f"B-Q{days}"), second_unit, queued_at=ago(days=days))
    for i in range(4):
        _link(
            _issue(make_issue, project, f"B-A{i}"),
            second_unit,
            routing=RoutingState.ASSIGNED,
            executor=plain_user,
        )
    for i in range(3):
        issue = _issue(make_issue, project, f"B-C{i}", group=StateGroup.COMPLETED.value)
        _stamp(issue, created=ago(days=10), completed=ago(days=5))
        _link(issue, second_unit, routing=RoutingState.ASSIGNED, executor=plain_user)
    cancelled = _issue(make_issue, project, "B-X", group=StateGroup.CANCELLED.value)
    _link(cancelled, second_unit, routing=RoutingState.SUSPENDED)
    _link(
        _issue(make_issue, second_project, "B-H"),
        second_unit,
        queued_at=ago(days=4),
        due=ago(hours=2),
    )

    # --- Area C / Finance (6) -----------------------------------------
    c_issues = []
    for i in range(2):
        c_issues.append(_link(_issue(make_issue, project, f"C-Q{i}"), finance_unit, queued_at=ago(days=1)))
    for i in range(2):
        c_issues.append(
            _link(
                _issue(make_issue, project, f"C-A{i}"),
                finance_unit,
                routing=RoutingState.ASSIGNED,
                executor=extra_user,
            )
        )
    for created_days, done_days in ((2, 1), (10, 1)):
        issue = _issue(make_issue, project, f"C-C{created_days}", group=StateGroup.COMPLETED.value)
        _stamp(issue, created=ago(days=created_days), completed=ago(days=done_days))
        c_issues.append(_link(issue, finance_unit, routing=RoutingState.ASSIGNED, executor=extra_user))
    for row in c_issues[:4]:
        _decision(
            row.issue,
            finance_unit,
            mode=AssignmentMode.LEAST_LOADED,
            trigger=DecisionTrigger.INTERNAL_API,
            created=ago(days=5),
        )

    # --- 2 processes -----------------------------------------------------
    running = ProcessInstanceReference.objects.create(
        workspace=workspace_with_members,
        external_source="espo",
        external_instance_id="client-running",
        template_name="onboarding",
        template_version="3",
        status=ProcessInstanceStatus.RUNNING,
        started_at=ago(days=10),
    )
    ProcessInstanceReference.objects.filter(pk=running.pk).update(started_at=ago(days=10))
    ProcessInstanceItem.objects.create(
        process_instance=running,
        issue=a_plain[0].issue,
        workspace=workspace_with_members,
        step_key="kyc",
    )
    ProcessInstanceItem.objects.create(
        process_instance=running,
        issue=a_plain[1].issue,
        workspace=workspace_with_members,
        step_key="contract",
    )
    IssueServiceLevel.objects.create(
        issue=a_plain[0].issue,
        workspace=workspace_with_members,
        completion_due_at=ago(days=2),
        source=ServiceLevelSource.PROCESS,
    )
    IssueServiceLevel.objects.create(
        issue=a_plain[1].issue,
        workspace=workspace_with_members,
        assignment_due_at=ago(days=1),
        source=ServiceLevelSource.PROCESS,
    )

    completed_proc = ProcessInstanceReference.objects.create(
        workspace=workspace_with_members,
        external_source="espo",
        external_instance_id="client-done",
        template_name="offboarding",
        template_version="1",
        status=ProcessInstanceStatus.COMPLETED,
        started_at=ago(days=20),
        completed_at=ago(days=5),
    )
    ProcessInstanceReference.objects.filter(pk=completed_proc.pk).update(
        started_at=ago(days=20), completed_at=ago(days=5)
    )
    ProcessInstanceItem.objects.create(
        process_instance=completed_proc,
        issue=a_c[0].issue,
        workspace=workspace_with_members,
        step_key="exit-interview",
    )

    hidden_issue_ids = {str(a_h1.issue_id), str(a_h2.issue_id), str(a_h3.issue_id), str(a_c[4].issue_id)}
    queued_visible_ids = {
        str(a_q1.issue_id),
        str(a_q2.issue_id),
        str(a_q3.issue_id),
        str(a_q4.issue_id),
        str(a_q5.issue_id),
    }

    assert IssueOrganizationalUnit.objects.filter(workspace=workspace_with_members).count() == 40

    return {
        "workspace": workspace_with_members,
        "compliance": unit,
        "legal": second_unit,
        "finance": finance_unit,
        "hidden_issue_ids": hidden_issue_ids,
        "queued_visible_ids": queued_visible_ids,
        "running_id": str(running.id),
        "completed_id": str(completed_proc.id),
        "h1_id": str(a_h1.issue_id),
    }


@pytest.mark.unit
@pytest.mark.django_db
class TestExecutiveEndpointAccess:
    def test_admin_receives_200(self, frozen_clock, world, admin_client):
        response = admin_client.get(executive_url(world["workspace"].slug))
        assert response.status_code == status.HTTP_200_OK, response.data
        assert len(response.data["units"]) == 3

    def test_member_receives_403(self, frozen_clock, world, member_client):
        response = member_client.get(executive_url(world["workspace"].slug))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_receives_403(self, frozen_clock, world, guest_client):
        response = guest_client.get(executive_url(world["workspace"].slug))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unknown_period_is_400(self, frozen_clock, world, admin_client):
        response = admin_client.get(executive_url(world["workspace"].slug, period="1y"))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_unit_is_404(self, frozen_clock, world, admin_client):
        response = admin_client.get(executive_url(world["workspace"].slug, unit=str(uuid4())))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_kill_switch_hides_the_route(self, frozen_clock, world, admin_client, settings):
        settings.ORCA_ORG_UNITS_ENABLED = False
        response = admin_client.get(executive_url(world["workspace"].slug))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
@pytest.mark.django_db
class TestExecutiveIndicators:
    def test_each_area_matches_the_hand_count(self, frozen_clock, world, admin_client):
        response = admin_client.get(executive_url(world["workspace"].slug, period="30d"))
        assert response.status_code == status.HTTP_200_OK, response.data
        a = _by_slug(response.data, "compliance")
        b = _by_slug(response.data, "legal")
        c = _by_slug(response.data, "finance")

        # Area A backlog: 5 queued P1 + 2 queued P2 + 8 assigned P1 + 1 assigned P2
        # = 16. Completed and the outside-window item are in a completed group.
        assert a["backlog"] == 16
        # queued: routing in (queued, allocation_failed) = 5 + 2 = 7
        assert a["queued"] == 7
        # assignment_overdue: Q2, Q4, Q5, H1 = 4 (Q1 future due, Q3/H2 no due)
        assert a["assignment_overdue"] == 4
        # target_overdue: Q4, A-P1, H1, H3 = 4 (open items with target < 2026-09-10)
        assert a["target_overdue"] == 4
        # queue ages (days): 1, 2, 3, 4, 5, 8, 16. p50 = 4th = 4d.
        # p90 index = 1+(7-1)*0.9 = 6.4 → 8 + 0.4*(16-8) = 11.2d.
        ages_a = [1 * DAY, 2 * DAY, 3 * DAY, 4 * DAY, 5 * DAY, 8 * DAY, 16 * DAY]
        assert a["queue_age_p50"] == pytest.approx(percentile_cont(ages_a, 0.5), abs=1)
        assert a["queue_age_p90"] == pytest.approx(percentile_cont(ages_a, 0.9), abs=1)
        # throughput: 4 on P1 + 1 on P2; the 40-day-old completion is out of window.
        assert a["throughput"] == 5
        # cycle times (days): 2, 3, 4, 6, 10. p50 = 3rd = 4d.
        # p90 index = 1+4*0.9 = 4.6 → 6 + 0.6*(10-6) = 8.4d.
        cycles_a = [2 * DAY, 3 * DAY, 4 * DAY, 6 * DAY, 10 * DAY]
        assert a["cycle_time_p50"] == pytest.approx(percentile_cont(cycles_a, 0.5), abs=1)
        assert a["cycle_time_p90"] == pytest.approx(percentile_cont(cycles_a, 0.9), abs=1)
        # concentration: plain 4, second 3, extra 1, guest 1 → top3 = 8/9.
        assert a["concentration_top3"] == pytest.approx(8 / 9)
        # auto_assign_kept_ratio: 10 least_loaded, 3 replaced → 7/10.
        assert a["auto_assign_kept_ratio"] == pytest.approx(0.7)
        # 4 live links on P2, which the admin is not a member of.
        assert a["hidden_count"] == 4
        assert sum(bar["count"] for bar in a["throughput_sparkline"]) == a["throughput"]

        # Area B backlog: 3 queued P1 + 4 assigned + 1 queued P2 = 8
        # (cancelled/suspended is cancelled-group, three completed are closed).
        assert b["backlog"] == 8
        assert b["queued"] == 4
        assert b["assignment_overdue"] == 1
        assert b["target_overdue"] == 0
        ages_b = [2 * DAY, 4 * DAY, 6 * DAY, 10 * DAY]
        assert b["queue_age_p50"] == pytest.approx(percentile_cont(ages_b, 0.5), abs=1)
        assert b["queue_age_p90"] == pytest.approx(percentile_cont(ages_b, 0.9), abs=1)
        assert b["throughput"] == 3
        # All three completions are 5 days (created 10d ago, done 5d ago).
        assert b["cycle_time_p50"] == pytest.approx(5 * DAY, abs=1)
        assert b["cycle_time_p90"] == pytest.approx(5 * DAY, abs=1)
        # Only plain holds the 4 open assigned items → 4/4 = 1.
        assert b["concentration_top3"] == pytest.approx(1.0)
        # No least_loaded in B → null, not 0.
        assert b["auto_assign_kept_ratio"] is None
        assert b["hidden_count"] == 1

        # Area C: 2 queued + 2 assigned = 4 backlog; 2 completed.
        assert c["backlog"] == 4
        assert c["queued"] == 2
        assert c["assignment_overdue"] == 0
        assert c["target_overdue"] == 0
        assert c["queue_age_p50"] == pytest.approx(1 * DAY, abs=1)
        assert c["queue_age_p90"] == pytest.approx(1 * DAY, abs=1)
        assert c["throughput"] == 2
        # Cycle times 1d and 9d. p50 index = 1.5 → (1+9)/2 = 5d.
        # p90 index = 1.9 → 1 + 0.9*8 = 8.2d.
        cycles_c = [1 * DAY, 9 * DAY]
        assert c["cycle_time_p50"] == pytest.approx(percentile_cont(cycles_c, 0.5), abs=1)
        assert c["cycle_time_p90"] == pytest.approx(percentile_cont(cycles_c, 0.9), abs=1)
        assert c["concentration_top3"] == pytest.approx(1.0)
        # 4 least_loaded, none replaced → 1.0.
        assert c["auto_assign_kept_ratio"] == pytest.approx(1.0)
        assert c["hidden_count"] == 0

    def test_process_aggregates(self, frozen_clock, world, admin_client):
        response = admin_client.get(executive_url(world["workspace"].slug))
        processes = response.data["processes"]
        # One running, one completed in the window.
        assert processes["running"] == 1
        assert processes["completed"] == 1
        # Lead time of the completed run: 20d - 5d = 15d. n=1 so p50 = p90.
        assert processes["lead_time_p50"] == pytest.approx(15 * DAY, abs=1)
        assert processes["lead_time_p90"] == pytest.approx(15 * DAY, abs=1)
        steps = processes["delayed_steps"]
        assert [row["step_key"] for row in steps] == ["kyc", "contract"]
        assert steps[0]["late_seconds"] == pytest.approx(2 * DAY, abs=1)
        assert steps[1]["late_seconds"] == pytest.approx(1 * DAY, abs=1)
        assert steps[0]["kind"] == "completion"
        assert steps[1]["kind"] == "assignment"

    def test_unit_filter_returns_one_area(self, frozen_clock, world, admin_client):
        response = admin_client.get(
            executive_url(world["workspace"].slug, period="30d", unit=str(world["compliance"].id))
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert [row["slug"] for row in response.data["units"]] == ["compliance"]

    def test_seven_day_window_drops_older_completions(self, frozen_clock, world, admin_client):
        response = admin_client.get(executive_url(world["workspace"].slug, period="7d"))
        a = _by_slug(response.data, "compliance")
        # Completions at now-1d, now-2d (hidden), now-5d stay; now-10d and now-20d drop.
        assert a["throughput"] == 3


@pytest.mark.unit
@pytest.mark.django_db
class TestExecutiveDrillDown:
    def test_queued_omits_issues_without_project_member(self, frozen_clock, world, admin_client):
        """
        The admin is a ProjectMember of P1 only. H1 and H2 live on P2, so
        they stay in the count and leave the list.
        """
        response = admin_client.get(
            drill_url(
                world["workspace"].slug,
                unit=str(world["compliance"].id),
                metric="queued",
                period="30d",
            )
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        ids = {row["issue_id"] for row in response.data["results"]}
        assert ids == world["queued_visible_ids"]
        assert world["h1_id"] not in ids
        assert ids.isdisjoint(world["hidden_issue_ids"])
        assert response.data["hidden_count"] == 2
        assert all(
            row["permissions"] == {"can_claim": False, "can_assign": False, "can_return": False}
            for row in response.data["results"]
        )

    def test_member_cannot_drill_down(self, frozen_clock, world, member_client):
        response = member_client.get(
            drill_url(world["workspace"].slug, unit=str(world["compliance"].id), metric="queued")
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_cannot_drill_down(self, frozen_clock, world, guest_client):
        response = guest_client.get(
            drill_url(world["workspace"].slug, unit=str(world["compliance"].id), metric="queued")
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_backlog_hidden_count_covers_inaccessible_projects(self, frozen_clock, world, admin_client):
        response = admin_client.get(
            drill_url(
                world["workspace"].slug,
                unit=str(world["compliance"].id),
                metric="backlog",
                period="30d",
            )
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        ids = {row["issue_id"] for row in response.data["results"]}
        assert ids.isdisjoint(world["hidden_issue_ids"])
        # Open hidden links on P2: H1, H2, H3 (the completed hidden item is closed).
        assert response.data["hidden_count"] == 3
