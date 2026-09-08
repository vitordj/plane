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

    def test_a_guest_does_not_see_secret_projects_they_are_not_in(
        self, workspace_with_members, unit, guest_user, admin_user, token_client, link_project
    ):
        # R1.A5: a Guest with an API key used to receive every covered project,
        # secret ones included. Native project list already hides those.
        from plane.db.models import Project

        secret = Project.objects.create(
            name="Secret Onboarding",
            identifier="SEC",
            workspace=workspace_with_members,
            created_by=admin_user,
            network=0,
        )
        link_project(unit, secret)
        guest = token_client(guest_user)
        admin = token_client(admin_user)

        guest_response = guest.get(public_units_url(workspace_with_members.slug))
        admin_response = admin.get(public_units_url(workspace_with_members.slug))

        assert guest_response.status_code == 200
        area = next(row for row in guest_response.data["results"] if row["slug"] == "compliance")
        assert [p["identifier"] for p in area["projects"]] == []

        admin_area = next(row for row in admin_response.data["results"] if row["slug"] == "compliance")
        assert "SEC" in [p["identifier"] for p in admin_area["projects"]]

    def test_a_guest_sees_a_secret_project_they_belong_to(
        self, workspace_with_members, unit, guest_user, admin_user, token_client, link_project, grant_manual_access
    ):
        from plane.db.models import Project

        secret = Project.objects.create(
            name="Secret Onboarding",
            identifier="SEC",
            workspace=workspace_with_members,
            created_by=admin_user,
            network=0,
        )
        link_project(unit, secret)
        grant_manual_access(secret, guest_user)
        guest = token_client(guest_user)

        response = guest.get(public_units_url(workspace_with_members.slug))

        area = next(row for row in response.data["results"] if row["slug"] == "compliance")
        assert [p["identifier"] for p in area["projects"]] == ["SEC"]


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
