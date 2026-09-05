# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
HTTP contract tests for the responsible-unit and auto-assignment endpoints.

These routes are project-scoped rather than workspace-scoped, so they carry a
different permission shape from the rest of the layer. The engine underneath
is tested directly elsewhere; what is pinned here is the wrapper — scoping,
validation, and the guarantee that a request never returns a server error for
an input a user can actually send.
"""

import uuid

import pytest

from plane.db.models import IssueAssignee, IssueOrganizationalUnit, ProjectMember

from .conftest import ROLE_ADMIN, ROLE_MEMBER, issue_assign_url, issue_unit_url


@pytest.fixture
def project_with_admin(project, workspace_with_members, admin_user):
    """The requesting admin must also be a project member: these routes are
    project-scoped, so workspace admin alone does not grant access."""
    ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )
    return project


@pytest.fixture
def covering_unit(unit, project_with_admin, link_project):
    """
    An area that covers the project, which is now a precondition for owning
    work in it (defect D1). The refusal path has its own file,
    ``test_issue_unit_coverage.py``.
    """
    link_project(unit, project_with_admin, ROLE_MEMBER)
    return unit


@pytest.mark.unit
class TestIssueResponsibleUnit:
    def test_a_work_item_starts_without_a_responsible_unit(
        self, admin_client, workspace_with_members, project_with_admin, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.get(issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id))

        assert response.status_code == 200
        assert response.data["organizational_unit"] is None

    def test_setting_the_responsible_unit(
        self, admin_client, workspace_with_members, project_with_admin, covering_unit, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(covering_unit.id)},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["organizational_unit"]["slug"] == "compliance"
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == covering_unit.id

    def test_replacing_the_responsible_unit_keeps_a_single_link(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        covering_unit,
        second_unit,
        make_issue,
        link_project,
    ):
        link_project(second_unit, project_with_admin, ROLE_MEMBER)
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(covering_unit.id)}, format="json")

        response = admin_client.post(url, {"organizational_unit_id": str(second_unit.id)}, format="json")

        assert response.status_code == 200
        assert IssueOrganizationalUnit.objects.filter(issue=issue).count() == 1
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == second_unit.id

    def test_a_unit_from_another_workspace_cannot_be_made_responsible(
        self, admin_client, workspace_with_members, project_with_admin, foreign_unit, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(foreign_unit.id)},
            format="json",
        )

        assert response.status_code == 400
        assert not IssueOrganizationalUnit.objects.filter(issue=issue).exists()

    def test_an_unknown_unit_is_rejected(self, admin_client, workspace_with_members, project_with_admin, make_issue):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(uuid.uuid4())},
            format="json",
        )

        assert response.status_code == 400

    def test_an_unknown_work_item_is_not_found(self, admin_client, workspace_with_members, project_with_admin, unit):
        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, uuid.uuid4()),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 404

    def test_clearing_the_responsible_unit(
        self, admin_client, workspace_with_members, project_with_admin, covering_unit, make_issue
    ):
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(covering_unit.id)}, format="json")

        response = admin_client.delete(url)

        assert response.status_code == 204
        assert not IssueOrganizationalUnit.objects.filter(issue=issue).exists()

    def test_someone_outside_the_project_cannot_set_the_unit(
        self, member_client, workspace_with_members, project_with_admin, unit, make_issue
    ):
        """Workspace membership alone must not reach a project-scoped route."""
        issue = make_issue(project_with_admin)

        response = member_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 403
        assert not IssueOrganizationalUnit.objects.filter(issue=issue).exists()


@pytest.mark.unit
class TestIssueAssignmentFromUnit:
    @pytest.fixture
    def staffed_unit(self, unit, project_with_admin, link_project, add_member, plain_user, second_user):
        """A unit linked to the project with two members holding real access."""
        link_project(unit, project_with_admin, ROLE_MEMBER)
        add_member(unit, plain_user)
        add_member(unit, second_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)
        return unit

    def test_assigning_picks_the_least_loaded_member(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        staffed_unit,
        plain_user,
        second_user,
        make_issue,
    ):
        busy = make_issue(project_with_admin, name="Busy work")
        IssueAssignee.objects.create(
            issue=busy, assignee=plain_user, project=project_with_admin, workspace=workspace_with_members
        )
        issue = make_issue(project_with_admin, name="New work")
        admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id), {}, format="json"
        )

        assert response.status_code == 200
        assert response.data["reason"] == "assigned"
        assert IssueAssignee.objects.get(issue=issue).assignee_id == second_user.id

    def test_assigning_is_a_no_op_when_the_item_already_has_an_assignee(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        staffed_unit,
        plain_user,
        make_issue,
    ):
        issue = make_issue(project_with_admin)
        IssueAssignee.objects.create(
            issue=issue, assignee=plain_user, project=project_with_admin, workspace=workspace_with_members
        )
        admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id), {}, format="json"
        )

        assert response.status_code == 200
        assert response.data["assigned"] is None
        assert response.data["reason"] == "already_assigned"
        assert IssueAssignee.objects.filter(issue=issue).count() == 1

    def test_append_mode_adds_alongside_the_existing_assignee(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        staffed_unit,
        plain_user,
        make_issue,
    ):
        issue = make_issue(project_with_admin)
        IssueAssignee.objects.create(
            issue=issue, assignee=plain_user, project=project_with_admin, workspace=workspace_with_members
        )
        admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"mode": "append"},
            format="json",
        )

        assert response.status_code == 200
        assignees = set(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True))
        assert plain_user.id in assignees
        assert len(assignees) == 2

    def test_assigning_without_a_responsible_unit_is_rejected(
        self, admin_client, workspace_with_members, project_with_admin, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id), {}, format="json"
        )

        assert response.status_code == 400

    def test_an_explicit_unit_overrides_the_responsible_one(
        self, admin_client, workspace_with_members, project_with_admin, staffed_unit, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["reason"] == "assigned"

    def test_a_unit_from_another_workspace_cannot_drive_assignment(
        self, admin_client, workspace_with_members, project_with_admin, foreign_unit, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(foreign_unit.id)},
            format="json",
        )

        assert response.status_code == 400
        assert not IssueAssignee.objects.filter(issue=issue).exists()

    def test_an_unknown_mode_is_rejected(
        self, admin_client, workspace_with_members, project_with_admin, staffed_unit, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id), "mode": "replace"},
            format="json",
        )

        assert response.status_code == 400
        assert not IssueAssignee.objects.filter(issue=issue).exists()

    def test_a_unit_with_no_eligible_member_reports_it_explicitly(
        self, admin_client, workspace_with_members, project_with_admin, covering_unit, make_issue
    ):
        """An empty unit is an answer, not a server error."""
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(covering_unit.id)},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["assigned"] is None
        assert response.data["reason"] == "no_eligible_member"

    def test_assigning_an_unknown_work_item_is_not_found(
        self, admin_client, workspace_with_members, project_with_admin, staffed_unit
    ):
        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, uuid.uuid4()),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        assert response.status_code == 404

    def test_someone_outside_the_project_cannot_trigger_assignment(
        self, guest_client, workspace_with_members, project_with_admin, staffed_unit, make_issue
    ):
        """The unit's own members hold project access; an outsider does not."""
        issue = make_issue(project_with_admin)

        response = guest_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        assert response.status_code == 403
        assert not IssueAssignee.objects.filter(issue=issue).exists()

    def test_ranking_is_deterministic_when_load_is_tied(
        self, admin_client, workspace_with_members, project_with_admin, staffed_unit, make_issue
    ):
        """Two runs over identical state must choose the same person."""
        first = make_issue(project_with_admin, name="First")
        second = make_issue(project_with_admin, name="Second")

        chosen = []
        for issue in (first, second):
            IssueAssignee.objects.filter(issue=issue).delete()
            response = admin_client.post(
                issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
                {"organizational_unit_id": str(staffed_unit.id)},
                format="json",
            )
            chosen.append(response.data["assigned"]["user_id"])
            IssueAssignee.objects.filter(issue=issue).delete(soft=False)

        assert chosen[0] == chosen[1]
