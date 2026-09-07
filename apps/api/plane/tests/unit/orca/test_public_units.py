# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The two reads of the automation API (RFC §7.2, item 1.4).

``units`` is what an integration reads at startup so it can send work without
guessing: which areas exist, what they cover, and how each hands work out. The
policy is resolved before it is sent, because a client that received the raw
rows would have to reimplement the project-over-area-over-fallback precedence
and would get it wrong the first time a project policy appeared.

``queue`` is the area's backlog. It is the same rows the coordinator's inbox
will show in Phase 2, in the same order — overdue first, then oldest first —
because "what is waiting" should not depend on which door you came through.
Who may read it is narrower than ``units`` for a plain reason: these rows carry
the titles of real work.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.db.models import (
    AssignmentMode,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    ProjectMember,
    QueueReason,
    RoutingState,
)

from .conftest import ROLE_MEMBER, public_queue_url, public_units_url


@pytest.fixture
def covered(unit, project, link_project):
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def insider(unit, plain_user, add_member, project, grant_manual_access, token_client, covered):
    """Somebody in the area — the audience the queue is for."""
    add_member(unit, plain_user)
    grant_manual_access(project, plain_user)
    return token_client(plain_user)


@pytest.fixture
def admin_token_client(admin_user, token_client):
    return token_client(admin_user)


@pytest.fixture
def queued(unit, project, make_issue):
    """Put an item in the area's queue, with control over its deadline."""

    def _queue(name="Waiting", due_at=None, state=RoutingState.QUEUED, queued_at=None, executor=None):
        issue = make_issue(project, name=name)
        # A database CHECK enforces invariant I3 both ways: `assigned` needs an
        # executor, and every other state must not have one. Writing a row by
        # hand has to respect it, which is the constraint doing its job.
        assigned = state == RoutingState.ASSIGNED
        return IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=state,
            queue_reason="" if assigned else QueueReason.NEW_ITEM,
            queued_at=None if assigned else (queued_at or timezone.now()),
            assignment_due_at=due_at,
            primary_executor=executor if assigned else None,
        )

    return _queue


