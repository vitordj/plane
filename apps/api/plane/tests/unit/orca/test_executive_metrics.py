# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The executive page, against a dataset small enough to check by hand.

Every expected value below is worked out in the test rather than read off the
implementation, because a metrics test that computes its expectation the same
way the code does proves only that the code is consistent with itself.

The dataset: three areas, and work with controlled dates. Where a date matters
it is written directly onto the row — Plane sets `created_at` with
`auto_now_add`, so the only way to have work that is a fortnight old is to say
so afterwards.
"""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from plane.app.services.orca import set_responsibility
from plane.app.services.orca.executive_metrics import (
    auto_assign_kept_ratio,
    concentration_top3,
    period_start,
    unit_metrics,
)
from plane.db.models import (
    Issue,
    IssueOrganizationalUnit,
    ProjectMember,
    State,
    StateGroup,
)
from .conftest import ROLE_MEMBER


@pytest.fixture(autouse=True)
def clear_metric_cache():
    """The summary is cached for five minutes; tests must not read each other's."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def in_area(covered_unit, project, workspace_with_members, add_member):
    def _add(user):
        add_member(covered_unit, user)
        ProjectMember.objects.get_or_create(
            project=project,
            member=user,
            defaults={"workspace": workspace_with_members, "role": ROLE_MEMBER, "is_active": True},
        )
        return user

    return _add


@pytest.fixture
def done_state(project, workspace_with_members):
    return State.objects.create(
        name="Done", group=StateGroup.COMPLETED.value, project=project, workspace=workspace_with_members, sequence=9
    )


@pytest.fixture
def cancelled_state(project, workspace_with_members):
    return State.objects.create(
        name="Dropped",
        group=StateGroup.CANCELLED.value,
        project=project,
        workspace=workspace_with_members,
        sequence=10,
    )


def age_the_queue(issue, *, seconds):
    """Say the work has been waiting this long."""
    IssueOrganizationalUnit.objects.filter(issue=issue).update(queued_at=timezone.now() - timedelta(seconds=seconds))


@pytest.mark.unit
@pytest.mark.django_db
class TestCounts:
    def test_open_excludes_finished_and_cancelled_work(
        self, covered_unit, project, make_issue, done_state, cancelled_state
    ):
        """A report that counted cancelled work as outstanding would make every
        tidy-up look like a problem."""
        waiting = make_issue(project)
        finished = make_issue(project)
        dropped = make_issue(project)
        for issue in (waiting, finished, dropped):
            set_responsibility(issue, covered_unit, trigger="internal_api")
        Issue.objects.filter(pk=finished.pk).update(state=done_state)
        Issue.objects.filter(pk=dropped.pk).update(state=cancelled_state)

        metrics = unit_metrics(covered_unit)

        assert metrics["backlog"] == 1
        assert metrics["queued"] == 1

    def test_assigned_work_is_open_but_not_waiting(self, covered_unit, project, make_issue, in_area, plain_user):
        executor = in_area(plain_user)
        set_responsibility(make_issue(project), covered_unit, explicit_executor=executor, trigger="internal_api")
        set_responsibility(make_issue(project), covered_unit, trigger="internal_api")

        metrics = unit_metrics(covered_unit)

        assert metrics["backlog"] == 2
        assert metrics["queued"] == 1

    def test_only_promised_work_can_be_late_to_be_taken(
        self, covered_unit, project, make_issue, workspace_with_members
    ):
        """Null `assignment_due_at` means the area promised nothing, and a
        promise nobody made cannot be broken."""
        promised = make_issue(project)
        unpromised = make_issue(project)
        for issue in (promised, unpromised):
            set_responsibility(issue, covered_unit, trigger="internal_api")
        IssueOrganizationalUnit.objects.filter(issue=promised).update(
            assignment_due_at=timezone.now() - timedelta(hours=2)
        )

        metrics = unit_metrics(covered_unit)

        assert metrics["assignment_overdue"] == 1

    def test_past_its_date_reads_the_work_items_own_date(self, covered_unit, project, make_issue):
        late = make_issue(project)
        soon = make_issue(project)
        for issue in (late, soon):
            set_responsibility(issue, covered_unit, trigger="internal_api")
        today = timezone.now().date()
        Issue.objects.filter(pk=late.pk).update(target_date=today - timedelta(days=1))
        Issue.objects.filter(pk=soon.pk).update(target_date=today + timedelta(days=7))

        assert unit_metrics(covered_unit)["target_overdue"] == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestPercentiles:
    def test_an_empty_queue_reports_nothing_rather_than_zero(self, covered_unit):
        """Zero would read as "instant", which is the opposite of the truth."""
        metrics = unit_metrics(covered_unit)

        assert metrics["queue_age"] == {"p50": None, "p90": None}

    def test_the_median_wait_is_the_middle_one(self, covered_unit, project, make_issue):
        for seconds in (60, 120, 180):
            issue = make_issue(project)
            set_responsibility(issue, covered_unit, trigger="internal_api")
            age_the_queue(issue, seconds=seconds)

        p50 = unit_metrics(covered_unit)["queue_age"]["p50"]

        # Three samples at 60, 120 and 180: the median is 120, give or take the
        # second that passes between arranging it and measuring it.
        assert 118 <= p50 <= 125

    def test_one_ancient_item_moves_p90_and_not_p50(self, covered_unit, project, make_issue):
        """Which is the whole reason for reporting percentiles rather than a
        mean: an average would hide a healthy queue behind one outlier."""
        for _ in range(9):
            issue = make_issue(project)
            set_responsibility(issue, covered_unit, trigger="internal_api")
            age_the_queue(issue, seconds=60)
        ancient = make_issue(project)
        set_responsibility(ancient, covered_unit, trigger="internal_api")
        age_the_queue(ancient, seconds=86400 * 90)

        queue_age = unit_metrics(covered_unit)["queue_age"]

        assert queue_age["p50"] < 120
        assert queue_age["p90"] > 3600


