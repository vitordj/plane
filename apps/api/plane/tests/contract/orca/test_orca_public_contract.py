# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The automation API over real HTTP, with real retries.

Everything else about this API is tested in-process. This file exists for the
properties that only mean something end to end: fifty calls replayed exactly,
two racing calls with the same key, and a human decision surviving a robot's
retry. They are slow — a live server, real sockets, real threads — so they run
in the manual contract job rather than on every pull request.

The idempotency key is derived here the same way ``tools/orca-client`` derives
it. The duplication is deliberate: the test runner only mounts ``apps/api``,
and a contract test that imported the client would test nothing on the machine
where it matters.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from plane.db.models import (
    APIToken,
    AssignmentDecision,
    ExternalWorkItemBinding,
    Issue,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    Project,
    ProjectMember,
    State,
    StateGroup,
    User,
    Workspace,
    WorkspaceMember,
)

ROLE_ADMIN = 20
ROLE_MEMBER = 15
TIMEOUT = 30


def idempotency_key(source, external_id, operation, event_id=""):
    """The same derivation as tools/orca-client — see the module docstring."""
    return hashlib.sha256(f"{source}:{external_id}:{operation}:{event_id}".encode("utf-8")).hexdigest()


@pytest.fixture
def automation_world(db, settings):
    """A workspace with one area, one covered project and four people in it."""
    settings.ORCA_ORG_UNITS_ENABLED = True
    settings.ORCA_PUBLIC_API_ENABLED = True

    owner = User.objects.create(email="owner@orca.test", username="orca-owner", first_name="Owner")
    workspace = Workspace.objects.create(name="Orca Contract", slug="orca-contract", owner=owner)
    WorkspaceMember.objects.create(workspace=workspace, member=owner, role=ROLE_ADMIN)
    project = Project.objects.create(name="Onboarding", identifier="ONB", workspace=workspace, created_by=owner)
    ProjectMember.objects.create(project=project, member=owner, workspace=workspace, role=ROLE_ADMIN, is_active=True)
    State.objects.create(
        name="Backlog", group=StateGroup.UNSTARTED.value, project=project, workspace=workspace, sequence=1
    )

    unit = OrganizationalUnit.objects.create(workspace=workspace, name="Compliance", slug="compliance")
    OrganizationalUnitProject.objects.create(
        organizational_unit=unit, project=project, workspace=workspace, default_role=ROLE_MEMBER
    )
    OrganizationalUnitAssignmentPolicy.objects.create(
        organizational_unit=unit,
        workspace=workspace,
        default_mode="least_loaded",
        allowed_modes=["least_loaded", "manual"],
    )

    people = []
    for index in range(4):
        person = User.objects.create(
            email=f"exec{index}@orca.test", username=f"orca-exec{index}", first_name=f"Exec {index}"
        )
        member = WorkspaceMember.objects.create(workspace=workspace, member=person, role=ROLE_MEMBER)
        OrganizationalUnitMembership.objects.create(
            organizational_unit=unit, workspace_member=member, workspace=workspace
        )
        ProjectMember.objects.create(
            project=project, member=person, workspace=workspace, role=ROLE_MEMBER, is_active=True
        )
        people.append(person)

    token = APIToken.objects.create(user=owner, workspace=workspace, label="contract")
    return {
        "workspace": workspace,
        "project": project,
        "unit": unit,
        "people": people,
        "owner": owner,
        "token": token.token,
    }


def create_payload(external_id, name=None):
    return {
        "external": {"source": "espo", "id": external_id},
        "work_item": {"name": name or f"Validate {external_id}"},
        "responsibility": {"unit": "compliance", "assignment": {"mode": "default"}},
    }


def create(base_url, world, external_id, *, key=None, name=None):
    payload = create_payload(external_id, name)
    return requests.post(
        f"{base_url}/api/v1/orca/workspaces/{world['workspace'].slug}/projects/{world['project'].id}/work-items/",
        data=json.dumps(payload),
        headers={
            "X-Api-Key": world["token"],
            "Content-Type": "application/json",
            "Idempotency-Key": key or idempotency_key("espo", external_id, "create"),
        },
        timeout=TIMEOUT,
    )


