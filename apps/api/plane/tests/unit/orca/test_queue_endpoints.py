# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who may do what to an area's queue.

The permission matrix is the substance of this file. Every cell is a way for
somebody to take work away from a person who is doing it, or to hand
themselves work that was not theirs — and the coordinator role exists exactly
so that those are two different questions.
"""

import pytest
from rest_framework import status

from plane.app.services.orca import set_responsibility
from plane.db.models import (
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitCoordinator,
    ProjectMember,
)
from plane.db.models.organizational_unit import RoutingState

from .conftest import ROLE_MEMBER


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def in_area(covered_unit, project, workspace_with_members, add_member, workspace_member_of):
    """Put somebody in the area and in the project — an executor."""

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
def make_coordinator(covered_unit, workspace_with_members, workspace_member_of):
    def _make(user):
        return OrganizationalUnitCoordinator.objects.create(
            organizational_unit=covered_unit,
            workspace_member=workspace_member_of(user),
            workspace=workspace_with_members,
        )

    return _make


def queue_url(slug, unit_id):
    return f"/api/orca/workspaces/{slug}/organizational-units/{unit_id}/queue/"


def action_url(slug, project_id, issue_id, action):
    return f"/api/orca/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/organizational-unit/{action}/"


@pytest.fixture
def queued_issue(covered_unit, project, make_issue, workspace_with_members):
    issue = make_issue(project)
    set_responsibility(issue, covered_unit, trigger="internal_api")
    return issue


@pytest.mark.unit
@pytest.mark.django_db
class TestReadingTheQueue:
    def test_a_coordinator_sees_what_is_waiting(
        self, member_client, workspace_with_members, covered_unit, queued_issue, make_coordinator, plain_user
    ):
        make_coordinator(plain_user)

        response = member_client.get(queue_url(workspace_with_members.slug, covered_unit.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert len(response.data) == 1
        assert response.data[0]["routing_state"] == RoutingState.QUEUED
        assert response.data[0]["can_assign"] is True

    def test_a_member_of_the_area_sees_it_too_but_cannot_assign(
        self, member_client, workspace_with_members, covered_unit, queued_issue, in_area, plain_user
    ):
        in_area(plain_user)

        response = member_client.get(queue_url(workspace_with_members.slug, covered_unit.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data[0]["can_assign"] is False

    def test_somebody_with_no_role_in_the_area_is_refused(
        self, member_client, workspace_with_members, covered_unit, queued_issue
    ):
        response = member_client.get(queue_url(workspace_with_members.slug, covered_unit.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_can_always_look(self, admin_client, workspace_with_members, covered_unit, queued_issue):
        """Somebody has to be able to unstick an area whose coordinator left."""
        response = admin_client.get(queue_url(workspace_with_members.slug, covered_unit.id))

        assert response.status_code == status.HTTP_200_OK


@pytest.mark.unit
@pytest.mark.django_db
class TestClaiming:
    def test_a_member_can_take_queued_work(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        queued_issue,
        in_area,
        plain_user,
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="self_claim",
            allowed_modes=["self_claim"],
        )
        in_area(plain_user)

        response = member_client.post(
            action_url(workspace_with_members.slug, project.id, queued_issue.id, "claim"), {}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert IssueOrganizationalUnit.objects.get(issue=queued_issue).primary_executor_id == plain_user.id

    def test_somebody_outside_the_area_cannot(self, member_client, workspace_with_members, project, queued_issue):
        response = member_client.post(
            action_url(workspace_with_members.slug, project.id, queued_issue.id, "claim"), {}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.unit
@pytest.mark.django_db
class TestAssigningAndReturning:
    def test_a_coordinator_assigns(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        queued_issue,
        in_area,
        make_coordinator,
        plain_user,
        second_user,
    ):
        make_coordinator(plain_user)
        in_area(second_user)

        response = member_client.post(
            action_url(workspace_with_members.slug, project.id, queued_issue.id, "assign-to"),
            {"primary_executor": str(second_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert IssueOrganizationalUnit.objects.get(issue=queued_issue).primary_executor_id == second_user.id

    def test_a_plain_member_of_the_area_cannot_assign_somebody_else(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        queued_issue,
        in_area,
        plain_user,
        second_user,
    ):
        in_area(plain_user)
        in_area(second_user)

        response = member_client.post(
            action_url(workspace_with_members.slug, project.id, queued_issue.id, "assign-to"),
            {"primary_executor": str(second_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_person_carrying_it_may_put_it_down(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        make_issue,
        in_area,
        plain_user,
    ):
        executor = in_area(plain_user)
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")

        response = member_client.post(
            action_url(workspace_with_members.slug, project.id, issue.id, "return"),
            {"reason": "not mine after all"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.QUEUED

    def test_a_bystander_cannot_return_somebody_elses_work(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        make_issue,
        in_area,
        second_user,
    ):
        executor = in_area(second_user)
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")

        response = member_client.post(
            action_url(workspace_with_members.slug, project.id, issue.id, "return"), {}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.unit
@pytest.mark.django_db
class TestCandidatesAndDecisions:
    def test_the_candidate_list_carries_the_load(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        queued_issue,
        in_area,
        plain_user,
    ):
        in_area(plain_user)

        response = member_client.get(action_url(workspace_with_members.slug, project.id, queued_issue.id, "candidates"))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["candidates"][0]["user_id"] == str(plain_user.id)
        assert "total_open" in response.data["candidates"][0]

    def test_decisions_are_listed_newest_first(
        self, member_client, workspace_with_members, covered_unit, queued_issue, make_coordinator, plain_user
    ):
        make_coordinator(plain_user)

        response = member_client.get(
            f"/api/orca/workspaces/{workspace_with_members.slug}/organizational-units/{covered_unit.id}/decisions/"
        )

        assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.unit
@pytest.mark.django_db
class TestCoordinatorsAndPolicy:
    def test_an_admin_appoints_a_coordinator(
        self, admin_client, workspace_with_members, covered_unit, plain_user, workspace_member_of
    ):
        response = admin_client.post(
            f"/api/orca/workspaces/{workspace_with_members.slug}/organizational-units/{covered_unit.id}/coordinators/",
            {"workspace_member": str(workspace_member_of(plain_user).id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert OrganizationalUnitCoordinator.objects.filter(organizational_unit=covered_unit, is_active=True).exists()

    def test_a_member_cannot_appoint_one(
        self, member_client, workspace_with_members, covered_unit, plain_user, workspace_member_of
    ):
        response = member_client.post(
            f"/api/orca/workspaces/{workspace_with_members.slug}/organizational-units/{covered_unit.id}/coordinators/",
            {"workspace_member": str(workspace_member_of(plain_user).id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_sets_the_policy(self, admin_client, workspace_with_members, covered_unit):
        response = admin_client.put(
            f"/api/orca/workspaces/{workspace_with_members.slug}/organizational-units/{covered_unit.id}/policy/write/",
            {"default_mode": "least_loaded", "allowed_modes": ["least_loaded", "manual"]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["default_mode"] == "least_loaded"

    def test_a_policy_whose_default_is_not_allowed_is_refused(self, admin_client, workspace_with_members, covered_unit):
        response = admin_client.put(
            f"/api/orca/workspaces/{workspace_with_members.slug}/organizational-units/{covered_unit.id}/policy/write/",
            {"default_mode": "least_loaded", "allowed_modes": ["manual"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