@pytest.mark.unit
@pytest.mark.django_db
class TestThroughputAndCycleTime:
    def test_only_work_finished_inside_the_window_counts(self, covered_unit, project, make_issue, done_state):
        recent = make_issue(project)
        old = make_issue(project)
        for issue in (recent, old):
            set_responsibility(issue, covered_unit, trigger="internal_api")
        now = timezone.now()
        Issue.objects.filter(pk=recent.pk).update(state=done_state, completed_at=now - timedelta(days=2))
        Issue.objects.filter(pk=old.pk).update(state=done_state, completed_at=now - timedelta(days=60))

        assert unit_metrics(covered_unit, period="7d")["throughput"] == 1
        assert unit_metrics(covered_unit, period="90d")["throughput"] == 2

    def test_cancelled_work_is_not_throughput(self, covered_unit, project, make_issue, cancelled_state):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")
        Issue.objects.filter(pk=issue.pk).update(state=cancelled_state, completed_at=timezone.now())

        assert unit_metrics(covered_unit, period="30d")["throughput"] == 0

    def test_cycle_time_is_creation_to_completion(self, covered_unit, project, make_issue, done_state):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")
        now = timezone.now()
        Issue.objects.filter(pk=issue.pk).update(
            state=done_state, created_at=now - timedelta(days=5), completed_at=now - timedelta(days=1)
        )

        cycle_time = unit_metrics(covered_unit, period="30d")["cycle_time"]

        # Four days, in seconds.
        assert abs(cycle_time["p50"] - 4 * 86400) < 120


@pytest.mark.unit
@pytest.mark.django_db
class TestConcentration:
    def test_an_area_of_three_is_always_everything(
        self, covered_unit, project, make_issue, in_area, plain_user, second_user, admin_user
    ):
        """Which is why the sample travels with the ratio: 100% here is not the
        finding it would be in an area of twelve."""
        for user in (plain_user, second_user, admin_user):
            set_responsibility(make_issue(project), covered_unit, explicit_executor=in_area(user), trigger="command")

        concentration = concentration_top3(covered_unit)

        assert concentration["ratio"] == 1.0
        assert concentration["executors"] == 3
        assert concentration["open_items"] == 3

    def test_nobody_carrying_anything_reports_nothing(self, covered_unit):
        assert concentration_top3(covered_unit) == {"ratio": None, "open_items": 0, "executors": 0}

    def test_finished_work_stops_counting_toward_it(
        self, covered_unit, project, make_issue, in_area, done_state, plain_user
    ):
        executor = in_area(plain_user)
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="command")
        Issue.objects.filter(pk=issue.pk).update(state=done_state)

        assert concentration_top3(covered_unit)["open_items"] == 0


