# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The composed work-item operation over an API key (RFC §7.2, item 1.4).

What these tests are really checking is that one HTTP call is atomic in the
sense that matters to an integration: either there is a work item **and** an
area answerable for it **and** a decision saying why, or there is none of it.
A half-done operation is the failure mode this endpoint exists to prevent —
a work item nobody owns is worse than no work item, because somebody has to
find it before anybody can fix it.

The second theme is that a retry is not a second request. A webhook redelivered
after a timeout, a queue worker restarted mid-batch: both arrive as an
identical body under the same key, and both must be answered with what the
first call already did.
"""

from unittest import mock

import pytest

from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    AutomationOperation,
    AutomationOperationStatus,
    ExternalWorkItemBinding,
    Issue,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    RoutingState,
    State,
    StateGroup,
)
from plane.throttles.orca_public import OrcaPublicThrottle

from .conftest import (
    ROLE_GUEST,
    ROLE_MEMBER,
    public_by_external_url,
    public_work_items_url,
)


@pytest.fixture
def world(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user):
    """An area that covers the project, with two people who can hold its work."""
    link_project(unit, project, ROLE_MEMBER)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    return unit


@pytest.fixture
def caller(world, admin_user, project, grant_manual_access, token_client):
    """An API-key client whose user may create work in the project."""
    grant_manual_access(project, admin_user)
    return token_client(admin_user)


def body(external_id="cliente-1", mode="default", **overrides):
    """The smallest complete request body, with one knob per test."""
    payload = {
        "external": {"source": "espo-onboarding", "id": external_id},
        "work_item": {"name": "Validate registration documents"},
        "responsibility": {"unit": "compliance", "assignment": {"mode": mode}},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


def post(client, project, payload, key="key-1", **extra):
    return client.post(
        public_work_items_url(project.workspace.slug, project.id),
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **extra,
    )


@pytest.mark.unit
class TestCreating:
    def test_the_default_mode_queues_the_item_for_the_area(self, caller, project, world):
        response = post(caller, project, body())

        assert response.status_code == 201, response.data
        assert response.data["binding"]["created"] is True
        assert response.data["responsibility"]["unit"]["slug"] == "compliance"
        # No policy means manual (RFC §6.3), so the item waits for a human
        # rather than being handed to whoever happens to be free.
        assert response.data["responsibility"]["routing_state"] == RoutingState.QUEUED
        assert response.data["decision"]["effective_mode"] == AssignmentMode.MANUAL
        assert response.data["decision"]["requested_mode"] == "default"
        assert response.data["operation"]["replay"] is False

    def test_the_work_item_is_an_ordinary_plane_work_item(self, caller, project, world):
        response = post(caller, project, body())

        issue = Issue.objects.get(pk=response.data["work_item"]["id"])
        assert issue.name == "Validate registration documents"
        assert issue.project_id == project.id
        assert issue.sequence_id == response.data["work_item"]["sequence_id"]
        assert response.data["work_item"]["identifier"] == f"{project.identifier}-{issue.sequence_id}"
        # The external key is written to the native columns too, so the
        # upstream API's own by-external lookup keeps working.
        assert issue.external_source == "espo-onboarding"
        assert issue.external_id == "cliente-1"

    def test_nobody_is_assigned_by_the_project_default(self, caller, project, world, plain_user, admin_user):
        # Defect D2: the native serializer falls back to the project's default
        # assignee when `assignees` is empty. On this path the area decides, so
        # the fallback is switched off — and this is the test that says so.
        project.default_assignee = plain_user
        project.save(update_fields=["default_assignee"])

        response = post(caller, project, body())

        issue_id = response.data["work_item"]["id"]
        assert not IssueAssignee.objects.filter(issue_id=issue_id).exists()
        assert response.data["responsibility"]["primary_executor"] is None

    def test_least_loaded_puts_somebody_on_it(self, caller, project, world, workspace_with_members):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=world,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
        )

        response = post(caller, project, body())

        assert response.status_code == 201, response.data
        assert response.data["responsibility"]["routing_state"] == RoutingState.ASSIGNED
        assert response.data["responsibility"]["primary_executor"] is not None
        assert response.data["decision"]["algorithm_version"] == "lb-1"

    def test_self_claim_leaves_it_waiting_to_be_taken(self, caller, project, world):
        response = post(caller, project, body(mode="self_claim"))

        assert response.data["responsibility"]["routing_state"] == RoutingState.QUEUED
        assert response.data["responsibility"]["queue_reason"] == "awaiting_claim"

    def test_explicit_names_the_person_and_the_collaborators(self, caller, project, world, plain_user, second_user):
        payload = body(
            responsibility={
                "unit": "compliance",
                "assignment": {
                    "mode": "explicit",
                    "primary_executor": str(plain_user.id),
                    "collaborators": [str(second_user.id)],
                },
            }
        )

        response = post(caller, project, payload)

        assert response.status_code == 201, response.data
        assert response.data["responsibility"]["primary_executor"]["id"] == str(plain_user.id)
        issue_id = response.data["work_item"]["id"]
        # The collaborator is on the item but is not the one answerable for it.
        assert IssueAssignee.objects.filter(issue_id=issue_id, assignee=second_user).exists()
        assert IssueOrganizationalUnit.objects.get(issue_id=issue_id).primary_executor_id == plain_user.id

    def test_the_decision_records_the_public_api_and_the_operation(self, caller, project, world):
        response = post(caller, project, body())

        decision = AssignmentDecision.objects.get(issue_id=response.data["work_item"]["id"])
        assert decision.trigger == "public_api"
        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert decision.automation_operation_id == operation.id
        assert operation.status == AutomationOperationStatus.SUCCEEDED
        assert operation.issue_id == decision.issue_id

    def test_the_deadline_the_caller_gave_is_the_one_recorded(self, caller, project, world):
        payload = body(
            responsibility={
                "unit": "compliance",
                "assignment": {"mode": "default"},
                "assignment_due_at": "2026-09-30T12:00:00Z",
            }
        )

        response = post(caller, project, payload)

        assert response.data["responsibility"]["assignment_due_at"].startswith("2026-09-30T12:00:00")


@pytest.mark.unit
class TestRetrying:
    def test_the_same_key_and_body_replays_the_first_answer(self, caller, project, world):
        first = post(caller, project, body())
        second = post(caller, project, body())

        assert first.status_code == 201
        assert second.status_code == 201
        assert second["Idempotent-Replay"] == "true"
        assert second.data["operation"]["replay"] is True
        assert second.data["work_item"]["id"] == first.data["work_item"]["id"]
        # Nothing ran twice.
        assert Issue.objects.count() == 1
        assert AssignmentDecision.objects.count() == 1
        assert ExternalWorkItemBinding.objects.count() == 1
        assert AutomationOperation.objects.count() == 1

    def test_a_transient_crash_does_not_burn_the_key(self, caller, project, world):
        """R1.A6: a blip must not spend the key; the retry has to execute."""
        from plane.app.services.orca.assignment_service import set_responsibility as real_place

        calls = {"n": 0}

        def fail_once(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("connection reset")
            return real_place(*args, **kwargs)

        with mock.patch("plane.api.views.orca.work_items.set_responsibility", side_effect=fail_once):
            first = post(caller, project, body())
            assert first.status_code == 500
            assert not AutomationOperation.all_objects.filter(idempotency_key="key-1").exists()
            second = post(caller, project, body())

        assert second.status_code == 201, second.data
        assert second.get("Idempotent-Replay") is None
        assert Issue.objects.filter(project=project).count() == 1
        assert AutomationOperation.objects.filter(idempotency_key="key-1").count() == 1
        assert calls["n"] == 2

    def test_a_replay_answers_the_original_not_the_present(
        self, caller, project, world, workspace_with_members, plain_user, second_user
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=world, workspace=workspace_with_members, default_mode=AssignmentMode.LEAST_LOADED
        )
        first = post(caller, project, body())
        chosen = first.data["responsibility"]["primary_executor"]["id"]

        # A person reassigns it in the interface, between the call and the retry.
        from plane.app.services.orca import reassign

        issue = Issue.objects.get(pk=first.data["work_item"]["id"])
        other = second_user if str(plain_user.id) == chosen else plain_user
        reassign(issue, other)

        replay = post(caller, project, body())

        # Still the first allocation: a retry must not read as though it
        # changed something. A client that wants current state does a GET.
        assert replay.data["responsibility"]["primary_executor"]["id"] == chosen
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == other.id

    def test_the_same_key_with_a_different_body_is_a_conflict(self, caller, project, world):
        post(caller, project, body())

        response = post(caller, project, body(work_item={"name": "Something else"}))

        assert response.status_code == 409
        assert response.data["error_message"] == "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH"
        assert Issue.objects.count() == 1

    def test_a_new_key_for_the_same_external_item_reuses_the_work_item(self, caller, project, world):
        first = post(caller, project, body(), key="key-1")

        second = post(caller, project, body(), key="key-2")

        assert second.status_code == 201
        assert second.data["work_item"]["id"] == first.data["work_item"]["id"]
        assert second.data["binding"]["created"] is False
        assert Issue.objects.count() == 1

    def test_a_new_key_does_not_re_run_the_allocation(
        self, caller, project, world, workspace_with_members, plain_user, second_user
    ):
        # The case this protects: an integration calls this route on every
        # webhook its record emits. Each webhook is a different event, so each
        # derives a different key, so each is a new operation rather than a
        # replay. Re-running the allocation would re-rank an assigned item and
        # hand the work to somebody else every time a comment was added.
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=world, workspace=workspace_with_members, default_mode=AssignmentMode.LEAST_LOADED
        )
        first = post(caller, project, body(), key="key-1")
        chosen = first.data["responsibility"]["primary_executor"]["id"]

        second = post(caller, project, body(), key="key-2")

        assert second.status_code == 201
        assert second.data["binding"]["created"] is False
        assert second.data["responsibility"]["primary_executor"]["id"] == chosen
        # No second decision: nothing was decided, because nothing changed.
        assert AssignmentDecision.objects.count() == 1

    def test_a_new_key_naming_a_different_area_still_transfers(
        self, caller, project, world, second_unit, link_project, plain_user, add_member
    ):
        # A different area in the body is a real instruction, not a repeat.
        link_project(second_unit, project, ROLE_MEMBER)
        post(caller, project, body(), key="key-1")

        response = post(caller, project, body(responsibility={"unit": "legal"}), key="key-2")

        assert response.status_code == 201
        assert response.data["responsibility"]["unit"]["slug"] == "legal"

    def test_a_missing_key_is_refused(self, caller, project, world):
        response = caller.post(public_work_items_url(project.workspace.slug, project.id), body(), format="json")

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_IDEMPOTENCY_KEY_REQUIRED"
        assert not Issue.objects.exists()

    def test_a_key_too_long_for_the_receipt_is_refused(self, caller, project, world):
        response = post(caller, project, body(), key="x" * 256)

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_IDEMPOTENCY_KEY_REQUIRED"


@pytest.mark.unit
class TestRefusing:
    def test_assignees_in_the_work_item_block_are_refused(self, caller, project, world, plain_user):
        response = post(caller, project, body(work_item={"name": "x", "assignees": [str(plain_user.id)]}))

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_ASSIGNEES_NOT_ALLOWED_HERE"
        assert not Issue.objects.exists()

    def test_a_process_block_is_refused_until_phase_four(self, caller, project, world):
        response = post(caller, project, body(process={"source": "espo", "instance_id": "c-1"}))

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_PROCESS_PROJECTION_DISABLED"

    def test_a_completion_deadline_is_kept_on_the_service_level(self, caller, project, world):
        payload = body(responsibility={"unit": "compliance", "completion_due_at": "2026-09-30T12:00:00Z"})

        response = post(caller, project, payload)

        assert response.status_code == 201
        from plane.db.models import IssueServiceLevel

        row = IssueServiceLevel.objects.get(issue_id=response.data["work_item"]["id"])
        assert row.completion_due_at.isoformat().startswith("2026-09-30T12:00")
        assert row.original_completion_due_at == row.completion_due_at
        assert row.source == "manual"

    def test_an_unknown_field_is_refused_rather_than_ignored(self, caller, project, world):
        response = post(caller, project, body(work_item={"name": "x", "asignees": []}))

        assert response.status_code == 400
        # The typo is named, with the accepted keys, so one deploy fixes the
        # integration instead of one field per deploy.
        assert "asignees" in str(response.data["detail"])

    def test_an_unknown_area_is_refused(self, caller, project, world):
        response = post(caller, project, body(responsibility={"unit": "no-such-area"}))

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_UNIT_NOT_IN_WORKSPACE"

    def test_an_area_that_does_not_cover_the_project_is_refused(
        self, caller, project, world, second_unit, second_project
    ):
        response = post(caller, project, body(responsibility={"unit": "legal"}))

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_UNIT_NOT_COVERING_PROJECT"
        assert not Issue.objects.exists()

    def test_a_forbidden_mode_leaves_no_work_item_and_a_failed_receipt(
        self, caller, project, world, workspace_with_members
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=world,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )

        response = post(caller, project, body(mode="least_loaded"))

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_ASSIGNMENT_MODE_NOT_ALLOWED"
        # The transaction took the work item and the binding with it...
        assert not Issue.objects.exists()
        assert not ExternalWorkItemBinding.objects.exists()
        # ...and left the receipt behind, which is the whole reason it is
        # written outside that transaction.
        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert operation.status == AutomationOperationStatus.FAILED
        assert operation.error_code == "ORG_ASSIGNMENT_MODE_NOT_ALLOWED"

    def test_a_failure_replays_as_the_same_failure(self, caller, project, world, workspace_with_members):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=world,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )
        post(caller, project, body(mode="least_loaded"))

        replay = post(caller, project, body(mode="least_loaded"))

        # Not a 200 with an error body: the status is part of what is replayed.
        assert replay.status_code == 400
        assert replay["Idempotent-Replay"] == "true"
        assert replay.data["error_message"] == "ORG_ASSIGNMENT_MODE_NOT_ALLOWED"

    def test_an_ineligible_named_executor_is_refused(self, caller, project, world, guest_user, grant_manual_access):
        grant_manual_access(project, guest_user, ROLE_GUEST)
        payload = body(
            responsibility={
                "unit": "compliance",
                "assignment": {"mode": "explicit", "primary_executor": str(guest_user.id)},
            }
        )

        response = post(caller, project, payload)

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_EXECUTOR_NOT_ELIGIBLE"
        assert not Issue.objects.exists()

    def test_explicit_without_a_person_is_refused(self, caller, project, world):
        payload = body(responsibility={"unit": "compliance", "assignment": {"mode": "explicit"}})

        response = post(caller, project, payload)

        assert response.status_code == 400
        assert "primary_executor" in str(response.data["detail"])

    def test_naming_a_person_without_explicit_is_refused(self, caller, project, world, plain_user):
        # Otherwise a caller could believe it had assigned somebody while the
        # area's policy quietly decided something else.
        payload = body(
            responsibility={
                "unit": "compliance",
                "assignment": {"mode": "least_loaded", "primary_executor": str(plain_user.id)},
            }
        )

        response = post(caller, project, payload)

        assert response.status_code == 400
        assert "primary_executor" in str(response.data["detail"])

    def test_an_external_key_held_in_another_project_is_a_conflict(
        self, caller, project, world, second_project, make_issue
    ):
        other = make_issue(second_project)
        ExternalWorkItemBinding.objects.create(
            workspace=project.workspace, external_source="espo-onboarding", external_id="cliente-1", issue=other
        )

        response = post(caller, project, body())

        assert response.status_code == 409
        assert response.data["error_message"] == "ORG_EXTERNAL_BINDING_CONFLICT"
        assert response.data["issue_id"] == str(other.id)


@pytest.mark.unit
class TestWhoMayCall:
    def test_a_guest_token_cannot_create_work(self, project, world, guest_user, grant_manual_access, token_client):
        grant_manual_access(project, guest_user, ROLE_GUEST)
        client = token_client(guest_user)

        response = post(client, project, body())

        assert response.status_code == 403
        assert not Issue.objects.exists()

    def test_somebody_outside_the_project_cannot_create_work(self, project, world, second_user, token_client):
        # A workspace member with no membership in this project.
        from plane.db.models import ProjectMember

        ProjectMember.objects.filter(project=project, member=second_user).delete()
        client = token_client(second_user)

        response = post(client, project, body())

        assert response.status_code == 403

    def test_a_session_without_a_token_is_refused(self, project, world, admin_client, public_api_on):
        response = admin_client.post(public_work_items_url(project.workspace.slug, project.id), body(), format="json")

        # The namespace speaks API keys only (Gate 1). A logged-in browser is
        # not an integration.
        assert response.status_code in (401, 403)

    def test_the_switch_hides_the_route_entirely(self, caller, project, world, settings):
        settings.ORCA_PUBLIC_API_ENABLED = False

        response = post(caller, project, body())

        assert response.status_code == 404
        assert response.data["error_message"] == "ORG_PUBLIC_API_DISABLED"

    def test_the_budget_is_per_token(self, caller, project, world, monkeypatch):
        # The rate is a class attribute read at import, like upstream's own
        # ApiKeyRateThrottle — so changing the setting in a test would do
        # nothing, and the limit needs a restart in production too.
        monkeypatch.setattr(OrcaPublicThrottle, "rate", "2/minute")

        first = post(caller, project, body("a"), key="k-a")
        second = post(caller, project, body("b"), key="k-b")
        third = post(caller, project, body("c"), key="k-c")

        assert first.status_code == 201
        assert second.status_code == 201
        assert third.status_code == 429


@pytest.mark.unit
class TestTheNativeActivity:
    def test_creation_announces_the_work_item_like_any_other(
        self, caller, project, world, django_capture_on_commit_callbacks
    ):
        with (
            mock.patch("plane.api.views.orca.work_items.issue_activity") as activity,
            mock.patch("plane.api.views.orca.work_items.model_activity") as webhook,
        ):
            with django_capture_on_commit_callbacks(execute=True):
                response = post(caller, project, body())

        assert response.status_code == 201
        assert activity.delay.called
        assert webhook.delay.called
        assert activity.delay.call_args.kwargs["type"] == "issue.activity.created"

    def test_a_rollback_announces_nothing(
        self, caller, project, world, workspace_with_members, django_capture_on_commit_callbacks
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=world,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )

        with (
            mock.patch("plane.api.views.orca.work_items.issue_activity") as activity,
            mock.patch("plane.api.views.orca.work_items.model_activity") as webhook,
        ):
            with django_capture_on_commit_callbacks(execute=True):
                response = post(caller, project, body(mode="least_loaded"))

        assert response.status_code == 400
        # No work item was created, so nothing may be announced — a webhook for
        # an item that does not exist is worse than a missing one.
        assert not activity.delay.called
        assert not webhook.delay.called

    def test_a_broker_that_is_down_does_not_fail_a_committed_operation(
        self, caller, project, world, django_capture_on_commit_callbacks
    ):
        # These run after the commit, so the work item cannot be taken back. If
        # the failure were allowed to propagate, the caller would get 500 for
        # an operation that succeeded, the receipt would be marked failed, and
        # every retry with that key would replay the 500 — leaving a real work
        # item the calling system believes does not exist.
        with mock.patch("plane.api.views.orca.work_items.issue_activity") as activity:
            activity.delay.side_effect = OSError("broker unreachable")
            with django_capture_on_commit_callbacks(execute=True):
                response = post(caller, project, body())

        assert response.status_code == 201
        assert Issue.objects.count() == 1
        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert operation.status == AutomationOperationStatus.SUCCEEDED

    def test_a_replay_announces_nothing(self, caller, project, world, django_capture_on_commit_callbacks):
        post(caller, project, body())

        with mock.patch("plane.api.views.orca.work_items.issue_activity") as activity:
            with django_capture_on_commit_callbacks(execute=True):
                post(caller, project, body())

        assert not activity.delay.called


@pytest.mark.unit
class TestReadingByExternalKey:
    def test_the_caller_finds_its_own_item_again(self, caller, project, world):
        created = post(caller, project, body())

        response = caller.get(public_by_external_url(project.workspace.slug, "espo-onboarding", "cliente-1"))

        assert response.status_code == 200
        assert response.data["work_item"]["id"] == created.data["work_item"]["id"]
        assert response.data["binding"]["created"] is False
        # A read is not an operation.
        assert response.data["operation"] is None

    def test_the_read_shows_the_present_where_a_replay_shows_the_past(
        self, caller, project, world, workspace_with_members, plain_user, second_user
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=world, workspace=workspace_with_members, default_mode=AssignmentMode.LEAST_LOADED
        )
        created = post(caller, project, body())
        chosen = created.data["responsibility"]["primary_executor"]["id"]

        from plane.app.services.orca import reassign

        issue = Issue.objects.get(pk=created.data["work_item"]["id"])
        other = second_user if str(plain_user.id) == chosen else plain_user
        reassign(issue, other)

        response = caller.get(public_by_external_url(project.workspace.slug, "espo-onboarding", "cliente-1"))

        assert response.data["responsibility"]["primary_executor"]["id"] == str(other.id)

    def test_an_unknown_key_is_not_found(self, caller, project, world):
        response = caller.get(public_by_external_url(project.workspace.slug, "espo-onboarding", "nope"))

        assert response.status_code == 404
        assert response.data["error_message"] == "ORG_WORK_ITEM_NOT_FOUND"

    def test_somebody_outside_the_project_cannot_read_it(self, caller, project, world, second_user, token_client):
        post(caller, project, body())
        from plane.db.models import ProjectMember

        ProjectMember.objects.filter(project=project, member=second_user).delete()
        stranger = token_client(second_user)

        response = stranger.get(public_by_external_url(project.workspace.slug, "espo-onboarding", "cliente-1"))

        assert response.status_code == 403
