# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for the Orca work item serializer changes.

* **The default assignee, and only the default assignee.** A work item created
  with no assignees used to pick up the assignees of the last work item the
  same person created in that project. That is defect D2 (RFC §2.2): a client
  posting an unassigned item got someone attached to it without asking, the
  API contract silently diverged from upstream's, and the choice depended on
  invisible history. The upstream rule is back in both serializers — the
  project's ``default_assignee`` if it is still a valid one, nothing else —
  and these tests pin both halves: the default is applied, and the inheritance
  is gone.
* **Label ids as plain UUIDs.** ``label_ids`` used to be a
  ``PrimaryKeyRelatedField`` over the default manager, so a request carrying a
  label that had since been deleted was rejected outright — the client had no
  way to know which id had gone stale. It is now a ``UUIDField`` list filtered
  against the project, so unknown ids are dropped and the write goes through.

Both are about which rows end up attached, so both run through the endpoint
against a real database. The public ``/api/v1`` serializer carries the same
rule and is exercised directly, since it authenticates by API key rather than
by session.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    IssueAssignee,
    IssueLabel,
    Label,
    ProjectMember,
    State,
)

from .conftest import ROLE_ADMIN, ROLE_GUEST, ROLE_MEMBER

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def backlog_state(project, workspace_with_members):
    return State.objects.create(
        name="Backlog",
        group="backlog",
        sequence=10,
        color="#000000",
        default=True,
        project=project,
        workspace=workspace_with_members,
    )


@pytest.fixture
def project_member(project, workspace_with_members, plain_user):
    ProjectMember.objects.create(
        project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
    )
    return plain_user


@pytest.fixture
def other_project_member(project, workspace_with_members, second_user):
    ProjectMember.objects.create(
        project=project, member=second_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
    )
    return second_user


@pytest.fixture
def issues_url(workspace_with_members, project):
    return f"/api/workspaces/{workspace_with_members.slug}/projects/{project.id}/issues/"


@pytest.fixture
def drafts_url(workspace_with_members):
    return f"/api/workspaces/{workspace_with_members.slug}/draft-issues/"


@pytest.fixture
def make_label(project, workspace_with_members):
    def _make(name, project_override=None):
        return Label.objects.create(
            name=name, color="#000000", project=project_override or project, workspace=workspace_with_members
        )

    return _make


def assignee_ids_on(issue_id):
    return set(
        IssueAssignee.objects.filter(issue_id=issue_id, deleted_at__isnull=True).values_list("assignee_id", flat=True)
    )


def label_ids_on(issue_id):
    return set(IssueLabel.objects.filter(issue_id=issue_id, deleted_at__isnull=True).values_list("label_id", flat=True))


# --- inherited assignees -----------------------------------------------------


