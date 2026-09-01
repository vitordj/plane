# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for the Orca intake fallbacks.

Two fork changes to ``IntakeIssueViewSet``, untested until now:

* **A missing triage state is created rather than fatal.** ``create`` needs a
  triage state to file the work item into; a project that has none (an older
  project, or one whose triage state was removed) now gets one made on the spot.
* **A missing intake issue answers 404.** ``partial_update`` and ``destroy``
  used to resolve the project's *first* ``Intake`` row and then ``get()`` the
  intake issue inside it, so an unknown work item raised ``DoesNotExist`` — a
  500 to the caller — and an intake issue belonging to a second intake was
  invisible. Both now look the intake issue up directly and answer 404 when
  there is none.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    Intake,
    IntakeIssue,
    Issue,
    Project,
    ProjectMember,
    State,
)

from .conftest import ROLE_ADMIN

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def intake_project(workspace_with_members, admin_user):
    """A project with intake switched on and the caller as its admin."""
    project = Project.objects.create(
        name="Support",
        identifier="SUP",
        workspace=workspace_with_members,
        created_by=admin_user,
        intake_view=True,
    )
    ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )
    State.objects.create(
        name="Backlog",
        group="backlog",
        sequence=10,
        color="#000000",
        default=True,
        project=project,
        workspace=workspace_with_members,
    )
    return project


@pytest.fixture
def intake(intake_project, workspace_with_members):
    return Intake.objects.create(
        name="Intake", project=intake_project, workspace=workspace_with_members, is_default=True
    )


@pytest.fixture
def intake_url(workspace_with_members, intake_project):
    return f"/api/workspaces/{workspace_with_members.slug}/projects/{intake_project.id}/intake-issues/"


@pytest.fixture
def make_intake_issue(intake_project, workspace_with_members, admin_user):
    def _make(intake, status_value=-2, name="Reported"):
        triage = State.all_state_objects.filter(project=intake_project, group="triage").first() or State.objects.create(
            name="Triage",
            group="triage",
            sequence=65000,
            color="#4E5355",
            project=intake_project,
            workspace=workspace_with_members,
        )
        issue = Issue.objects.create(
            name=name,
            project=intake_project,
            workspace=workspace_with_members,
            state=triage,
            created_by=admin_user,
        )
        intake_issue = IntakeIssue.objects.create(
            intake=intake,
            issue=issue,
            project=intake_project,
            workspace=workspace_with_members,
            status=status_value,
            created_by=admin_user,
        )
        return issue, intake_issue

    return _make


# --- the triage state fallback -----------------------------------------------


@pytest.mark.contract
class TestTriageStateFallback:
    def test_a_project_with_no_triage_state_gets_one(self, admin_client, intake_url, intake_project, intake):
        assert not State.all_state_objects.filter(project=intake_project, group="triage").exists()

        response = admin_client.post(intake_url, {"issue": {"name": "Something is broken"}}, format="json")

        # The endpoint answers 200, not 201, on a successful file — pinned as its contract.
        assert response.status_code == status.HTTP_200_OK, response.data
        triage = State.all_state_objects.get(project=intake_project, group="triage")
        assert triage.name == "Triage"
        assert triage.default is False
        assert Issue.objects.get(id=response.data["issue"]["id"]).state_id == triage.id

    def test_an_existing_triage_state_is_reused(
        self, admin_client, intake_url, intake_project, workspace_with_members, intake
    ):
        existing = State.objects.create(
            name="Needs triage",
            group="triage",
            sequence=65000,
            color="#4E5355",
            project=intake_project,
            workspace=workspace_with_members,
        )

        response = admin_client.post(intake_url, {"issue": {"name": "Something else"}}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert State.all_state_objects.filter(project=intake_project, group="triage").count() == 1
        assert Issue.objects.get(id=response.data["issue"]["id"]).state_id == existing.id

    def test_a_nameless_request_is_still_rejected(self, admin_client, intake_url, intake_project, intake):
        response = admin_client.post(intake_url, {"issue": {}}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"error": "Name is required"}
        assert not State.all_state_objects.filter(project=intake_project, group="triage").exists(), (
            "a rejected request must not leave a state behind"
        )

    def test_an_invalid_priority_is_still_rejected(self, admin_client, intake_url, intake):
        response = admin_client.post(
            intake_url, {"issue": {"name": "Broken", "priority": "catastrophic"}}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"error": "Invalid priority"}


# --- the missing intake issue ------------------------------------------------


@pytest.mark.contract
class TestMissingIntakeIssue:
    def test_updating_an_unknown_work_item_answers_404(
        self, admin_client, intake_url, intake_project, workspace_with_members, admin_user, intake
    ):
        """
        Previously an unresolvable intake issue raised ``DoesNotExist`` out of
        ``get()``, which the caller saw as a 500.
        """
        orphan = Issue.objects.create(
            name="Not in intake",
            project=intake_project,
            workspace=workspace_with_members,
            state=State.objects.filter(project=intake_project, group="backlog").first(),
            created_by=admin_user,
        )

        response = admin_client.patch(f"{intake_url}{orphan.id}/", {"status": 1}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data == {"error": "Intake issue not found"}

    def test_deleting_an_unknown_work_item_answers_404(
        self, admin_client, intake_url, intake_project, workspace_with_members, admin_user, intake
    ):
        orphan = Issue.objects.create(
            name="Not in intake",
            project=intake_project,
            workspace=workspace_with_members,
            state=State.objects.filter(project=intake_project, group="backlog").first(),
            created_by=admin_user,
        )

        response = admin_client.delete(f"{intake_url}{orphan.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data == {"error": "Intake issue not found"}

    def test_an_intake_issue_in_a_second_intake_is_still_reachable(
        self, admin_client, intake_url, intake_project, workspace_with_members, intake, make_intake_issue
    ):
        """
        The lookup no longer goes through "the project's first intake", so a work
        item filed in another intake of the same project is still updatable.
        """
        second_intake = Intake.objects.create(
            name="Escalations", project=intake_project, workspace=workspace_with_members
        )
        issue, intake_issue = make_intake_issue(second_intake)

        response = admin_client.patch(f"{intake_url}{issue.id}/", {"status": -1}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        intake_issue.refresh_from_db()
        assert intake_issue.status == -1


# --- deleting an intake issue ------------------------------------------------


@pytest.mark.contract
class TestDeletingAnIntakeIssue:
    @pytest.mark.parametrize("status_value", [-2, -1, 0, 2])
    def test_an_unaccepted_request_takes_its_work_item_with_it(
        self, admin_client, intake_url, intake, make_intake_issue, status_value
    ):
        issue, _ = make_intake_issue(intake, status_value=status_value)

        response = admin_client.delete(f"{intake_url}{issue.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Issue.objects.filter(id=issue.id).exists()

    def test_an_accepted_request_leaves_the_work_item_in_place(
        self, admin_client, intake_url, intake, make_intake_issue
    ):
        """Once accepted, the work item belongs to the project, not to intake."""
        issue, _ = make_intake_issue(intake, status_value=1)

        response = admin_client.delete(f"{intake_url}{issue.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Issue.objects.filter(id=issue.id).exists()
        assert not IntakeIssue.objects.filter(issue_id=issue.id).exists()
