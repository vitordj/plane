# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Regression tests for R2.02 (assuming D2): coordinator gate across every route
that mutates a work item's responsible area or executor.

Rule under test (D2):
* Any active Project Member/Admin may set an area on a work item that has none.
* Changing the area, re-routing with an explicit ``mode``, removing the area,
  running ``organizational-unit-assign/``, or calling public ``reassign/`` and
  ``transfer/`` all require workspace Admin or a coordinator of the item's
  *current* area (403 ``ORG_NOT_UNIT_COORDINATOR`` otherwise).
"""

import pytest

from plane.app.services.orca import set_responsibility
from plane.db.models import (
    AutomationOperation,
    IssueOrganizationalUnit,
    RoutingState,
    State,
    StateGroup,
)

from .conftest import (
    ROLE_MEMBER,
    issue_assign_url,
    issue_unit_url,
    public_reassign_url,
    public_transfer_url,
)


@pytest.fixture
def project_setup(
    project,
    unit,
    second_unit,
    link_project,
    add_member,
    grant_manual_access,
    plain_user,
    second_user,
    admin_user,
):
    link_project(unit, project, ROLE_MEMBER)
    link_project(second_unit, project, ROLE_MEMBER)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user, ROLE_MEMBER)
    grant_manual_access(project, admin_user, ROLE_MEMBER)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    return project, unit, second_unit


@pytest.fixture
def assigned_issue(project_setup, make_issue, second_user):
    project, unit, _ = project_setup
    issue = make_issue(project)
    result = set_responsibility(issue, unit, explicit_executor=second_user)
    return issue, result.decision


@pytest.mark.unit
class TestInternalCoordinatorGate:
    def test_project_member_can_set_area_on_unassigned_work_item(
        self, member_client, workspace_with_members, project_setup, make_issue
    ):
        project, unit, _ = project_setup
        issue = make_issue(project)

        response = member_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 200
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == unit.id

    def test_project_member_idempotent_repost_same_area_without_mode_is_noop(
        self, member_client, workspace_with_members, project_setup, assigned_issue, second_user
    ):
        project, unit, _ = project_setup
        issue, _ = assigned_issue

        response = member_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 200
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == second_user.id

    def test_project_member_cannot_transfer_via_issue_unit_post(
        self, member_client, workspace_with_members, project_setup, assigned_issue, second_user
    ):
        project, unit, second_unit = project_setup
        issue, _ = assigned_issue

        response = member_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.organizational_unit_id == unit.id
        assert link.primary_executor_id == second_user.id

    def test_project_member_cannot_return_to_queue_via_manual_mode(
        self, member_client, workspace_with_members, project_setup, assigned_issue, second_user
    ):
        project, unit, _ = project_setup
        issue, _ = assigned_issue

        response = member_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id), "mode": "manual"},
            format="json",
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == second_user.id

    def test_project_member_cannot_delete_area(
        self, member_client, workspace_with_members, project_setup, assigned_issue
    ):
        project, unit, _ = project_setup
        issue, _ = assigned_issue

        response = member_client.delete(issue_unit_url(workspace_with_members.slug, project.id, issue.id))

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        assert IssueOrganizationalUnit.objects.filter(issue=issue, organizational_unit=unit).exists()

    def test_project_member_cannot_swap_executor_via_assign_append(
        self, member_client, workspace_with_members, project_setup, assigned_issue, second_user
    ):
        project, _, _ = project_setup
        issue, _ = assigned_issue

        response = member_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id),
            {"mode": "append"},
            format="json",
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == second_user.id

    def test_coordinator_of_target_area_cannot_steal_from_current_area(
        self, member_client, workspace_with_members, project_setup, assigned_issue, add_coordinator, plain_user
    ):
        project, unit, second_unit = project_setup
        issue, _ = assigned_issue
        add_coordinator(second_unit, plain_user)

        response = member_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == unit.id

    def test_coordinator_of_current_area_can_transfer_assign_and_delete(
        self, member_client, workspace_with_members, project_setup, assigned_issue, add_coordinator, plain_user
    ):
        project, unit, second_unit = project_setup
        issue, _ = assigned_issue
        add_coordinator(unit, plain_user)
        add_coordinator(second_unit, plain_user)

        assign_resp = member_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id),
            {"mode": "append"},
            format="json",
        )
        assert assign_resp.status_code == 200
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == plain_user.id

        transfer_resp = member_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )
        assert transfer_resp.status_code == 200
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == second_unit.id

        delete_resp = member_client.delete(issue_unit_url(workspace_with_members.slug, project.id, issue.id))
        assert delete_resp.status_code == 204
        assert not IssueOrganizationalUnit.objects.filter(issue=issue).exists()


@pytest.mark.unit
class TestPublicCoordinatorGate:
    def test_plain_member_token_cannot_reassign_and_does_not_spend_key(
        self, workspace_with_members, project_setup, assigned_issue, plain_user, second_user, token_client
    ):
        project, _, _ = project_setup
        issue, decision = assigned_issue
        client = token_client(plain_user)

        response = client.post(
            public_reassign_url(workspace_with_members.slug, project.id, issue.id),
            {"primary_executor": str(plain_user.id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY="r202-reassign-1",
            HTTP_IF_MATCH=str(decision.id),
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        assert not AutomationOperation.objects.exists()
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == second_user.id

    def test_plain_member_token_cannot_transfer_and_does_not_spend_key(
        self, workspace_with_members, project_setup, assigned_issue, plain_user, token_client
    ):
        project, unit, second_unit = project_setup
        issue, _ = assigned_issue
        client = token_client(plain_user)

        response = client.post(
            public_transfer_url(workspace_with_members.slug, project.id, issue.id),
            {"unit": second_unit.slug},
            format="json",
            HTTP_IDEMPOTENCY_KEY="r202-transfer-1",
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        assert not AutomationOperation.objects.exists()
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == unit.id

    def test_coordinator_token_can_reassign_and_transfer(
        self,
        workspace_with_members,
        project_setup,
        assigned_issue,
        plain_user,
        add_coordinator,
        token_client,
    ):
        project, unit, second_unit = project_setup
        issue, decision = assigned_issue
        add_coordinator(unit, plain_user)
        client = token_client(plain_user)

        reassign_resp = client.post(
            public_reassign_url(workspace_with_members.slug, project.id, issue.id),
            {"primary_executor": str(plain_user.id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY="r202-coord-reassign",
            HTTP_IF_MATCH=str(decision.id),
        )
        assert reassign_resp.status_code == 200
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == plain_user.id

        transfer_resp = client.post(
            public_transfer_url(workspace_with_members.slug, project.id, issue.id),
            {"unit": second_unit.slug},
            format="json",
            HTTP_IDEMPOTENCY_KEY="r202-coord-transfer",
        )
        assert transfer_resp.status_code == 200
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == second_unit.id