@pytest.mark.unit
class TestListingAreas:
    def test_an_area_arrives_with_its_projects_and_its_policy(
        self, admin_token_client, workspace_with_members, unit, project, covered
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value, AssignmentMode.MANUAL.value],
        )

        response = admin_token_client.get(public_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        assert area["projects"][0]["project_id"] == str(project.id)
        assert area["projects"][0]["identifier"] == project.identifier
        # Resolved, not stored: the client is told what would happen, not what
        # rows exist.
        assert area["projects"][0]["policy"]["default_mode"] == AssignmentMode.LEAST_LOADED
        assert set(area["projects"][0]["policy"]["allowed_modes"]) == {"least_loaded", "manual"}

    def test_an_area_with_no_policy_reports_the_fallback(
        self, admin_token_client, workspace_with_members, unit, covered
    ):
        response = admin_token_client.get(public_units_url(workspace_with_members.slug))

        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        # An unconfigured area hands nothing out on its own, but forbids
        # nothing either (RFC §6.3).
        assert area["projects"][0]["policy"]["default_mode"] == AssignmentMode.MANUAL
        assert set(area["projects"][0]["policy"]["allowed_modes"]) == {"manual", "self_claim", "least_loaded"}

    def test_an_inactive_area_is_not_listed(self, admin_token_client, workspace_with_members, unit, covered):
        unit.is_active = False
        unit.save(update_fields=["is_active"])

        response = admin_token_client.get(public_units_url(workspace_with_members.slug))

        assert [row for row in response.data["results"] if row["slug"] == "compliance"] == []

    def test_a_member_of_another_workspace_sees_nothing(
        self, workspace_with_members, unit, covered, outsider_user, token_client
    ):
        stranger = token_client(outsider_user)

        response = stranger.get(public_units_url(workspace_with_members.slug))

        assert response.status_code == 403


class TestWhichProjectsACallerIsToldAbout:
    """
    R1.A5 — the area structure is workspace-wide, the project map is not.

    Plane lets any member mint an API token, Guests included, and this route
    used to answer every one of them with the id and identifier of every project
    every area covers. The native project route has always filtered to the
    projects a caller belongs to plus the ones public to the workspace; this one
    filtered nothing, so a Guest with a token read the private project map of
    the whole tenant. Combined with the member listing, that is the org chart
    and the project map for the role least entitled to either.

    The area still appears. That it exists is not the secret; what it covers is.
    """

    def test_a_guest_is_not_told_about_a_private_project_they_do_not_belong_to(
        self, token_client, guest_user, workspace_with_members, unit, project, covered
    ):
        # `network` defaults to 2, "visible to the whole workspace", so a test
        # that skipped this line would pass against the unfixed code and prove
        # nothing: the caller was entitled to that project all along.
        project.network = 0
        project.save(update_fields=["network"])

        response = token_client(guest_user).get(public_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        assert area["projects"] == []

    def test_the_area_is_still_listed_with_an_empty_project_list(
        self, token_client, guest_user, workspace_with_members, unit, project, covered
    ):
        project.network = 0
        project.save(update_fields=["network"])

        response = token_client(guest_user).get(public_units_url(workspace_with_members.slug))

        slugs = [row["slug"] for row in response.data["results"]]
        assert "compliance" in slugs

    def test_a_project_the_caller_belongs_to_is_reported(
        self, token_client, guest_user, workspace_with_members, unit, project, covered, grant_manual_access
    ):
        project.network = 0
        project.save(update_fields=["network"])
        grant_manual_access(project, guest_user)

        response = token_client(guest_user).get(public_units_url(workspace_with_members.slug))

        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        assert [row["project_id"] for row in area["projects"]] == [str(project.id)]

    def test_a_project_public_to_the_workspace_is_reported(
        self, token_client, guest_user, workspace_with_members, unit, project, covered
    ):
        """Same predicate the native project route uses, so the two agree."""
        assert project.network == 2  # the default, and what this test is about

        response = token_client(guest_user).get(public_units_url(workspace_with_members.slug))

        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        assert [row["project_id"] for row in area["projects"]] == [str(project.id)]

    def test_a_workspace_admin_still_sees_everything(
        self, admin_token_client, workspace_with_members, unit, project, covered
    ):
        project.network = 0
        project.save(update_fields=["network"])

        response = admin_token_client.get(public_units_url(workspace_with_members.slug))

        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        assert [row["project_id"] for row in area["projects"]] == [str(project.id)]

    def test_a_project_is_reported_once_however_many_memberships_matched(
        self, token_client, plain_user, workspace_with_members, unit, project, covered, grant_manual_access
    ):
        """
        The membership filter joins ProjectMember. Without ``distinct`` the join
        repeats the project per matching row, and the caller sees duplicates.
        """
        project.network = 0
        project.save(update_fields=["network"])
        grant_manual_access(project, plain_user)

        response = token_client(plain_user).get(public_units_url(workspace_with_members.slug))

        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        assert [row["project_id"] for row in area["projects"]] == [str(project.id)]


@pytest.mark.unit
@pytest.mark.unit
class TestReadingTheQueue:
    def test_the_area_reports_what_is_waiting(self, insider, workspace_with_members, unit, queued):
        queued(name="First")

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        assert response.status_code == 200
        assert [row["name"] for row in response.data["results"]] == ["First"]
        assert response.data["results"][0]["routing_state"] == RoutingState.QUEUED
        assert response.data["results"][0]["assignment_overdue"] is False

    def test_overdue_items_come_first(self, insider, workspace_with_members, unit, queued):
        now = timezone.now()
        queued(name="Fresh", queued_at=now)
        queued(name="Breached", due_at=now - timedelta(hours=2), queued_at=now)

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        # An item whose deadline has passed is the one costing somebody
        # something right now; sorting by age alone would bury it.
        assert [row["name"] for row in response.data["results"]] == ["Breached", "Fresh"]
        assert response.data["results"][0]["assignment_overdue"] is True

    def test_among_items_merely_waiting_the_oldest_comes_first(self, insider, workspace_with_members, unit, queued):
        now = timezone.now()
        queued(name="Newer", queued_at=now)
        queued(name="Older", queued_at=now - timedelta(days=1))

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        assert [row["name"] for row in response.data["results"]] == ["Older", "Newer"]
        assert response.data["results"][0]["age_seconds"] >= 86000

    def test_assigned_work_is_not_in_the_queue_unless_asked_for(
        self, insider, workspace_with_members, unit, queued, plain_user
    ):
        queued(name="Waiting")
        queued(name="Under way", state=RoutingState.ASSIGNED, executor=plain_user)

        default = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))
        everything = insider.get(public_queue_url(workspace_with_members.slug, unit.slug) + "?routing_state=all")

        assert [row["name"] for row in default.data["results"]] == ["Waiting"]
        assert len(everything.data["results"]) == 2

    def test_a_failed_allocation_counts_as_waiting(self, insider, workspace_with_members, unit, queued):
        queued(name="Nobody eligible", state=RoutingState.ALLOCATION_FAILED)

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        # It is waiting for a human precisely because the machine could not
        # place it — hiding it would hide the only items needing attention.
        assert [row["name"] for row in response.data["results"]] == ["Nobody eligible"]

    def test_the_overdue_filter_keeps_only_breached_items(self, insider, workspace_with_members, unit, queued):
        now = timezone.now()
        queued(name="Fresh")
        queued(name="Breached", due_at=now - timedelta(hours=2))

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug) + "?overdue=true")

        assert [row["name"] for row in response.data["results"]] == ["Breached"]

    def test_the_project_filter_narrows_to_one_project(
        self, insider, workspace_with_members, unit, project, second_project, queued, link_project, make_issue
    ):
        queued(name="In onboarding")
        link_project(unit, second_project, ROLE_MEMBER)
        IssueOrganizationalUnit.objects.create(
            issue=make_issue(second_project, name="In billing"),
            organizational_unit=unit,
            project=second_project,
            workspace=workspace_with_members,
            routing_state=RoutingState.QUEUED,
            queued_at=timezone.now(),
        )

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug) + f"?project={project.id}")

        assert [row["name"] for row in response.data["results"]] == ["In onboarding"]

    def test_an_unknown_state_filter_is_refused(self, insider, workspace_with_members, unit, queued):
        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug) + "?routing_state=napping")

        assert response.status_code == 400

    def test_a_workspace_admin_may_look(self, admin_token_client, workspace_with_members, unit, covered, queued):
        queued(name="Waiting")

        response = admin_token_client.get(public_queue_url(workspace_with_members.slug, unit.slug))

        # An admin can already see every item through the interface; hiding the
        # queue from them would protect nothing.
        assert response.status_code == 200

    def test_a_workspace_member_outside_the_area_may_not(
        self, workspace_with_members, unit, covered, second_user, token_client, queued
    ):
        queued(name="Waiting")
        outsider = token_client(second_user)

        response = outsider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        assert response.status_code == 403

    def test_an_unknown_area_is_not_found(self, insider, workspace_with_members):
        response = insider.get(public_queue_url(workspace_with_members.slug, "no-such-area"))

        assert response.status_code == 404
        assert response.data["error_message"] == "ORG_UNIT_NOT_FOUND"

    def test_the_switch_hides_the_route(self, insider, workspace_with_members, unit, settings):
        settings.ORCA_PUBLIC_API_ENABLED = False

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        assert response.status_code == 404
        assert response.data["error_message"] == "ORG_PUBLIC_API_DISABLED"