@pytest.mark.unit
@pytest.mark.django_db
class TestAutoAssignKept:
    def test_no_automatic_decisions_reports_nothing_rather_than_a_hundred_percent(self, covered_unit):
        """A ratio over an empty sample is the most confidently wrong number a
        report can produce."""
        assert auto_assign_kept_ratio(covered_unit, period_start("30d")) == {
            "ratio": None,
            "decisions": 0,
            "kept": 0,
        }

    def test_an_overridden_decision_lowers_it(
        self, covered_unit, project, make_issue, in_area, workspace_with_members, plain_user, second_user
    ):
        from plane.app.services.orca import reassign
        from plane.db.models import OrganizationalUnitAssignmentPolicy

        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="least_loaded",
            allowed_modes=["least_loaded"],
        )
        in_area(plain_user)
        other = in_area(second_user)
        kept = make_issue(project)
        overridden = make_issue(project)
        for issue in (kept, overridden):
            set_responsibility(issue, covered_unit, requested_mode="least_loaded", trigger="internal_api")
        reassign(overridden, other.id, actor=other)

        result = auto_assign_kept_ratio(covered_unit, period_start("30d"))

        assert result["decisions"] == 2
        assert result["kept"] == 1
        assert result["ratio"] == 0.5


@pytest.mark.unit
@pytest.mark.django_db
class TestTheEndpoint:
    def url(self, workspace, period="30d"):
        return f"/api/orca/workspaces/{workspace.slug}/executive/?period={period}"

    def test_an_admin_sees_it(self, admin_client, workspace_with_members, covered_unit):
        response = admin_client.get(self.url(workspace_with_members))

        assert response.status_code == 200, response.data
        assert response.data["period"] == "30d"
        assert [row["unit"]["slug"] for row in response.data["units"]] == [covered_unit.slug]

    def test_a_member_does_not(self, member_client, workspace_with_members, covered_unit):
        """The aggregate is a management view (RFC F18); the parts are already
        visible to the people in each area."""
        response = member_client.get(self.url(workspace_with_members))

        assert response.status_code == 403

    def test_an_unknown_period_is_refused_rather_than_defaulted(self, admin_client, workspace_with_members):
        """A typo that silently reported a different window is worse than an
        error, because the number still looks right."""
        response = admin_client.get(self.url(workspace_with_members, period="ever"))

        assert response.status_code == 400
        assert response.data["error_code"] == 4934

    def test_the_route_is_gone_while_the_layer_is_off(self, admin_client, workspace_with_members, settings):
        settings.ORCA_ORG_UNITS_ENABLED = False

        assert admin_client.get(self.url(workspace_with_members)).status_code == 404

    def test_the_summary_is_cached_for_the_same_question(
        self, admin_client, workspace_with_members, covered_unit, project, make_issue
    ):
        first = admin_client.get(self.url(workspace_with_members)).data
        set_responsibility(make_issue(project), covered_unit, trigger="internal_api")

        second = admin_client.get(self.url(workspace_with_members)).data

        # Five minutes stale is the deal: the page is for deciding something
        # this week, and recomputing percentiles on every reload is not free.
        assert second["units"][0]["backlog"] == first["units"][0]["backlog"]

    def test_a_different_period_is_a_different_question(
        self, admin_client, workspace_with_members, covered_unit, project, make_issue
    ):
        admin_client.get(self.url(workspace_with_members, period="30d"))
        set_responsibility(make_issue(project), covered_unit, trigger="internal_api")

        response = admin_client.get(self.url(workspace_with_members, period="7d"))

        assert response.data["units"][0]["backlog"] == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestThreeAreasSideBySide:
    def test_each_area_reports_only_its_own_work(
        self,
        admin_client,
        workspace_with_members,
        unit,
        second_unit,
        project,
        second_project,
        link_project,
        make_issue,
    ):
        link_project(unit, project)
        link_project(second_unit, second_project)
        for _ in range(3):
            set_responsibility(make_issue(project), unit, trigger="internal_api")
        set_responsibility(make_issue(second_project), second_unit, trigger="internal_api")

        response = admin_client.get(f"/api/orca/workspaces/{workspace_with_members.slug}/executive/")

        by_slug = {row["unit"]["slug"]: row for row in response.data["units"]}
        assert by_slug[unit.slug]["backlog"] == 3
        assert by_slug[second_unit.slug]["backlog"] == 1
        assert by_slug[unit.slug]["queued"] == 3
