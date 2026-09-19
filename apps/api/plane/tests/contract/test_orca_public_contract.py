# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The automation API's promises, over real HTTP, through the reference client.

The unit tests exercise the endpoints through Django's test client, which
shares a transaction with the test. That is the right tool for "does this rule
hold", and the wrong one for the two promises this file is about, both of which
are only true if real concurrency and real commits are involved:

* **replaying 50 operations changes nothing.** Not "returns the same body" —
  leaves the same number of rows in every table the operation writes;
* **two callers racing on one key produce one work item.** Settled by a unique
  constraint in the database, which a shared-transaction test cannot exercise
  at all.

It also runs the client from ``tools/orca-client/``, which is what keeps the
examples in ``docs/orca-public-api.md`` honest: if the guide's flow stops
working, this fails.

Marked ``contract`` and pointed at ``live_server``, so it needs a real HTTP
server, but nothing beyond PostgreSQL — no object storage, no broker.
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests
from django.core.cache import cache

from plane.db.models import (
    APIToken,
    AssignmentDecision,
    AssignmentMode,
    AutomationOperation,
    ExternalWorkItemBinding,
    Issue,
    IssueAssignee,
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

# The reference client is a script under tools/, not an installed package: it
# is meant to be read and copied, not depended on. Importing it by path is what
# makes this suite prove the published example works.
# parents: [0] contract, [1] tests, [2] plane, [3] apps/api, [4] apps, [5] repo root.
CLIENT_DIR = Path(__file__).resolve().parents[5] / "tools" / "orca-client"
if not CLIENT_DIR.is_dir():  # pragma: no cover - a wrong path here fails obscurely
    raise RuntimeError(f"reference client not found at {CLIENT_DIR}")
sys.path.insert(0, str(CLIENT_DIR))

from orca_client import OrcaApiError, OrcaClient, idempotency_key  # noqa: E402

ROLE_ADMIN = 20
ROLE_MEMBER = 15


@pytest.fixture(autouse=True)
def clear_throttle_history():
    """
    Empty the token's rate-limit bucket around every test.

    @description The throttle counts in the shared cache, which outlives the
    test database: without this, a file that issues a hundred requests would
    start failing with 429 the second time it ran inside the same minute, and
    the failure would look like a bug in the endpoint.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def public_api(settings):
    settings.ORCA_ORG_UNITS_ENABLED = True
    settings.ORCA_PUBLIC_API_ENABLED = True
    return settings


@pytest.fixture
def world(transactional_db, public_api):
    """A workspace with an area that covers a project and two people in it."""
    admin = User.objects.create(email="contract-admin@plane.so", username="contract-admin")
    first = User.objects.create(email="contract-one@plane.so", username="contract-one")
    second = User.objects.create(email="contract-two@plane.so", username="contract-two")

    workspace = Workspace.objects.create(name="Contract", slug="contract-ws", owner=admin)
    members = {}
    for user, role in ((admin, ROLE_ADMIN), (first, ROLE_MEMBER), (second, ROLE_MEMBER)):
        members[user] = WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)

    project = Project.objects.create(name="Onboarding", identifier="ONB", workspace=workspace, created_by=admin)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)

    unit = OrganizationalUnit.objects.create(workspace=workspace, name="Compliance", slug="compliance")
    OrganizationalUnitProject.objects.create(
        organizational_unit=unit, project=project, workspace=workspace, default_role=ROLE_MEMBER
    )
    for user in (first, second):
        OrganizationalUnitMembership.objects.create(
            organizational_unit=unit, workspace_member=members[user], workspace=workspace, role="member"
        )
    for user in (admin, first, second):
        ProjectMember.objects.create(
            project=project, member=user, workspace=workspace, role=ROLE_MEMBER, is_active=True
        )
    OrganizationalUnitAssignmentPolicy.objects.create(
        organizational_unit=unit, workspace=workspace, default_mode=AssignmentMode.LEAST_LOADED
    )

    token = APIToken.objects.create(user=admin, label="contract")
    return {
        "workspace": workspace,
        "project": project,
        "unit": unit,
        "admin": admin,
        "first": first,
        "second": second,
        "token": token,
    }


@pytest.fixture
def client(world, live_server):
    return OrcaClient(live_server.url, world["token"].token, world["workspace"].slug)


def create(client, world, n, attempt=None):
    """One deterministic creation, as an integration would issue it."""
    return client.create_work_item(
        project_id=str(world["project"].id),
        source="espo-onboarding",
        external_id=f"cliente-{n}",
        name=f"Validate documents for client {n}",
        unit="compliance",
        mode="least_loaded",
        event_id=f"evt-{n}",
        attempt=attempt,
    )


def counts():
    """Everything one creation writes, so "nothing ran twice" is checkable."""
    return {
        "issues": Issue.objects.count(),
        "decisions": AssignmentDecision.objects.count(),
        "assignees": IssueAssignee.objects.count(),
        "bindings": ExternalWorkItemBinding.objects.count(),
        "operations": AutomationOperation.objects.count(),
    }


@pytest.mark.contract
class TestReplayingTheWholeBatch:
    def test_fifty_creations_run_twice_leave_one_of_everything(self, client, world):
        for n in range(50):
            result = create(client, world, n)
            assert result.replayed is False

        after_first = counts()
        assert after_first["issues"] == 50

        for n in range(50):
            result = create(client, world, n)
            # Every second call is a replay, and says so in the header the
            # client reads back.
            assert result.replayed is True, f"call {n} was not replayed"

        # The real assertion: not "the bodies matched" but "the database did
        # not grow". A replay that re-ran the allocation would show up here as
        # 100 decisions, or 100 assignee rows, long before anybody noticed in
        # the interface.
        assert counts() == after_first

    def test_a_replay_reports_the_original_allocation(self, client, world):
        from plane.app.services.orca import reassign

        first = create(client, world, 900)
        chosen = first["responsibility"]["primary_executor"]["id"]
        issue = Issue.objects.get(pk=first["work_item"]["id"])
        other = world["second"] if str(world["first"].id) == chosen else world["first"]

        # A person acts in the interface between the call and the retry.
        reassign(issue, other)

        replay = create(client, world, 900)

        assert replay.replayed is True
        assert replay["responsibility"]["primary_executor"]["id"] == chosen
        # The API tells the truth about the present when asked for it.
        current = client.get_by_external("espo-onboarding", "cliente-900")
        assert current["responsibility"]["primary_executor"]["id"] == str(other.id)


@pytest.mark.contract
class TestRacing:
    def test_two_callers_on_one_key_create_one_work_item(self, client, world, live_server):
        # Separate sessions: two processes, not two threads sharing a socket.
        clients = [OrcaClient(live_server.url, world["token"].token, world["workspace"].slug) for _ in range(2)]

        def call(each):
            try:
                return create(each, world, 700)
            except OrcaApiError as exc:
                # 409 ORG_OPERATION_IN_PROGRESS is a legitimate outcome for the
                # loser: the first call had not finished when the second
                # arrived. What is not legitimate is a second work item.
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, clients))

        assert Issue.objects.count() == 1
        assert ExternalWorkItemBinding.objects.count() == 1
        assert AutomationOperation.objects.count() == 1
        assert AssignmentDecision.objects.count() == 1
        succeeded = [r for r in results if not isinstance(r, OrcaApiError)]
        assert succeeded, "at least one caller must have been answered"
        for failure in (r for r in results if isinstance(r, OrcaApiError)):
            assert failure.error_message in ("ORG_OPERATION_IN_PROGRESS", "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH")


@pytest.mark.contract
class TestTheKeyIsDerived:
    def test_the_same_event_always_derives_the_same_key(self):
        first = idempotency_key("espo", "cliente-1", "create", "evt-9")
        again = idempotency_key("espo", "cliente-1", "create", "evt-9")

        assert first == again
        assert len(first) == 69
        # Different operations on one event get different keys, or a transfer
        # would replay the creation's answer.
        assert idempotency_key("espo", "cliente-1", "transfer", "evt-9") != first

    def test_a_refused_body_keeps_its_key_until_the_caller_varies_it(self, client, world):
        with pytest.raises(OrcaApiError) as refused:
            client.create_work_item(
                project_id=str(world["project"].id),
                source="espo-onboarding",
                external_id="cliente-800",
                name="Bad request",
                unit="no-such-area",
                event_id="evt-800",
            )
        assert refused.value.error_message == "ORG_UNIT_NOT_IN_WORKSPACE"

        # Fixing the area but keeping the key replays the refusal: the key was
        # spent on the first body. This is the behaviour the guide warns about.
        with pytest.raises(OrcaApiError) as replayed:
            client.create_work_item(
                project_id=str(world["project"].id),
                source="espo-onboarding",
                external_id="cliente-800",
                name="Bad request",
                unit="compliance",
                event_id="evt-800",
            )
        assert replayed.value.error_message == "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH"

        # A new key for the corrected body goes through.
        ok = client.create_work_item(
            project_id=str(world["project"].id),
            source="espo-onboarding",
            external_id="cliente-800",
            name="Bad request",
            unit="compliance",
            event_id="evt-800",
            attempt=2,
        )
        assert ok["binding"]["created"] is True


@pytest.mark.contract
class TestTheTwoNamespacesStaySeparate:
    def test_the_internal_routes_do_not_accept_an_api_key(self, world, live_server):
        url = f"{live_server.url}/api/orca/workspaces/{world['workspace'].slug}/organizational-units/"

        response = requests.get(url, headers={"X-Api-Key": world["token"].token}, timeout=30)

        # The internal API is session-authenticated. A leaked API token must
        # not be a way into it.
        assert response.status_code in (401, 403), response.text

    def test_the_public_routes_do_not_accept_a_session(self, world, live_server, client):
        url = f"{live_server.url}/api/v1/orca/workspaces/{world['workspace'].slug}/units/"

        response = requests.get(url, timeout=30)

        assert response.status_code == 401, response.text

    def test_with_the_key_the_public_route_answers(self, client, world):
        units = client.list_units()

        assert [unit["slug"] for unit in units] == ["compliance"]
        assert units[0]["projects"][0]["policy"]["default_mode"] == AssignmentMode.LEAST_LOADED


@pytest.mark.contract
class TestTheGuidesFlow:
    def test_create_read_reassign_transfer(self, client, world, live_server):
        created = create(client, world, 500)
        issue_id = created["work_item"]["id"]
        assert created["work_item"]["identifier"] == f"ONB-{created['work_item']['sequence_id']}"

        read = client.get_by_external("espo-onboarding", "cliente-500")
        assert read["work_item"]["id"] == issue_id

        chosen = created["responsibility"]["primary_executor"]["id"]
        other = world["second"] if str(world["first"].id) == chosen else world["first"]
        moved = client.reassign(
            project_id=str(world["project"].id),
            issue_id=issue_id,
            decision_id=created["decision"]["id"],
            primary_executor=str(other.id),
            reason="rota",
            event_id="evt-500-reassign",
        )
        assert moved["responsibility"]["primary_executor"]["id"] == str(other.id)

        # The decision the reassignment produced is now the current one, so the
        # old If-Match must be refused.
        with pytest.raises(OrcaApiError) as stale:
            client.reassign(
                project_id=str(world["project"].id),
                issue_id=issue_id,
                decision_id=created["decision"]["id"],
                return_to_queue=True,
                event_id="evt-500-return",
            )
        assert stale.value.status == 412
        assert stale.value.body["current_decision_id"] == moved["decision"]["id"]

        queue = client.list_queue("compliance", routing_state="all")
        assert issue_id in [row["issue_id"] for row in queue]