@pytest.mark.contract
@pytest.mark.django_db(transaction=True)
class TestRetriesAreFree:
    def test_fifty_creations_replayed_change_nothing(self, plane_server, automation_world):
        """
        The property the whole API is built for. Fifty calls, then the same
        fifty: the second round must move no counter at all.
        """
        base = plane_server.url
        for index in range(50):
            response = create(base, automation_world, f"client-{index}")
            assert response.status_code == 201, response.text

        counts = {
            "issues": Issue.objects.count(),
            "bindings": ExternalWorkItemBinding.objects.count(),
            "decisions": AssignmentDecision.objects.count(),
            "assignees": IssueAssignee.objects.count(),
        }

        for index in range(50):
            response = create(base, automation_world, f"client-{index}")
            assert response.status_code == 200, response.text
            assert response.headers.get("Idempotent-Replay") == "true"
            assert response.json()["operation"]["replay"] is True

        assert counts == {
            "issues": Issue.objects.count(),
            "bindings": ExternalWorkItemBinding.objects.count(),
            "decisions": AssignmentDecision.objects.count(),
            "assignees": IssueAssignee.objects.count(),
        }

    def test_two_racing_calls_create_one_work_item(self, plane_server, automation_world):
        """The retry that arrives while the first call is still running."""
        base = plane_server.url

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: create(base, automation_world, "client-race"), range(2)))

        assert Issue.objects.filter(name="Validate client-race").count() == 1
        # One did the work; the other either replayed it or was told to wait.
        assert sorted(response.status_code for response in responses) in ([200, 201], [201, 409])

    def test_a_replay_does_not_undo_a_human_decision(self, plane_server, automation_world):
        """
        A coordinator moves the work, then the robot retries its creation. The
        replay must return the original snapshot and touch nothing.
        """
        from plane.app.services.orca import reassign

        base = plane_server.url
        first = create(base, automation_world, "client-human")
        assert first.status_code == 201, first.text

        issue = Issue.objects.get(pk=first.json()["work_item"]["id"])
        chosen = IssueOrganizationalUnit.objects.get(issue=issue).primary_executor
        somebody_else = next(person for person in automation_world["people"] if person.id != chosen.id)
        reassign(issue, somebody_else, actor=automation_world["owner"], reason="coordinator knows better")

        replay = create(base, automation_world, "client-human")

        assert replay.status_code == 200
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == somebody_else.id


@pytest.mark.contract
@pytest.mark.django_db(transaction=True)
class TestTheDoorsAreSeparate:
    def test_an_api_key_cannot_open_the_session_api(self, plane_server, automation_world):
        """The internal routes are for people in a browser, not for tokens."""
        response = requests.get(
            f"{plane_server.url}/api/orca/workspaces/{automation_world['workspace'].slug}/organizational-units/",
            headers={"X-Api-Key": automation_world["token"]},
            timeout=TIMEOUT,
        )

        assert response.status_code in (401, 403)

    def test_the_public_api_refuses_a_call_with_no_token(self, plane_server, automation_world):
        response = requests.get(
            f"{plane_server.url}/api/v1/orca/workspaces/{automation_world['workspace'].slug}/units/",
            timeout=TIMEOUT,
        )

        assert response.status_code in (401, 403)

    def test_with_the_api_switched_off_nothing_is_there(self, plane_server, automation_world, settings):
        settings.ORCA_PUBLIC_API_ENABLED = False

        response = requests.get(
            f"{plane_server.url}/api/v1/orca/workspaces/{automation_world['workspace'].slug}/units/",
            headers={"X-Api-Key": automation_world["token"]},
            timeout=TIMEOUT,
        )

        assert response.status_code == 404
