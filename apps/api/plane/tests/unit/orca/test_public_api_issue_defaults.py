# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Assignment defaults on the public work item API (``/api/v1``).

The fork taught ``IssueSerializer`` to fall back to the assignees of the last
work item the same person created. In the web app that is a convenience — the
person is right there and can see the result. Through an API key it is a
liability: a robot creating work items in a loop hands each one to whoever
happened to receive the previous one, invisibly and unpredictably, which also
makes it impossible to route work by area.

The public serializer therefore keeps upstream's rule — the project's default
assignee, or nobody. The web app's own serializer is untouched; these tests
pin the public boundary.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import APIToken, IssueAssignee, ProjectMember, State

from .conftest import ROLE_ADMIN, ROLE_MEMBER


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def project_member(project, plain_user, workspace_with_members):
    ProjectMember.objects.create(
        project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
    )
    return plain_user


@pytest.fixture
def backlog_state(project, workspace_with_members):
    return State.objects.create(
        name="Backlog", group="backlog", project=project, workspace=workspace_with_members, sequence=1000
    )


@pytest.fixture
def api_client(admin_user, workspace_with_members, project_admin):
    """An API-key client, the way an automation reaches ``/api/v1``."""
    token = APIToken.objects.create(user=admin_user, workspace=workspace_with_members, label="tests")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def public_issues_url(workspace_with_members, project):
    return f"/api/v1/workspaces/{workspace_with_members.slug}/projects/{project.id}/issues/"


def assignee_ids_on(issue_id):
    return set(
        IssueAssignee.objects.filter(issue_id=issue_id, deleted_at__isnull=True).values_list("assignee_id", flat=True)
    )


@pytest.mark.unit
@pytest.mark.django_db
class TestPublicApiAssignmentDefaults:
    def test_no_assignees_and_no_project_default_leaves_the_item_unassigned(
        self, api_client, public_issues_url, backlog_state
    ):
        response = api_client.post(public_issues_url, {"name": "Created by a robot"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == set()

    def test_the_project_default_assignee_is_used_when_set(
        self, api_client, public_issues_url, backlog_state, project, project_member
    ):
        project.default_assignee = project_member
        project.save(update_fields=["default_assignee"])

        response = api_client.post(public_issues_url, {"name": "Routed to the default"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == {project_member.id}

    def test_an_explicit_assignee_list_is_used_as_given(
        self, api_client, public_issues_url, backlog_state, project_member
    ):
        response = api_client.post(
            public_issues_url, {"name": "Explicit", "assignees": [str(project_member.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == {project_member.id}

    def test_the_next_item_does_not_inherit_the_previous_one(
        self, api_client, public_issues_url, backlog_state, project_member
    ):
        """The defect this closes: item two used to land on whoever got item one."""
        first = api_client.post(
            public_issues_url, {"name": "First", "assignees": [str(project_member.id)]}, format="json"
        )
        assert first.status_code == status.HTTP_201_CREATED, first.data

        second = api_client.post(public_issues_url, {"name": "Second"}, format="json")

        assert second.status_code == status.HTTP_201_CREATED, second.data
        assert assignee_ids_on(second.data["id"]) == set()
