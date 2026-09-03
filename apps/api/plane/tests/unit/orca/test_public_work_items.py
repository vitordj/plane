# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The public automation API, from the outside.

This is the surface a robot drives, and robots retry. Most of what is pinned
here is about what happens the second time: the same call must not create a
second work item, a stale view must not silently undo a person's decision, and
a refused call must leave nothing half-made.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    AssignmentDecision,
    ExternalWorkItemBinding,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    ProjectMember,
    State,
    StateGroup,
)
from plane.db.models.organizational_unit import RoutingState
from plane.utils.orca_error_codes import ORCA_ERROR_CODES

from .conftest import ROLE_ADMIN, ROLE_GUEST, ROLE_MEMBER


@pytest.fixture(autouse=True)
def public_api_on(settings):
    """Every test here is about the API being reachable at all."""
    settings.ORCA_PUBLIC_API_ENABLED = True


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def backlog_state(project, workspace_with_members):
    return State.objects.create(
        name="Backlog", group=StateGroup.UNSTARTED.value, project=project, workspace=workspace_with_members, sequence=1
    )


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def executor(covered_unit, project, workspace_with_members, add_member, plain_user):
    add_member(covered_unit, plain_user)
    ProjectMember.objects.create(
        project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
    )
    return plain_user


@pytest.fixture
def token_client(admin_user, workspace_with_members, project_admin):
    token = APIToken.objects.create(user=admin_user, workspace=workspace_with_members, label="automation")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def guest_client(guest_user, workspace_with_members, project):
    ProjectMember.objects.create(
        project=project, member=guest_user, workspace=workspace_with_members, role=ROLE_GUEST, is_active=True
    )
    token = APIToken.objects.create(user=guest_user, workspace=workspace_with_members, label="guest automation")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def work_items_url(workspace_with_members, project):
    return f"/api/v1/orca/workspaces/{workspace_with_members.slug}/projects/{project.id}/work-items/"


def payload(unit_slug="compliance", external_id="client-1", mode="default", **assignment):
    body = {
        "external": {"source": "espo", "id": external_id},
        "work_item": {"name": "Validate documents"},
        "responsibility": {"unit": unit_slug, "assignment": {"mode": mode, **assignment}},
    }
    return body


def post(client, url, body, key="key-1", **headers):
    return client.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY=key, **headers)


