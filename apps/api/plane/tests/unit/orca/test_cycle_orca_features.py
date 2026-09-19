# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for the Orca cycle customizations.

The fork changes four things about cycles, none of which upstream tests touch:

* a cycle may be created with a ``start_date`` and no ``end_date``, so it can be
  started manually and run open-ended;
* ``status`` is derived from ``ProjectCustomSettings.cycle_auto_complete`` and,
  when that is off, from ``view_props["completed"]`` rather than from the end
  date passing;
* ``manually_completed`` on a write is what sets ``view_props["completed"]``;
* ``set_in_progress`` / ``mark_completed`` on a ``PATCH`` bulk-move the work
  items in the cycle between state groups.

Everything runs against a real database through the real endpoint: the bulk
moves are ``QuerySet.update()`` calls whose whole risk is *which rows they
match*, which a mocked ORM cannot answer.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from plane.db.models import (
    Cycle,
    CycleIssue,
    Issue,
    ProjectCustomSettings,
    ProjectMember,
    State,
)

from .conftest import ROLE_ADMIN

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    """The cycle endpoints are project-scoped, so the caller needs a project role."""
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def states(project, workspace_with_members):
    """
    One state per group, plus a second ``started`` and ``completed`` state.

    The view picks the target state by ``sequence``, so the duplicates are what
    prove it picks the first one rather than an arbitrary row.
    """

    def _state(name, group, sequence):
        return State.objects.create(
            name=name,
            group=group,
            sequence=sequence,
            color="#000000",
            project=project,
            workspace=workspace_with_members,
        )

    return {
        "backlog": _state("Backlog", "backlog", 10),
        "unstarted": _state("Todo", "unstarted", 20),
        "started": _state("In Progress", "started", 30),
        "started_late": _state("In Review", "started", 40),
        "completed": _state("Done", "completed", 50),
        "completed_late": _state("Shipped", "completed", 60),
        "cancelled": _state("Cancelled", "cancelled", 70),
        "triage": _state("Triage", "triage", 80),
    }


@pytest.fixture
def make_cycle(project, workspace_with_members, admin_user):
    def _make(name="Sprint 1", start_date=None, end_date=None, view_props=None, project_override=None):
        return Cycle.objects.create(
            name=name,
            project=project_override or project,
            workspace=workspace_with_members,
            owned_by=admin_user,
            start_date=start_date,
            end_date=end_date,
            view_props=view_props or {},
        )

    return _make


@pytest.fixture
def make_cycle_issue(project, workspace_with_members, admin_user):
    """Create a work item in a given state and put it in a cycle."""

    def _make(cycle, state, name="Work item", is_draft=False, archived=False, project_override=None):
        target_project = project_override or project
        issue = Issue.objects.create(
            name=name,
            project=target_project,
            workspace=workspace_with_members,
            state=state,
            created_by=admin_user,
            is_draft=is_draft,
            archived_at=timezone.now() if archived else None,
        )
        cycle_issue = CycleIssue.objects.create(
            issue=issue,
            cycle=cycle,
            project=target_project,
            workspace=workspace_with_members,
            created_by=admin_user,
        )
        return issue, cycle_issue

    return _make


def cycles_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/cycles/"


def cycle_url(slug, project_id, cycle_id):
    return f"{cycles_url(slug, project_id)}{cycle_id}/"


def state_group_of(issue):
    issue.refresh_from_db()
    return issue.state.group


# --- create: a cycle may start without an end date ---------------------------


