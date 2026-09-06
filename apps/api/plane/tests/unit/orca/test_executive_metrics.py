# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The executive view over a dataset small enough to check by hand (item 5.4).

Every expected number below was worked out on paper from the fixture and is
written as a literal with its arithmetic in a comment. That is the point of the
file: a dashboard's failure mode is not an exception, it is a plausible wrong
number, and the only defence is a population somebody can count.

The percentiles use ``percentile_cont``'s linear interpolation, so the
expectations show it: over ``[600, 1200, 1800, 3600]`` the median is not 1200,
it is 1500, and a test asserting 1200 would be asserting the wrong definition
rather than catching a bug.
"""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status

from plane.app.services.orca.executive_metrics import executive_metrics
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionOutcome,
    DecisionTrigger,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    PolicySource,
    ProcessInstanceItem,
    ProcessInstanceReference,
    RoutingState,
    StateGroup,
)

from .conftest import ROLE_MEMBER

# The instant the whole dataset is built around and every number judged
# against. Fixed, because "now" moving between the fixture and the assertion is
# the classic way a metrics test starts failing at midnight.
NOW = timezone.now().replace(microsecond=0)


def executive_url(slug):
    return f"/api/orca/workspaces/{slug}/executive/"


def drilldown_url(slug):
    return f"/api/orca/workspaces/{slug}/executive/drilldown/"


@pytest.fixture(autouse=True)
def clear_metric_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def third_unit(db, workspace_with_members):
    return OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Finance", slug="finance")


@pytest.fixture
def place(workspace_with_members, make_issue):
    """
    Put one work item in an area with a known age, deadline and history.

    Dates that Django maintains itself (``created_at``, ``completed_at``) are
    written with ``update`` rather than ``save``: the point is to control them,
    and ``Issue.save`` would helpfully overwrite ``completed_at`` with the
    present.
    """

    def _place(
        unit,
        project,
        *,
        state_group=StateGroup.UNSTARTED.value,
        routing_state=RoutingState.QUEUED,
        queued_seconds_ago=None,
        assignment_due_in=None,
        executor=None,
        target_date=None,
        created_days_ago=None,
        completed_days_ago=None,
        name="Work item",
    ):
        issue = make_issue(project, name=name, state_group=state_group)
        updates = {}
        if created_days_ago is not None:
            updates["created_at"] = NOW - timedelta(days=created_days_ago)
        if completed_days_ago is not None:
            updates["completed_at"] = NOW - timedelta(days=completed_days_ago)
        elif state_group != StateGroup.COMPLETED.value:
            updates["completed_at"] = None
        if target_date is not None:
            updates["target_date"] = target_date
        if updates:
            Issue.objects.filter(id=issue.id).update(**updates)

        return IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            routing_state=routing_state,
            queued_at=(NOW - timedelta(seconds=queued_seconds_ago) if queued_seconds_ago is not None else None),
            assignment_due_at=(NOW + assignment_due_in if assignment_due_in is not None else None),
            primary_executor=executor,
        )

    return _place


@pytest.fixture
def decision(workspace_with_members, project, make_issue):
    """One recorded allocation, so the kept-ratio has something to count."""

    def _decide(unit, *, made_by=None, supersedes=None, issue=None, mode=AssignmentMode.LEAST_LOADED):
        decided = AssignmentDecision.objects.create(
            issue=issue or (supersedes.issue if supersedes is not None else make_issue(project, name="decided")),
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            trigger=DecisionTrigger.PUBLIC_API,
            effective_mode=mode,
            policy_source=PolicySource.FALLBACK,
            outcome=DecisionOutcome.ASSIGNED,
            decided_by=made_by,
            supersedes=supersedes,
        )
        # Backdated so the window is the fixture's and not the wall clock's:
        # ``created_at`` is ``auto_now_add``, and a decision stamped after
        # ``NOW`` falls outside every period this file asks about.
        AssignmentDecision.objects.filter(id=decided.id).update(created_at=NOW - timedelta(days=1))
        decided.refresh_from_db()
        return decided

    return _decide


@pytest.fixture
def dataset(
    settings,
    workspace_with_members,
    unit,
    second_unit,
    third_unit,
    project,
    second_project,
    link_project,
    place,
    decision,
    admin_user,
    plain_user,
    second_user,
    guest_user,
    make_issue,
):
    """
    Three areas, two processes, and enough items to make every indicator
    non-trivial — 3 areas × (queue, assigned work, finished work) plus two runs.
    """
    settings.ORCA_ORG_UNITS_ENABLED = True
    link_project(unit, project, ROLE_MEMBER)
    link_project(second_unit, project, ROLE_MEMBER)

    # --- Compliance: the area with everything ---------------------------------
    # Queue: four waiting items aged 600, 1200, 1800 and 3600 seconds. One is
    # allocation_failed (still waiting for a person), one is past its
    # assignment deadline.
    place(unit, project, queued_seconds_ago=600, name="q-10min")
    place(
        unit,
        project,
        queued_seconds_ago=1200,
        assignment_due_in=-timedelta(minutes=5),
        name="q-20min-overdue",
    )
    place(
        unit,
        project,
        routing_state=RoutingState.ALLOCATION_FAILED,
        queued_seconds_ago=1800,
        name="q-30min-failed",
    )
    place(
        unit,
        project,
        queued_seconds_ago=3600,
        target_date=(NOW - timedelta(days=1)).date(),
        name="q-60min-late",
    )

    # Assigned and open: seven items across four people, 3/2/1/1.
    for index, (holder, count) in enumerate(((admin_user, 3), (plain_user, 2), (second_user, 1), (guest_user, 1))):
        for repeat in range(count):
            place(
                unit,
                project,
                state_group=StateGroup.STARTED.value,
                routing_state=RoutingState.ASSIGNED,
                executor=holder,
                target_date=((NOW - timedelta(days=1)).date() if index == 0 and repeat == 0 else None),
                name=f"assigned-{index}-{repeat}",
            )

    # Finished inside the window: cycle times of 10 days and 1 day.
    place(
        unit,
        project,
        state_group=StateGroup.COMPLETED.value,
        routing_state=RoutingState.ASSIGNED,
        executor=admin_user,
        created_days_ago=12,
        completed_days_ago=2,
        name="done-10d",
    )
    place(
        unit,
        project,
        state_group=StateGroup.COMPLETED.value,
        routing_state=RoutingState.ASSIGNED,
        executor=plain_user,
        created_days_ago=6,
        completed_days_ago=5,
        name="done-1d",
    )

    # Four ranked allocations in the window. One is overturned by a person, one
    # by the availability sweep — and only the first counts against the ranking.
    kept_one = decision(unit)
    decision(unit)
    overturned_by_person = decision(unit)
    returned_by_sweep = decision(unit)
    decision(unit, made_by=admin_user, supersedes=overturned_by_person, mode=AssignmentMode.MANUAL)
    decision(unit, supersedes=returned_by_sweep, mode=AssignmentMode.MANUAL)
    assert kept_one is not None

    # --- Legal: one waiting item, nothing else --------------------------------
    place(second_unit, project, queued_seconds_ago=900, name="legal-15min")

    # --- Finance: an area that exists and holds nothing ------------------------
    # (third_unit is created and linked to nothing on purpose.)

    # --- Two process runs -----------------------------------------------------
    finished_run = ProcessInstanceReference.objects.create(
        workspace=workspace_with_members,
        external_source="orchestrator",
        external_instance_id="run-done",
        template_name="onboarding",
        template_version="3",
        started_at=NOW - timedelta(days=10),
        completed_at=NOW - timedelta(days=3),
    )
    running_run = ProcessInstanceReference.objects.create(
        workspace=workspace_with_members,
        external_source="orchestrator",
        external_instance_id="run-live",
        template_name="onboarding",
        template_version="3",
        started_at=NOW - timedelta(days=2),
    )
    late_link = place(
        unit,
        project,
        queued_seconds_ago=1200,
        assignment_due_in=-timedelta(hours=2),
        name="late-kyc-step",
    )
    ProcessInstanceItem.objects.create(
        issue=late_link.issue,
        process_instance=running_run,
        workspace=workspace_with_members,
        step_key="kyc",
    )
    return {
        "finished_run": finished_run,
        "running_run": running_run,
        "hidden_project": second_project,
    }


def unit_row(payload, slug):
    return next(row for row in payload["units"] if row["unit"]["slug"] == slug)


@pytest.mark.unit
@pytest.mark.django_db
class TestTheNumbersThemselves:
    """Each indicator against a population counted by hand."""

    def test_the_counts_of_an_area_that_holds_work(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)
        row = unit_row(payload, "compliance")

        # 4 waiting + 7 assigned-and-open + 1 late process step = 12 open.
        # The two completed ones are not backlog, whoever closed them.
        assert row["backlog"] == 12
        # queued + allocation_failed, all still open.
        assert row["queued"] == 5
        assert row["allocation_failed"] == 1
        # Two waiting items are past their assignment deadline: the 20-minute
        # one and the late process step.
        assert row["assignment_overdue"] == 2
        # One waiting item and one assigned item carry yesterday's target date.
        assert row["target_overdue"] == 2
        assert row["assigned_open"] == 7

    def test_queue_age_percentiles_interpolate(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)
        row = unit_row(payload, "compliance")

        # Ages, in seconds: [600, 1200, 1200, 1800, 3600] (the late process
        # step waited 1200 too).
        # p50 -> index 0.5*(5-1) = 2 -> exactly 1200.
        # p90 -> index 0.9*(5-1) = 3.6 -> 1800 + 0.6*(3600-1800) = 2880.
        assert row["queue_age_p50"] == 1200
        assert row["queue_age_p90"] == 2880

    def test_throughput_and_cycle_time_over_the_window(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)
        row = unit_row(payload, "compliance")

        assert row["throughput"] == 2
        # Cycle times: 10 days (864000s) and 1 day (86400s).
        # p50 -> 86400 + 0.5*(864000-86400) = 475200.
        # p90 -> 86400 + 0.9*(864000-86400) = 786240.
        assert row["cycle_time_p50"] == 475200
        assert row["cycle_time_p90"] == 786240

    def test_a_shorter_window_excludes_what_finished_before_it(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="7d", now=NOW, use_cache=False)
        row = unit_row(payload, "compliance")

        # Both finished inside seven days, so this is not a trick: what the
        # window changes is the population, and it must change the number.
        assert row["throughput"] == 2

        payload_1d = executive_metrics(
            workspace_with_members, period="30d", now=NOW - timedelta(days=4), use_cache=False
        )
        # Four days earlier, the item completed two days ago has not happened
        # yet: one finished item, and the cycle time is that one's alone.
        assert unit_row(payload_1d, "compliance")["throughput"] == 1
        assert unit_row(payload_1d, "compliance")["cycle_time_p50"] == 86400

    def test_concentration_is_the_share_the_top_three_carry(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)

        # Open assigned work: 3 + 2 + 1 + 1 = 7 across four people.
        # Top three carry 3 + 2 + 1 = 6. 6/7 = 0.857142...
        assert unit_row(payload, "compliance")["concentration_top3"] == 0.8571

    def test_an_area_holding_nothing_reports_none_rather_than_zero(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)
        finance = unit_row(payload, "finance")

        assert finance["backlog"] == 0
        # A percentile of nothing, a share of nothing and a ratio of nothing are
        # not zero. Printing 0% for an empty area would read as "perfectly
        # distributed" and 0s as "instant".
        assert finance["queue_age_p50"] is None
        assert finance["cycle_time_p90"] is None
        assert finance["concentration_top3"] is None
        assert finance["auto_assign_kept_ratio"] is None

    def test_the_kept_ratio_ignores_what_the_sweep_returned(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)

        # Four ranked allocations, one overturned by a person. The one the
        # availability sweep returned is not the ranking being wrong — counting
        # it would make this number worse every time absences did their job.
        assert unit_row(payload, "compliance")["auto_assign_kept_ratio"] == 0.75

    def test_every_active_area_appears_even_when_empty(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)

        assert [row["unit"]["slug"] for row in payload["units"]] == ["compliance", "finance", "legal"]

    def test_one_area_can_be_asked_for_alone(self, dataset, workspace_with_members, second_unit):
        payload = executive_metrics(
            workspace_with_members, period="30d", unit_id=str(second_unit.id), now=NOW, use_cache=False
        )

        assert len(payload["units"]) == 1
        assert payload["units"][0]["queued"] == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestTheProcessBlock:
    def test_runs_and_lead_time(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)

        assert payload["processes"]["running"] == 1
        assert payload["processes"]["completed"] == 1
        # One completed run: started 10 days ago, finished 3 days ago -> 7 days.
        assert payload["processes"]["lead_time_p50"] == 604800
        assert payload["processes"]["lead_time_p90"] == 604800

    def test_late_steps_are_grouped_by_step_not_by_instance(self, dataset, workspace_with_members):
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)

        assert payload["processes"]["late_steps"] == [
            {"step_key": "kyc", "template_name": "onboarding", "late_count": 1}
        ]

    def test_a_workspace_with_no_processes_still_answers(self, settings, other_workspace):
        settings.ORCA_ORG_UNITS_ENABLED = True
        payload = executive_metrics(other_workspace, period="30d", now=NOW, use_cache=False)

        assert payload["processes"]["running"] == 0
        assert payload["processes"]["lead_time_p50"] is None
        assert payload["processes"]["late_steps"] == []


@pytest.mark.unit
@pytest.mark.django_db
class TestTheCache:
    def test_a_second_read_inside_five_minutes_is_the_first_answer(
        self, dataset, workspace_with_members, unit, project, place
    ):
        first = executive_metrics(workspace_with_members, period="30d", now=NOW)
        place(unit, project, queued_seconds_ago=60, name="arrived-after")
        second = executive_metrics(workspace_with_members, period="30d", now=NOW)

        assert second["generated_at"] == first["generated_at"]
        assert unit_row(second, "compliance")["queued"] == unit_row(first, "compliance")["queued"]

    def test_reading_through_the_cache_sees_the_new_item(self, dataset, workspace_with_members, unit, project, place):
        first = executive_metrics(workspace_with_members, period="30d", now=NOW)
        place(unit, project, queued_seconds_ago=60, name="arrived-after")
        fresh = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)

        assert unit_row(fresh, "compliance")["queued"] == unit_row(first, "compliance")["queued"] + 1

    def test_periods_are_cached_apart(self, dataset, workspace_with_members):
        thirty = executive_metrics(workspace_with_members, period="30d", now=NOW)
        seven = executive_metrics(workspace_with_members, period="7d", now=NOW)

        assert thirty["period"] == "30d"
        assert seven["period"] == "7d"


@pytest.mark.unit
@pytest.mark.django_db
class TestWhoMayRead:
    def test_a_workspace_admin_reads_it(self, dataset, admin_client, workspace_with_members):
        response = admin_client.get(executive_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_200_OK
        assert unit_row(response.data, "compliance")["backlog"] == 12

    def test_a_member_does_not(self, dataset, member_client, workspace_with_members):
        response = member_client.get(executive_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_kill_switch_closes_it(self, settings, dataset, admin_client, workspace_with_members):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.get(executive_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unknown_period_is_refused_rather_than_guessed(self, dataset, admin_client, workspace_with_members):
        response = admin_client.get(executive_url(workspace_with_members.slug), {"period": "1y"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.unit
@pytest.mark.django_db
class TestTheDrilldown:
    def test_it_returns_the_rows_behind_the_number(
        self, dataset, admin_client, workspace_with_members, unit, project, grant_manual_access, admin_user
    ):
        grant_manual_access(project, admin_user)

        response = admin_client.get(
            drilldown_url(workspace_with_members.slug), {"unit": str(unit.id), "metric": "queued"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["total"] == 5
        assert response.data["hidden"] == 0
        assert len(response.data["items"]) == 5

    def test_items_in_projects_the_reader_cannot_see_are_counted_and_withheld(
        self,
        dataset,
        admin_client,
        workspace_with_members,
        unit,
        project,
        second_project,
        link_project,
        place,
        grant_manual_access,
        admin_user,
    ):
        # The reader belongs to one project of the two the area covers.
        grant_manual_access(project, admin_user)
        link_project(unit, second_project, ROLE_MEMBER)
        place(unit, second_project, queued_seconds_ago=300, name="invisible")
        place(unit, second_project, queued_seconds_ago=300, name="invisible-too")

        response = admin_client.get(
            drilldown_url(workspace_with_members.slug), {"unit": str(unit.id), "metric": "queued"}
        )

        # The count is the area's; the rows are the reader's.
        assert response.data["total"] == 7
        assert response.data["hidden"] == 2
        assert len(response.data["items"]) == 5
        assert all("invisible" not in item["name"] for item in response.data["items"])

    def test_the_drilldown_matches_the_aggregate_it_came_from(
        self, dataset, admin_client, workspace_with_members, unit, project, grant_manual_access, admin_user
    ):
        grant_manual_access(project, admin_user)
        payload = executive_metrics(workspace_with_members, period="30d", now=NOW, use_cache=False)

        for metric in ("backlog", "queued", "allocation_failed", "assignment_overdue", "target_overdue"):
            response = admin_client.get(
                drilldown_url(workspace_with_members.slug), {"unit": str(unit.id), "metric": metric}
            )
            # The two are computed by different code paths on purpose; if they
            # ever disagree, the page is lying about one of them.
            assert response.data["total"] == unit_row(payload, "compliance")[metric], metric

    def test_a_percentile_has_no_rows_to_open(self, dataset, admin_client, workspace_with_members, unit):
        response = admin_client.get(
            drilldown_url(workspace_with_members.slug), {"unit": str(unit.id), "metric": "queue_age_p50"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_member_does_not_reach_the_drilldown_either(self, dataset, member_client, workspace_with_members, unit):
        response = member_client.get(
            drilldown_url(workspace_with_members.slug), {"unit": str(unit.id), "metric": "queued"}
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
