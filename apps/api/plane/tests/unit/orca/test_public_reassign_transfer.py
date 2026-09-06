# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Changing who holds a work item, and which area owns it (item 1.5).

Both routes exist because an automation's view of the world goes stale. A
ticketing system that decides the customer's case belongs to Legal, a rota that
moves work when somebody goes on leave — they act on a state they read some
time ago, and between the read and the write a person may have acted too.

That is what ``If-Match`` is for on ``reassign``. The header carries the
decision the caller believes is current; a mismatch answers 412 rather than
overwriting, and the caller re-reads. Transfer has no such header on purpose:
"this work belongs to Legal" is not a contested edit of one decision, and both
sides of a race still leave the item in one area with one history.

The status is worth naming: RFC §7.3 specifies **412** here, while the
``DecisionStale`` exception the interface uses carries 409. The exception was
not changed — that would change the interface's contract with the web app —
so the public view maps the status by code.
"""

import pytest

from plane.app.services.orca import reassign as reassign_service
from plane.db.models import (
    AssignmentDecision,
    AutomationOperation,
    AutomationOperationStatus,
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    RoutingState,
    State,
    StateGroup,
)

from .conftest import (
    ROLE_GUEST,
    ROLE_MEMBER,
    public_reassign_url,
    public_transfer_url,
)


@pytest.fixture
def world(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user, admin_user):
    link_project(unit, project, ROLE_MEMBER)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    grant_manual_access(project, admin_user)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    return unit


@pytest.fixture
def caller(world, admin_user, token_client):
    return token_client(admin_user)


@pytest.fixture
def assigned(world, project, make_issue, plain_user):
    """A work item this area owns, with somebody on it."""
    from plane.app.services.orca import set_responsibility

    issue = make_issue(project)
    result = set_responsibility(issue, world, explicit_executor=plain_user)
    return issue, result.decision


def reassign_call(client, project, issue, payload, *, key="r-1", if_match=None, **extra):
    headers = {"HTTP_IDEMPOTENCY_KEY": key}
    if if_match is not None:
        headers["HTTP_IF_MATCH"] = str(if_match)
    headers.update(extra)
    return client.post(
        public_reassign_url(project.workspace.slug, project.id, issue.id), payload, format="json", **headers
    )


def transfer_call(client, project, issue, payload, *, key="t-1"):
    return client.post(
        public_transfer_url(project.workspace.slug, project.id, issue.id),
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


@pytest.mark.unit
class TestReassigning:
    def test_the_item_moves_to_the_named_person(self, caller, project, assigned, second_user):
        issue, decision = assigned

        response = reassign_call(
            caller, project, issue, {"primary_executor": str(second_user.id)}, if_match=decision.id
        )

        assert response.status_code == 200, response.data
        assert response.data["responsibility"]["primary_executor"]["id"] == str(second_user.id)
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == second_user.id

    def test_the_previous_person_stays_on_the_item(self, caller, project, assigned, plain_user, second_user):
        issue, decision = assigned

        reassign_call(caller, project, issue, {"primary_executor": str(second_user.id)}, if_match=decision.id)

        # Plane shows assignees to everyone, and silently detaching somebody is
        # a human's call, not an allocator's (RFC §6.8).
        assert IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()

    def test_the_decision_says_it_was_the_public_api(self, caller, project, assigned, second_user):
        issue, decision = assigned

        response = reassign_call(
            caller, project, issue, {"primary_executor": str(second_user.id), "reason": "rota"}, if_match=decision.id
        )

        new_decision = AssignmentDecision.objects.get(pk=response.data["decision"]["id"])
        assert new_decision.trigger == "public_api"
        assert new_decision.reason == "rota"
        assert new_decision.supersedes_id == decision.id
        assert new_decision.automation_operation_id == AutomationOperation.objects.get(idempotency_key="r-1").id

    def test_a_stale_decision_is_refused_with_412(self, caller, project, assigned, plain_user, second_user):
        issue, stale = assigned
        # Somebody acted in the interface between the caller's read and write.
        reassign_service(issue, second_user)

        response = reassign_call(caller, project, issue, {"primary_executor": str(plain_user.id)}, if_match=stale.id)

        # 412, not the 409 the internal route answers: over HTTP a failed
        # precondition header is a precondition failure.
        assert response.status_code == 412
        assert response.data["error_message"] == "ORG_DECISION_STALE"
        assert response.data["current_decision_id"]
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == second_user.id

    def test_without_if_match_the_request_is_refused_with_428(self, caller, project, assigned, second_user):
        issue, _ = assigned

        response = reassign_call(caller, project, issue, {"primary_executor": str(second_user.id)})

        assert response.status_code == 428
        assert response.data["error_message"] == "ORG_IF_MATCH_REQUIRED"
        # A missing precondition header is a malformed request, not a failed
        # operation: it must not spend the caller's idempotency key.
        assert not AutomationOperation.objects.exists()

    def test_a_quoted_if_match_is_accepted(self, caller, project, assigned, second_user):
        issue, decision = assigned

        response = reassign_call(
            caller, project, issue, {"primary_executor": str(second_user.id)}, if_match=f'"{decision.id}"'
        )

        assert response.status_code == 200

    def test_returning_it_to_the_queue(self, caller, project, assigned, plain_user):
        issue, decision = assigned

        response = reassign_call(caller, project, issue, {"return_to_queue": True}, if_match=decision.id)

        assert response.status_code == 200
        assert response.data["responsibility"]["routing_state"] == RoutingState.QUEUED
        assert response.data["responsibility"]["primary_executor"] is None
        assert IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()

    def test_returning_it_is_optimistic_too(self, caller, project, assigned, second_user):
        issue, stale = assigned
        reassign_service(issue, second_user)

        response = reassign_call(caller, project, issue, {"return_to_queue": True}, if_match=stale.id)

        assert response.status_code == 412
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED

    def test_a_replay_does_not_reassign_twice(self, caller, project, assigned, second_user):
        issue, decision = assigned
        payload = {"primary_executor": str(second_user.id)}

        first = reassign_call(caller, project, issue, payload, if_match=decision.id)
        second = reassign_call(caller, project, issue, payload, if_match=decision.id)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second["Idempotent-Replay"] == "true"
        # Without the receipt the retry would find a stale If-Match — its own
        # first call moved the decision on — and answer 412 to a caller that
        # succeeded.
        assert AssignmentDecision.objects.filter(issue=issue).count() == 2

    def test_asking_for_both_or_neither_is_refused(self, caller, project, assigned, second_user):
        issue, decision = assigned

        both = reassign_call(
            caller,
            project,
            issue,
            {"primary_executor": str(second_user.id), "return_to_queue": True},
            if_match=decision.id,
            key="r-both",
        )
        neither = reassign_call(caller, project, issue, {}, if_match=decision.id, key="r-neither")

        assert both.status_code == 400
        assert neither.status_code == 400

    def test_an_ineligible_person_is_refused(self, caller, project, assigned, guest_user, grant_manual_access):
        issue, decision = assigned
        grant_manual_access(project, guest_user, ROLE_GUEST)

        response = reassign_call(caller, project, issue, {"primary_executor": str(guest_user.id)}, if_match=decision.id)

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_EXECUTOR_NOT_ELIGIBLE"
        operation = AutomationOperation.objects.get(idempotency_key="r-1")
        assert operation.status == AutomationOperationStatus.FAILED

    def test_an_item_no_area_owns_is_refused(self, caller, project, world, make_issue, second_user):
        issue = make_issue(project)

        response = reassign_call(caller, project, issue, {"primary_executor": str(second_user.id)}, if_match="anything")

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_WORK_ITEM_HAS_NO_UNIT"

    def test_an_unknown_work_item_is_not_found(self, caller, project, world, second_user, make_issue, second_project):
        elsewhere = make_issue(second_project)

        response = reassign_call(
            caller, project, elsewhere, {"primary_executor": str(second_user.id)}, if_match="anything"
        )

        assert response.status_code == 404
        assert response.data["error_message"] == "ORG_WORK_ITEM_NOT_FOUND"

    def test_a_guest_token_may_not_reassign(self, project, assigned, guest_user, grant_manual_access, token_client):
        issue, decision = assigned
        grant_manual_access(project, guest_user, ROLE_GUEST)
        client = token_client(guest_user)

        response = reassign_call(client, project, issue, {"return_to_queue": True}, if_match=decision.id)

        assert response.status_code == 403


@pytest.mark.unit
class TestTransferring:
    @pytest.fixture
    def legal(self, second_unit, project, link_project, add_member, grant_manual_access, second_user):
        link_project(second_unit, project, ROLE_MEMBER)
        return second_unit

    def test_the_item_moves_to_the_other_area(self, caller, project, assigned, legal):
        issue, _ = assigned

        response = transfer_call(caller, project, issue, {"unit": "legal", "reason": "regulatory"})

        assert response.status_code == 200, response.data
        assert response.data["responsibility"]["unit"]["slug"] == "legal"
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == legal.id

    def test_both_areas_are_recorded(self, caller, project, assigned, legal, unit):
        issue, _ = assigned

        transfer_call(caller, project, issue, {"unit": "legal"})

        event = IssueResponsibilityEvent.objects.filter(issue=issue, to_unit=legal).get()
        assert event.from_unit_id == unit.id
        assert event.source == "public_api"

    def test_an_executor_who_is_not_in_the_new_area_goes_back_to_the_queue(
        self, caller, project, assigned, legal, plain_user
    ):
        issue, _ = assigned

        response = transfer_call(caller, project, issue, {"unit": "legal"})

        assert response.data["responsibility"]["routing_state"] == RoutingState.QUEUED
        assert response.data["responsibility"]["primary_executor"] is None
        # Kept as a collaborator, so the item does not silently lose its history.
        assert IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()

    def test_an_executor_who_belongs_to_both_areas_keeps_the_item(
        self, caller, project, assigned, legal, plain_user, add_member
    ):
        issue, _ = assigned
        add_member(legal, plain_user)

        response = transfer_call(caller, project, issue, {"unit": "legal"})

        assert response.data["responsibility"]["routing_state"] == RoutingState.ASSIGNED
        assert response.data["responsibility"]["primary_executor"]["id"] == str(plain_user.id)

    def test_an_area_that_does_not_cover_the_project_is_refused(
        self, caller, project, assigned, second_unit, second_project
    ):
        issue, _ = assigned

        response = transfer_call(caller, project, issue, {"unit": "legal"})

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_UNIT_NOT_COVERING_PROJECT"
        operation = AutomationOperation.objects.get(idempotency_key="t-1")
        assert operation.status == AutomationOperationStatus.FAILED

    def test_transferring_to_the_area_that_already_owns_it_is_refused(self, caller, project, assigned):
        issue, _ = assigned

        response = transfer_call(caller, project, issue, {"unit": "compliance"})

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_INVALID_ROUTING_TRANSITION"

    def test_a_replay_does_not_transfer_twice(self, caller, project, assigned, legal):
        issue, _ = assigned

        first = transfer_call(caller, project, issue, {"unit": "legal"})
        second = transfer_call(caller, project, issue, {"unit": "legal"})

        assert first.status_code == 200
        assert second["Idempotent-Replay"] == "true"
        # Without the receipt the retry would hit "already belongs to that
        # area" and answer 400 to a caller whose transfer succeeded.
        assert IssueResponsibilityEvent.objects.filter(issue=issue, to_unit=legal).count() == 1

    def test_a_transfer_needs_an_idempotency_key(self, caller, project, assigned, legal):
        issue, _ = assigned

        response = caller.post(
            public_transfer_url(project.workspace.slug, project.id, issue.id), {"unit": "legal"}, format="json"
        )

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_IDEMPOTENCY_KEY_REQUIRED"
