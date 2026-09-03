# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The invariants of the routing layer, each named for the one it pins.

The others live where the behaviour they belong to is tested (I2 in
``test_issue_unit_coverage``, I4 to I7 and I10 in ``test_assignment_service``).
This file holds the two that belong to no single operation — one area per work
item, and "assigned" meaning somebody really is on it — plus the kill switch,
which is what makes the whole layer safe to turn off.
"""

import pytest
from rest_framework import status

from plane.app.services.orca import set_responsibility
from plane.db.models import IssueAssignee, IssueOrganizationalUnit, ProjectMember
from plane.db.models.organizational_unit import RoutingState

from .conftest import (
    ROLE_MEMBER,
    issue_assign_url,
    issue_unit_url,
    unit_url,
    units_url,
    workload_url,
)


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def executor(covered_unit, project, workspace_with_members, add_member, plain_user):
    add_member(covered_unit, plain_user)
    ProjectMember.objects.create(
        project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
    )
    return plain_user


@pytest.mark.unit
@pytest.mark.django_db
class TestI1OneAreaPerWorkItem:
    def test_i1_taking_responsibility_twice_leaves_one_live_link(
        self, covered_unit, second_unit, project, link_project, make_issue
    ):
        link_project(second_unit, project)
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, trigger="internal_api")
        set_responsibility(issue, second_unit, trigger="internal_api")

        links = IssueOrganizationalUnit.objects.filter(issue=issue, deleted_at__isnull=True)
        assert links.count() == 1
        assert links.first().organizational_unit_id == second_unit.id

    def test_i1_clearing_and_setting_again_works(
        self, admin_client, workspace_with_members, covered_unit, project, make_issue
    ):
        """
        The soft-delete trap: the link is kept as history when cleared, so a
        unique index over every row would make set → clear → set fail.
        """
        issue = make_issue(project)
        url = issue_unit_url(workspace_with_members.slug, project.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(covered_unit.id)}, format="json")
        admin_client.delete(url)

        response = admin_client.post(url, {"organizational_unit_id": str(covered_unit.id)}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.unit
@pytest.mark.django_db
class TestI3AssignedMeansSomebodyIsOnIt:
    def test_i3_an_assigned_item_has_an_executor_who_is_an_assignee(self, covered_unit, project, make_issue, executor):
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == executor.id
        assert IssueAssignee.objects.filter(issue=issue, assignee=executor, deleted_at__isnull=True).exists()

    def test_i3_a_queued_item_has_no_executor(self, covered_unit, project, make_issue, executor):
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, trigger="internal_api")

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None


@pytest.mark.unit
@pytest.mark.django_db
class TestTheKillSwitch:
    """
    With the layer off every route answers 404 — not 403, because a workspace
    that has never enabled it should not learn the endpoints exist. This is
    what makes the whole layer safe to switch off in an incident.
    """

    @pytest.fixture(autouse=True)
    def layer_off(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = False

    def test_the_unit_routes_are_gone(self, admin_client, workspace_with_members, unit):
        slug = workspace_with_members.slug

        assert admin_client.get(units_url(slug)).status_code == status.HTTP_404_NOT_FOUND
        assert admin_client.get(unit_url(slug, unit.id)).status_code == status.HTTP_404_NOT_FOUND
        assert admin_client.get(workload_url(slug, unit.id)).status_code == status.HTTP_404_NOT_FOUND

    def test_the_work_item_routes_are_gone(self, admin_client, workspace_with_members, project, make_issue):
        issue = make_issue(project)
        slug = workspace_with_members.slug

        assert admin_client.get(issue_unit_url(slug, project.id, issue.id)).status_code == status.HTTP_404_NOT_FOUND
        assert (
            admin_client.post(issue_assign_url(slug, project.id, issue.id), {}, format="json").status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_the_policy_route_is_gone(self, admin_client, workspace_with_members, unit):
        url = f"/api/orca/workspaces/{workspace_with_members.slug}/organizational-units/{unit.id}/policy/"

        assert admin_client.get(url).status_code == status.HTTP_404_NOT_FOUND