@pytest.mark.contract
class TestCycleCreateDateRules:
    """
    Upstream required both dates or neither. The fork allows a start date on its
    own so a cycle can be started manually and run until someone ends it.
    """

    def test_start_date_alone_is_accepted(self, admin_client, workspace_with_members, project, project_admin):
        response = admin_client.post(
            cycles_url(workspace_with_members.slug, project.id),
            {"name": "Open ended", "start_date": "2026-01-01"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        cycle = Cycle.objects.get(id=response.data["id"])
        assert cycle.start_date is not None
        assert cycle.end_date is None

    def test_end_date_without_start_date_is_rejected(
        self, admin_client, workspace_with_members, project, project_admin
    ):
        response = admin_client.post(
            cycles_url(workspace_with_members.slug, project.id),
            {"name": "Dangling end", "end_date": "2026-01-31"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"error": "A start date is required when an end date is provided"}
        assert not Cycle.objects.filter(name="Dangling end").exists()

    def test_both_dates_are_still_accepted(self, admin_client, workspace_with_members, project, project_admin):
        response = admin_client.post(
            cycles_url(workspace_with_members.slug, project.id),
            {"name": "Bounded", "start_date": "2026-01-01", "end_date": "2026-01-31"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        cycle = Cycle.objects.get(id=response.data["id"])
        assert cycle.start_date is not None
        assert cycle.end_date is not None

    def test_neither_date_is_still_accepted(self, admin_client, workspace_with_members, project, project_admin):
        response = admin_client.post(
            cycles_url(workspace_with_members.slug, project.id),
            {"name": "Draft"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data


# --- status: derived from the project's auto-complete setting ----------------


@pytest.mark.contract
class TestCycleStatusAnnotation:
    """
    ``status`` is annotated in ``get_queryset``, so every read goes through it.
    The five branches are exercised here through ``list``.
    """

    def _statuses(self, client, workspace, project):
        response = client.get(cycles_url(workspace.slug, project.id))
        assert response.status_code == status.HTTP_200_OK, response.data
        return {row["name"]: row["status"] for row in response.data}

    @pytest.fixture
    def auto_complete_on(self, project):
        return ProjectCustomSettings.objects.create(project=project, cycle_auto_complete=True)

    def test_no_dates_is_draft(self, admin_client, workspace_with_members, project, project_admin, make_cycle):
        make_cycle(name="Draft cycle")

        assert self._statuses(admin_client, workspace_with_members, project)["Draft cycle"] == "DRAFT"

    def test_future_start_is_upcoming(self, admin_client, workspace_with_members, project, project_admin, make_cycle):
        now = timezone.now()
        make_cycle(name="Later", start_date=now + timedelta(days=7), end_date=now + timedelta(days=14))

        assert self._statuses(admin_client, workspace_with_members, project)["Later"] == "UPCOMING"

    def test_started_with_no_end_date_is_current(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        """The open-ended cycle the fork exists to support."""
        make_cycle(name="Open ended", start_date=timezone.now() - timedelta(days=1))

        assert self._statuses(admin_client, workspace_with_members, project)["Open ended"] == "CURRENT"

    def test_auto_complete_on_and_end_date_passed_is_completed(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle, auto_complete_on
    ):
        now = timezone.now()
        make_cycle(name="Expired", start_date=now - timedelta(days=14), end_date=now - timedelta(days=1))

        assert self._statuses(admin_client, workspace_with_members, project)["Expired"] == "COMPLETED"

    def test_auto_complete_off_and_end_date_passed_stays_current(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        """
        With auto-complete off, an expired cycle is *not* finished — someone has
        to end it. This is the branch the fork added; upstream would call it
        completed the moment the end date passed.
        """
        now = timezone.now()
        make_cycle(name="Expired but open", start_date=now - timedelta(days=14), end_date=now - timedelta(days=1))

        assert self._statuses(admin_client, workspace_with_members, project)["Expired but open"] == "CURRENT"

    def test_auto_complete_off_and_marked_completed_is_completed(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        now = timezone.now()
        make_cycle(
            name="Ended by hand",
            start_date=now - timedelta(days=14),
            end_date=now - timedelta(days=1),
            view_props={"completed": True},
        )

        assert self._statuses(admin_client, workspace_with_members, project)["Ended by hand"] == "COMPLETED"

    def test_auto_complete_on_ignores_the_manual_flag_before_the_end_date(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle, auto_complete_on
    ):
        """
        With auto-complete on, the end date is the only authority: a cycle still
        running is CURRENT even if ``view_props`` carries a stale completed flag.
        """
        now = timezone.now()
        make_cycle(
            name="Still running",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=7),
            view_props={"completed": True},
        )

        assert self._statuses(admin_client, workspace_with_members, project)["Still running"] == "CURRENT"


# --- list?cycle_view=current -------------------------------------------------


@pytest.mark.contract
class TestCurrentCycleView:
    def _names(self, response):
        return {row["name"] for row in response.data}

    def test_current_view_includes_an_open_ended_cycle(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        """
        The fork's reason for the override: a manually started cycle has no end
        date, and the upstream ``start <= now <= end`` window would drop it.
        """
        make_cycle(name="Open ended", start_date=timezone.now() - timedelta(days=1))

        response = admin_client.get(cycles_url(workspace_with_members.slug, project.id), {"cycle_view": "current"})

        assert response.status_code == status.HTTP_200_OK, response.data
        assert self._names(response) == {"Open ended"}

    def test_current_view_includes_a_bounded_running_cycle(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        now = timezone.now()
        make_cycle(name="Running", start_date=now - timedelta(days=1), end_date=now + timedelta(days=7))

        response = admin_client.get(cycles_url(workspace_with_members.slug, project.id), {"cycle_view": "current"})

        assert self._names(response) == {"Running"}

    def test_current_view_excludes_a_cycle_that_has_not_started(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        now = timezone.now()
        make_cycle(name="Running", start_date=now - timedelta(days=1), end_date=now + timedelta(days=7))
        make_cycle(name="Later", start_date=now + timedelta(days=7), end_date=now + timedelta(days=14))

        response = admin_client.get(cycles_url(workspace_with_members.slug, project.id), {"cycle_view": "current"})

        assert self._names(response) == {"Running"}

    def test_current_view_is_empty_when_nothing_is_running(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        """
        The widened filter must not widen the empty case: with only an upcoming
        cycle in the project, the current view answers with nothing rather than
        falling through to the unfiltered list.
        """
        now = timezone.now()
        make_cycle(name="Later", start_date=now + timedelta(days=7), end_date=now + timedelta(days=14))

        response = admin_client.get(cycles_url(workspace_with_members.slug, project.id), {"cycle_view": "current"})

        assert response.status_code == status.HTTP_200_OK
        assert self._names(response) == set()


# --- PATCH set_in_progress ---------------------------------------------------


@pytest.mark.contract
class TestSetInProgress:
    """Starting a cycle can drag its not-yet-started work items into progress."""

    def test_moves_backlog_and_unstarted_work_items(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        backlog, _ = make_cycle_issue(cycle, states["backlog"], name="From backlog")
        unstarted, _ = make_cycle_issue(cycle, states["unstarted"], name="From todo")

        response = admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"start_date": "2026-01-01", "set_in_progress": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        backlog.refresh_from_db()
        unstarted.refresh_from_db()
        assert backlog.state_id == states["started"].id
        assert unstarted.state_id == states["started"].id

    def test_picks_the_first_started_state_by_sequence(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        issue, _ = make_cycle_issue(cycle, states["backlog"])

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        issue.refresh_from_db()
        assert issue.state_id == states["started"].id, "should take In Progress (sequence 30), not In Review (40)"

    def test_leaves_work_already_underway_or_finished_alone(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        in_review, _ = make_cycle_issue(cycle, states["started_late"], name="In review")
        done, _ = make_cycle_issue(cycle, states["completed"], name="Done")
        cancelled, _ = make_cycle_issue(cycle, states["cancelled"], name="Cancelled")

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        for issue in (in_review, done, cancelled):
            issue.refresh_from_db()
        assert in_review.state_id == states["started_late"].id, "work already started must keep its own state"
        assert done.state_id == states["completed"].id
        assert cancelled.state_id == states["cancelled"].id

    def test_does_nothing_without_the_flag(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        issue, _ = make_cycle_issue(cycle, states["backlog"])

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"name": "Renamed"},
            format="json",
        )

        assert state_group_of(issue) == "backlog"

    def test_is_a_no_op_when_the_project_has_no_started_state(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle, make_cycle_issue
    ):
        """No ``started`` state must mean "leave everything alone", not a 500."""
        backlog = State.objects.create(
            name="Backlog",
            group="backlog",
            sequence=10,
            color="#000000",
            project=project,
            workspace=workspace_with_members,
        )
        cycle = make_cycle()
        issue, _ = make_cycle_issue(cycle, backlog)

        response = admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert state_group_of(issue) == "backlog"

    def test_does_not_touch_another_cycle(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        started_cycle = make_cycle(name="Starting")
        other_cycle = make_cycle(name="Untouched")
        mine, _ = make_cycle_issue(started_cycle, states["backlog"], name="Mine")
        theirs, _ = make_cycle_issue(other_cycle, states["backlog"], name="Theirs")

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, started_cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        assert state_group_of(mine) == "started"
        assert state_group_of(theirs) == "backlog"

    def test_does_not_touch_a_work_item_removed_from_the_cycle(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        """
        Removing a work item from a cycle soft-deletes the ``CycleIssue`` row.
        The counts the cycle reports already ignore those rows, so the bulk move
        has to ignore them too — otherwise starting a cycle rewrites the state of
        work that was deliberately taken out of it.
        """
        cycle = make_cycle()
        removed, link = make_cycle_issue(cycle, states["backlog"], name="Taken out")
        link.delete(soft=True)

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        assert state_group_of(removed) == "backlog"

    def test_does_not_touch_an_archived_work_item(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        """Archived work is out of the cycle's own counts; it is out of the move too."""
        cycle = make_cycle()
        archived, _ = make_cycle_issue(cycle, states["backlog"], name="Archived", archived=True)

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        assert state_group_of(archived) == "backlog"

    def test_does_not_touch_a_draft_work_item(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        draft, _ = make_cycle_issue(cycle, states["backlog"], name="Draft", is_draft=True)

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        assert state_group_of(draft) == "backlog"

    def test_does_not_touch_an_intake_work_item(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        """Triage items belong to Intake and are not part of the cycle's board."""
        cycle = make_cycle()
        triaged, _ = make_cycle_issue(cycle, states["triage"], name="Awaiting triage")

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True},
            format="json",
        )

        assert state_group_of(triaged) == "triage"


# --- PATCH mark_completed ----------------------------------------------------


@pytest.mark.contract
class TestMarkCompleted:
    """Ending a cycle can close out whatever work is still open in it."""

    def test_moves_every_unfinished_work_item(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        backlog, _ = make_cycle_issue(cycle, states["backlog"], name="Backlog")
        unstarted, _ = make_cycle_issue(cycle, states["unstarted"], name="Todo")
        started, _ = make_cycle_issue(cycle, states["started"], name="Doing")

        response = admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"end_date": "2026-01-31", "mark_completed": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        for issue in (backlog, unstarted, started):
            issue.refresh_from_db()
            assert issue.state_id == states["completed"].id, f"{issue.name} was not closed out"

    def test_picks_the_first_completed_state_by_sequence(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        issue, _ = make_cycle_issue(cycle, states["started"])

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"mark_completed": True},
            format="json",
        )

        issue.refresh_from_db()
        assert issue.state_id == states["completed"].id, "should take Done (sequence 50), not Shipped (60)"

    def test_leaves_completed_and_cancelled_work_alone(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        shipped, _ = make_cycle_issue(cycle, states["completed_late"], name="Shipped")
        cancelled, _ = make_cycle_issue(cycle, states["cancelled"], name="Cancelled")

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"mark_completed": True},
            format="json",
        )

        shipped.refresh_from_db()
        cancelled.refresh_from_db()
        assert shipped.state_id == states["completed_late"].id, "a finished item must keep the state it finished in"
        assert cancelled.state_id == states["cancelled"].id, "cancelled work is not completed work"

    def test_does_nothing_without_the_flag(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        issue, _ = make_cycle_issue(cycle, states["started"])

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"end_date": "2026-01-31"},
            format="json",
        )

        assert state_group_of(issue) == "started"

    def test_is_a_no_op_when_the_project_has_no_completed_state(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle, make_cycle_issue
    ):
        started = State.objects.create(
            name="In Progress",
            group="started",
            sequence=30,
            color="#000000",
            project=project,
            workspace=workspace_with_members,
        )
        cycle = make_cycle()
        issue, _ = make_cycle_issue(cycle, started)

        response = admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"mark_completed": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert state_group_of(issue) == "started"

    def test_does_not_touch_another_cycle(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        ending = make_cycle(name="Ending")
        other = make_cycle(name="Untouched")
        mine, _ = make_cycle_issue(ending, states["started"], name="Mine")
        theirs, _ = make_cycle_issue(other, states["started"], name="Theirs")

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, ending.id),
            {"mark_completed": True},
            format="json",
        )

        assert state_group_of(mine) == "completed"
        assert state_group_of(theirs) == "started"

    def test_does_not_touch_a_work_item_removed_from_the_cycle(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        removed, link = make_cycle_issue(cycle, states["started"], name="Taken out")
        link.delete(soft=True)

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"mark_completed": True},
            format="json",
        )

        assert state_group_of(removed) == "started"

    def test_does_not_touch_an_archived_work_item(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        archived, _ = make_cycle_issue(cycle, states["started"], name="Archived", archived=True)

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"mark_completed": True},
            format="json",
        )

        assert state_group_of(archived) == "started"

    def test_does_not_touch_an_intake_work_item(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        cycle = make_cycle()
        triaged, _ = make_cycle_issue(cycle, states["triage"], name="Awaiting triage")

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"mark_completed": True},
            format="json",
        )

        assert state_group_of(triaged) == "triage"

    def test_both_flags_together_end_with_everything_completed(
        self, admin_client, workspace_with_members, project, project_admin, states, make_cycle, make_cycle_issue
    ):
        """
        ``set_in_progress`` runs first, so a backlog item is moved twice in one
        request and must land on completed, not in progress.
        """
        cycle = make_cycle()
        issue, _ = make_cycle_issue(cycle, states["backlog"])

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"set_in_progress": True, "mark_completed": True},
            format="json",
        )

        issue.refresh_from_db()
        assert issue.state_id == states["completed"].id


# --- manually_completed on the write serializer ------------------------------


@pytest.mark.contract
class TestManuallyCompletedFlag:
    """
    With auto-complete off, ``view_props["completed"]`` is what ends a cycle, and
    only ``manually_completed`` sets it.
    """

    def test_manually_completed_marks_the_cycle_completed(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        now = timezone.now()
        cycle = make_cycle(name="Ending", start_date=now - timedelta(days=7))

        response = admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"end_date": now.date().isoformat(), "manually_completed": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        cycle.refresh_from_db()
        assert cycle.view_props.get("completed") is True
        assert response.data["status"] == "COMPLETED"

    def test_an_end_date_alone_does_not_complete_the_cycle(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        now = timezone.now()
        cycle = make_cycle(name="Still open", start_date=now - timedelta(days=7))

        response = admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"end_date": (now + timedelta(days=7)).date().isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        cycle.refresh_from_db()
        assert cycle.view_props.get("completed") is False

    def test_extending_a_completed_cycle_into_the_future_reopens_it(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        now = timezone.now()
        cycle = make_cycle(
            name="Reopened",
            start_date=now - timedelta(days=14),
            end_date=now - timedelta(days=1),
            view_props={"completed": True},
        )

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"end_date": (now + timedelta(days=7)).date().isoformat()},
            format="json",
        )

        cycle.refresh_from_db()
        assert cycle.view_props.get("completed") is False

    def test_auto_complete_projects_ignore_the_flag(
        self, admin_client, workspace_with_members, project, project_admin, make_cycle
    ):
        """
        With auto-complete on, the end date decides and ``view_props`` is left
        untouched — the flag must not write a completed marker behind its back.
        """
        ProjectCustomSettings.objects.create(project=project, cycle_auto_complete=True)
        now = timezone.now()
        cycle = make_cycle(name="Auto", start_date=now - timedelta(days=7))

        admin_client.patch(
            cycle_url(workspace_with_members.slug, project.id, cycle.id),
            {"end_date": (now + timedelta(days=7)).date().isoformat(), "manually_completed": True},
            format="json",
        )

        cycle.refresh_from_db()
        assert "completed" not in cycle.view_props