@pytest.mark.unit
class TestTheQueueOnlyReportsWorkTheReaderCouldOpen:
    """
    R1.A7 — being in the area is not the same as being able to open its
    projects, and the layer itself is what pulls those apart.

    Archiving a project drops it from ``_active_sources``, so the reconciler
    deactivates the ``ProjectMember`` rows it had granted, and
    ``unit_covers_project`` stops counting it as covered. Both halves agree the
    project is out of reach. The queue, filtered only by area and state, kept
    handing out its work item titles and its executors' email addresses to
    anybody still in the area.

    The internal queue reuses this same query (decision M6), so leaving it
    would have put the leak on a screen rather than only behind an API key.
    """

    def test_a_reader_who_lost_project_access_sees_none_of_its_work(
        self, insider, plain_user, workspace_with_members, unit, project, queued
    ):
        queued()
        ProjectMember.objects.filter(member=plain_user, project=project).update(is_active=False)

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        assert response.status_code == 200
        assert response.data["results"] == []

    def test_a_reader_who_kept_access_still_sees_it(self, insider, workspace_with_members, unit, project, queued):
        queued()

        response = insider.get(public_queue_url(workspace_with_members.slug, unit.slug))

        assert response.status_code == 200
        assert response.data["results"]

    def test_a_workspace_admin_is_not_restricted(
        self, admin_token_client, plain_user, workspace_with_members, unit, project, covered, queued
    ):
        """
        An admin already sees every project through the interface, and building
        the id set for a large tenant to tell them what they can already read
        would be a query for nothing.
        """
        queued()
        ProjectMember.objects.filter(project=project).update(is_active=False)

        response = admin_token_client.get(public_queue_url(workspace_with_members.slug, unit.slug))

        assert response.status_code == 200
        assert response.data["results"]