@pytest.mark.unit
@pytest.mark.django_db
class TestCreatingWork:
    def test_manual_leaves_it_in_the_queue(self, token_client, work_items_url, covered_unit, backlog_state, executor):
        response = post(token_client, work_items_url, payload(covered_unit.slug))

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["responsibility"]["routing_state"] == RoutingState.QUEUED
        assert response.data["binding"]["created"] is True
        assert response.data["operation"]["replay"] is False

    def test_least_loaded_hands_it_to_somebody(
        self, token_client, work_items_url, covered_unit, backlog_state, executor, workspace_with_members
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="least_loaded",
            allowed_modes=["least_loaded"],
        )

        response = post(token_client, work_items_url, payload(covered_unit.slug))

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["responsibility"]["routing_state"] == RoutingState.ASSIGNED
        assert response.data["responsibility"]["primary_executor"]["id"] == str(executor.id)

    def test_explicit_names_the_person(self, token_client, work_items_url, covered_unit, backlog_state, executor):
        response = post(
            token_client,
            work_items_url,
            payload(covered_unit.slug, mode="explicit", primary_executor=str(executor.id)),
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["responsibility"]["primary_executor"]["id"] == str(executor.id)

    def test_an_ineligible_named_person_is_refused(
        self, token_client, work_items_url, covered_unit, backlog_state, outsider_user
    ):
        response = post(
            token_client,
            work_items_url,
            payload(covered_unit.slug, mode="explicit", primary_executor=str(outsider_user.id)),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_EXECUTOR_NOT_ELIGIBLE"]

    def test_assignees_on_the_work_item_are_refused(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        """Assignment is the area's decision; a list smuggled in beside it is not."""
        body = payload(covered_unit.slug)
        body["work_item"]["assignees"] = [str(executor.id)]

        response = post(token_client, work_items_url, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_ASSIGNEES_NOT_ALLOWED_HERE"]

    def test_a_process_block_is_refused_until_phase_four(
        self, token_client, work_items_url, covered_unit, backlog_state
    ):
        body = payload(covered_unit.slug)
        body["process"] = {"source": "espo", "instance_id": "client-1", "template_version": "1"}

        response = post(token_client, work_items_url, body)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_PROCESS_PROJECTION_DISABLED"]

    def test_an_area_that_does_not_cover_the_project_leaves_nothing_behind(
        self, token_client, work_items_url, unit, backlog_state
    ):
        """The transaction is the point: no work item, no binding, no half-state."""
        response = post(token_client, work_items_url, payload(unit.slug))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]
        assert not Issue.objects.filter(name="Validate documents").exists()
        assert not ExternalWorkItemBinding.objects.exists()

    def test_a_guest_token_cannot_create(self, guest_client, work_items_url, covered_unit, backlog_state):
        response = post(guest_client, work_items_url, payload(covered_unit.slug))

        assert response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED)


@pytest.mark.unit
@pytest.mark.django_db
class TestRetrying:
    def test_the_same_key_replays_the_first_answer(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        first = post(token_client, work_items_url, payload(covered_unit.slug))
        second = post(token_client, work_items_url, payload(covered_unit.slug))

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_200_OK
        assert second["Idempotent-Replay"] == "true"
        assert second.data["work_item"]["id"] == first.data["work_item"]["id"]
        assert Issue.objects.filter(name="Validate documents").count() == 1
        assert AssignmentDecision.objects.count() == 1

    def test_the_same_key_with_another_payload_is_refused(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        post(token_client, work_items_url, payload(covered_unit.slug))
        changed = payload(covered_unit.slug)
        changed["work_item"]["name"] = "Something else"

        response = post(token_client, work_items_url, changed)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_IDEMPOTENCY_PAYLOAD_MISMATCH"]

    def test_a_missing_key_is_refused(self, token_client, work_items_url, covered_unit, backlog_state):
        response = token_client.post(work_items_url, payload(covered_unit.slug), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_IDEMPOTENCY_KEY_REQUIRED"]

    def test_a_new_key_for_the_same_external_record_reuses_the_work_item(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        """
        The binding, not the key, is what makes the second call safe: a caller
        that lost its key and retried must not get a duplicate.
        """
        first = post(token_client, work_items_url, payload(covered_unit.slug), key="key-1")
        second = post(token_client, work_items_url, payload(covered_unit.slug), key="key-2")

        assert second.status_code == status.HTTP_200_OK
        assert second.data["work_item"]["id"] == first.data["work_item"]["id"]
        assert second.data["binding"]["created"] is False
        assert Issue.objects.filter(name="Validate documents").count() == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestReadingItBack:
    def test_by_external_reference(
        self, token_client, work_items_url, workspace_with_members, covered_unit, backlog_state, executor
    ):
        created = post(token_client, work_items_url, payload(covered_unit.slug, external_id="client-9"))

        response = token_client.get(
            f"/api/v1/orca/workspaces/{workspace_with_members.slug}/work-items/by-external/espo/client-9/"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["work_item"]["id"] == created.data["work_item"]["id"]

    def test_an_unknown_reference_is_not_found(self, token_client, workspace_with_members):
        response = token_client.get(
            f"/api/v1/orca/workspaces/{workspace_with_members.slug}/work-items/by-external/espo/nope/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_area_queue_lists_what_is_waiting(
        self, token_client, work_items_url, workspace_with_members, covered_unit, backlog_state, executor
    ):
        post(token_client, work_items_url, payload(covered_unit.slug))

        response = token_client.get(
            f"/api/v1/orca/workspaces/{workspace_with_members.slug}/units/{covered_unit.slug}/queue/"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        assert len(rows) == 1
        assert rows[0]["routing_state"] == RoutingState.QUEUED


@pytest.mark.unit
@pytest.mark.django_db
class TestMovingWork:
    def make_item(self, token_client, work_items_url, unit, executor):
        response = post(
            token_client,
            work_items_url,
            payload(unit.slug, mode="explicit", primary_executor=str(executor.id)),
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        return response.data

    def reassign_url(self, workspace, project, issue_id):
        return f"/api/v1/orca/workspaces/{workspace.slug}/projects/{project.id}/work-items/{issue_id}/reassign/"

    def test_reassignment_needs_to_say_what_it_saw(
        self, token_client, work_items_url, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        created = self.make_item(token_client, work_items_url, covered_unit, executor)

        response = post(
            token_client,
            self.reassign_url(workspace_with_members, project, created["work_item"]["id"]),
            {"return_to_queue": True},
            key="reassign-1",
        )

        assert response.status_code == status.HTTP_428_PRECONDITION_REQUIRED
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_IF_MATCH_REQUIRED"]

    def test_a_stale_view_is_refused(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        executor,
        second_user,
        add_member,
    ):
        created = self.make_item(token_client, work_items_url, covered_unit, executor)
        add_member(covered_unit, second_user)
        ProjectMember.objects.create(
            project=project, member=second_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
        )
        stale_decision = created["decision"]["id"]
        url = self.reassign_url(workspace_with_members, project, created["work_item"]["id"])

        # Somebody moves it first.
        post(
            token_client,
            url,
            {"primary_executor": str(second_user.id)},
            key="reassign-1",
            HTTP_IF_MATCH=stale_decision,
        )
        # The automation is still holding the view it read a moment ago.
        response = post(
            token_client,
            url,
            {"primary_executor": str(executor.id)},
            key="reassign-2",
            HTTP_IF_MATCH=stale_decision,
        )

        assert response.status_code == status.HTTP_412_PRECONDITION_FAILED
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_DECISION_STALE"]

    def test_returning_to_the_queue_keeps_the_person_on_the_item(
        self, token_client, work_items_url, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        created = self.make_item(token_client, work_items_url, covered_unit, executor)

        response = post(
            token_client,
            self.reassign_url(workspace_with_members, project, created["work_item"]["id"]),
            {"return_to_queue": True, "reason": "wrong person"},
            key="reassign-1",
            HTTP_IF_MATCH=created["decision"]["id"],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["responsibility"]["routing_state"] == RoutingState.QUEUED
        link = IssueOrganizationalUnit.objects.get(issue_id=created["work_item"]["id"])
        assert link.primary_executor_id is None

    def test_transfer_to_an_area_that_does_not_cover_the_project_is_refused(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        second_unit,
        backlog_state,
        executor,
    ):
        created = self.make_item(token_client, work_items_url, covered_unit, executor)
        url = (
            f"/api/v1/orca/workspaces/{workspace_with_members.slug}/projects/{project.id}"
            f"/work-items/{created['work_item']['id']}/transfer/"
        )

        response = post(token_client, url, {"unit": second_unit.slug}, key="transfer-1")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]


@pytest.mark.unit
@pytest.mark.django_db
class TestTheKillSwitch:
    def test_with_the_api_off_the_routes_do_not_exist(
        self, settings, token_client, work_items_url, covered_unit, backlog_state
    ):
        settings.ORCA_PUBLIC_API_ENABLED = False

        response = post(token_client, work_items_url, payload(covered_unit.slug))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_with_the_layer_off_the_routes_do_not_exist(
        self, settings, token_client, work_items_url, covered_unit, backlog_state
    ):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = post(token_client, work_items_url, payload(covered_unit.slug))

        assert response.status_code == status.HTTP_404_NOT_FOUND