@pytest.mark.contract
class TestDefaultAssignee:
    """The internal serializer, which is what the web app posts to."""

    def test_a_work_item_with_no_default_gets_no_assignees(
        self, admin_client, issues_url, project_admin, backlog_state
    ):
        response = admin_client.post(issues_url, {"name": "First ever"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == set()

    def test_an_explicit_assignee_list_is_used_as_given(
        self, admin_client, issues_url, project_admin, backlog_state, project_member
    ):
        response = admin_client.post(
            issues_url, {"name": "Explicit", "assignee_ids": [str(project_member.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == {project_member.id}

    def test_the_project_default_assignee_is_applied(
        self, admin_client, issues_url, project_admin, backlog_state, project_member, project
    ):
        project.default_assignee = project_member
        project.save()

        response = admin_client.post(issues_url, {"name": "Unassigned"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == {project_member.id}

    def test_a_default_assignee_who_left_the_project_is_not_applied(
        self, admin_client, issues_url, project_admin, backlog_state, project_member, project
    ):
        project.default_assignee = project_member
        project.save()
        ProjectMember.objects.filter(project=project, member=project_member).update(is_active=False)

        response = admin_client.post(issues_url, {"name": "Unassigned"}, format="json")

        assert assignee_ids_on(response.data["id"]) == set()

    def test_a_default_assignee_demoted_to_guest_is_not_applied(
        self, admin_client, issues_url, project_admin, backlog_state, project_member, project
    ):
        project.default_assignee = project_member
        project.save()
        ProjectMember.objects.filter(project=project, member=project_member).update(role=ROLE_GUEST)

        response = admin_client.post(issues_url, {"name": "Unassigned"}, format="json")

        assert assignee_ids_on(response.data["id"]) == set()

    def test_assignees_are_no_longer_inherited_from_the_previous_work_item(
        self, admin_client, issues_url, project_admin, backlog_state, project_member
    ):
        """
        Defect D2, pinned from the other side: creating an item with assignees
        must not turn those people into a default for the next one. This is the
        behaviour that was removed, and the test that would catch it coming
        back.
        """
        admin_client.post(issues_url, {"name": "First", "assignee_ids": [str(project_member.id)]}, format="json")

        response = admin_client.post(issues_url, {"name": "Second"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == set()

    def test_inheritance_does_not_cross_over_from_another_person(
        self, admin_client, member_client, issues_url, project_admin, backlog_state, project_member
    ):
        admin_client.post(issues_url, {"name": "Admin's", "assignee_ids": [str(project_member.id)]}, format="json")

        response = member_client.post(issues_url, {"name": "Member's"}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert assignee_ids_on(response.data["id"]) == set()


@pytest.mark.unit
class TestPublicApiDefaultAssignee:
    """
    The ``/api/v1`` serializer, exercised directly: it authenticates by API key
    rather than by session, and what matters here is which rows ``create``
    writes, not the routing around it.
    """

    @staticmethod
    def create_issue(project, workspace, name, default_assignee_id=None, assignees=None):
        from plane.api.serializers import IssueSerializer

        serializer = IssueSerializer(
            context={
                "project_id": project.id,
                "workspace_id": workspace.id,
                "default_assignee_id": default_assignee_id,
            }
        )
        validated = {"name": name}
        if assignees is not None:
            validated["assignees"] = assignees
        return serializer.create(validated)

    def test_no_default_means_no_assignees(self, db, project, workspace_with_members):
        issue = self.create_issue(project, workspace_with_members, "Unassigned")

        assert assignee_ids_on(issue.id) == set()

    def test_the_default_assignee_is_applied(self, db, project, workspace_with_members, plain_user):
        ProjectMember.objects.create(
            project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
        )

        issue = self.create_issue(project, workspace_with_members, "Unassigned", default_assignee_id=plain_user.id)

        assert assignee_ids_on(issue.id) == {plain_user.id}

    def test_a_default_who_is_not_a_project_member_is_not_applied(
        self, db, project, workspace_with_members, plain_user
    ):
        issue = self.create_issue(project, workspace_with_members, "Unassigned", default_assignee_id=plain_user.id)

        assert assignee_ids_on(issue.id) == set()

    def test_nothing_is_inherited_from_the_previous_work_item(self, db, project, workspace_with_members, plain_user):
        """The public API contract is upstream's again (defect D2)."""
        ProjectMember.objects.create(
            project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
        )
        self.create_issue(project, workspace_with_members, "First", assignees=[plain_user.id])

        issue = self.create_issue(project, workspace_with_members, "Second")

        assert assignee_ids_on(issue.id) == set()


# --- label ids as plain UUIDs ------------------------------------------------


@pytest.mark.contract
class TestLabelIdHandling:
    def test_a_project_label_is_attached(self, admin_client, issues_url, project_admin, backlog_state, make_label):
        label = make_label("Compliance")

        response = admin_client.post(issues_url, {"name": "Labelled", "label_ids": [str(label.id)]}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert label_ids_on(response.data["id"]) == {label.id}

    def test_a_deleted_label_is_dropped_rather_than_rejected(
        self, admin_client, issues_url, project_admin, backlog_state, make_label
    ):
        """
        The bug this change fixed: a stale id in the payload used to fail
        validation outright, so a client holding a label that had since been
        deleted could not save the work item at all.
        """
        alive = make_label("Compliance")
        deleted = make_label("Retired")
        deleted.delete(soft=True)

        response = admin_client.post(
            issues_url,
            {"name": "Labelled", "label_ids": [str(alive.id), str(deleted.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert label_ids_on(response.data["id"]) == {alive.id}

    def test_a_label_from_another_project_is_dropped(
        self, admin_client, issues_url, project_admin, backlog_state, make_label, second_project
    ):
        foreign = make_label("Sibling", project_override=second_project)

        response = admin_client.post(issues_url, {"name": "Labelled", "label_ids": [str(foreign.id)]}, format="json")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert label_ids_on(response.data["id"]) == set()

    def test_a_malformed_label_id_is_still_rejected(self, admin_client, issues_url, project_admin, backlog_state):
        """Dropping unknown ids is not the same as accepting nonsense."""
        response = admin_client.post(issues_url, {"name": "Labelled", "label_ids": ["not-a-uuid"]}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_empty_label_list_clears_the_labels(
        self, admin_client, issues_url, project_admin, backlog_state, make_label
    ):
        label = make_label("Compliance")
        created = admin_client.post(issues_url, {"name": "Labelled", "label_ids": [str(label.id)]}, format="json")

        response = admin_client.patch(f"{issues_url}{created.data['id']}/", {"label_ids": []}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert label_ids_on(created.data["id"]) == set()

    def test_a_deleted_label_does_not_block_an_update(
        self, admin_client, issues_url, project_admin, backlog_state, make_label
    ):
        alive = make_label("Compliance")
        deleted = make_label("Retired")
        created = admin_client.post(issues_url, {"name": "Labelled"}, format="json")
        deleted.delete(soft=True)

        response = admin_client.patch(
            f"{issues_url}{created.data['id']}/",
            {"label_ids": [str(alive.id), str(deleted.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert label_ids_on(created.data["id"]) == {alive.id}


# --- the same rule on drafts -------------------------------------------------


@pytest.mark.contract
class TestDraftLabelIdHandling:
    def test_a_deleted_label_is_dropped_rather_than_rejected(
        self, admin_client, drafts_url, project, project_admin, backlog_state, make_label
    ):
        alive = make_label("Compliance")
        deleted = make_label("Retired")
        deleted.delete(soft=True)

        response = admin_client.post(
            drafts_url,
            {
                "name": "Draft",
                "project_id": str(project.id),
                "label_ids": [str(alive.id), str(deleted.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_a_malformed_label_id_is_still_rejected(
        self, admin_client, drafts_url, project, project_admin, backlog_state
    ):
        response = admin_client.post(
            drafts_url,
            {"name": "Draft", "project_id": str(project.id), "label_ids": ["not-a-uuid"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
