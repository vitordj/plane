# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The coordinator's surfaces over HTTP (item 2.2).

The service layer is already tested on its own: what these check is the half
that only exists at the edge — who may call each route, what the route hands
back, and that the two gates compose in the right order (the project gate
first, then the area's own role). The permission matrix of RFC §10 is the
point of the file: for every route, a workspace Admin, the area's coordinator,
a coordinator of *another* area, a member of the area, a member of the project
who is in neither, and a Guest.
"""

import pytest
from rest_framework import status

from plane.app.services.orca import allocate, claim as claim_service
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitCoordinator,
    ProjectMember,
    RoutingState,
)
from plane.utils.orca_error_codes import ORCA_ERROR_CODES

from .conftest import (
    ROLE_MEMBER,
    issue_action_url,
    unit_coordinator_url,
    unit_coordinators_url,
    unit_decisions_url,
    unit_policy_write_url,
    unit_project_policy_write_url,
    unit_queue_url,
)


@pytest.fixture
def covered(unit, project, link_project):
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def staffed(covered, unit, project, add_member, grant_manual_access, plain_user, second_user):
    """Two members of the area who can hold work in the project."""
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    return plain_user, second_user


@pytest.fixture
def queued_issue(unit, project, make_issue, staffed):
    """A work item the area owns and nobody is on."""

    def _make(name="Queued item"):
        issue = make_issue(project, name=name)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.QUEUED,
            queue_reason="new_item",
        )
        return issue

    return _make


@pytest.fixture
def admin_in_project(grant_manual_access, project, admin_user):
    """
    The workspace Admin, with access to the project.

    @description The item routes gate on the project first, the way every
    other work-item route in Plane does: a workspace Admin who is not in the
    project cannot see the item, so they cannot move it either. In production
    a coordinator gets that access from the coordination itself (item 2.1);
    here the fixture writes it, so the test is about the area's rules rather
    than about the reconciler.
    """
    from .conftest import ROLE_ADMIN

    return grant_manual_access(project, admin_user, ROLE_ADMIN)


@pytest.fixture
def coordinator_client(admin_client, workspace_with_members, unit, add_coordinator, guest_user, api_client_for):
    """A client for somebody who coordinates the area but is not an admin."""

    def _make(user):
        add_coordinator(unit, user)
        return api_client_for(user)

    return _make


@pytest.fixture
def api_client_for():
    from rest_framework.test import APIClient

    def _make(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _make


@pytest.mark.unit
@pytest.mark.django_db
class TestTheQueueRead:
    def test_a_member_of_the_area_sees_its_queue(self, member_client, workspace_with_members, unit, queued_issue):
        queued_issue("First")
        response = member_client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_200_OK
        assert [row["name"] for row in response.data["items"]] == ["First"]
        assert response.data["capabilities"]["can_claim"] is True
        # A member is not a coordinator: the queue says so, so the interface
        # does not draw a button the API would refuse.
        assert response.data["capabilities"]["can_assign"] is False

    def test_an_admin_sees_it_without_belonging_to_the_area(
        self, admin_client, workspace_with_members, unit, queued_issue
    ):
        queued_issue()
        response = admin_client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["capabilities"]["can_assign"] is True

    def test_a_coordinator_who_is_not_a_member_sees_it(
        self, workspace_with_members, unit, queued_issue, coordinator_client, second_user
    ):
        queued_issue()
        client = coordinator_client(second_user)
        response = client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["capabilities"]["can_assign"] is True

    def test_somebody_in_neither_is_refused(self, guest_client, workspace_with_members, unit, queued_issue):
        queued_issue()
        response = guest_client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_PERMISSION_DENIED"]

    def test_a_coordinator_of_another_area_is_refused(
        self, workspace_with_members, unit, second_unit, queued_issue, add_coordinator, api_client_for, guest_user
    ):
        queued_issue()
        add_coordinator(second_unit, guest_user)
        response = api_client_for(guest_user).get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_someone_outside_the_workspace_is_refused(
        self, outsider_client, workspace_with_members, unit, queued_issue
    ):
        queued_issue()
        response = outsider_client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_overdue_items_come_first(self, member_client, workspace_with_members, unit, queued_issue):
        from django.utils import timezone

        fresh = queued_issue("Fresh")
        late = queued_issue("Late")
        now = timezone.now()
        IssueOrganizationalUnit.objects.filter(issue=fresh).update(queued_at=now)
        IssueOrganizationalUnit.objects.filter(issue=late).update(
            queued_at=now, assignment_due_at=now - timezone.timedelta(hours=1)
        )

        response = member_client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert [row["name"] for row in response.data["items"]] == ["Late", "Fresh"]
        assert response.data["items"][0]["assignment_overdue"] is True
        assert response.data["items"][0]["age_seconds"] >= 0

    def test_the_state_filter_rejects_a_state_that_does_not_exist(
        self, member_client, workspace_with_members, unit, queued_issue
    ):
        queued_issue()
        response = member_client.get(unit_queue_url(workspace_with_members.slug, unit.id) + "?routing_state=nope")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_all_includes_assigned_items(
        self, member_client, workspace_with_members, unit, queued_issue, staffed, project
    ):
        plain_user, _ = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        waiting = member_client.get(unit_queue_url(workspace_with_members.slug, unit.id))
        everything = member_client.get(unit_queue_url(workspace_with_members.slug, unit.id) + "?routing_state=all")

        assert waiting.data["items"] == []
        assert len(everything.data["items"]) == 1
        assert everything.data["items"][0]["primary_executor_detail"]["id"] == str(plain_user.id)

    def test_the_kill_switch_hides_the_queue(self, member_client, workspace_with_members, unit, settings, queued_issue):
        queued_issue()
        settings.ORCA_ORG_UNITS_ENABLED = False
        response = member_client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
@pytest.mark.django_db
class TestClaiming:
    def test_a_member_claims_a_queued_item(
        self, member_client, workspace_with_members, project, queued_issue, plain_user
    ):
        issue = queued_issue()
        response = member_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "claim"))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["routing"]["routing_state"] == RoutingState.ASSIGNED
        assert str(response.data["routing"]["primary_executor"]) == str(plain_user.id)

    def test_the_second_claimer_is_told_who_won(
        self, member_client, workspace_with_members, project, queued_issue, staffed, api_client_for
    ):
        plain_user, second_user = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        response = api_client_for(second_user).post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "claim")
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_WORK_ITEM_ALREADY_CLAIMED"]
        assert response.data["primary_executor_id"] == str(plain_user.id)

    def test_an_admin_who_is_not_in_the_area_cannot_claim(
        self, admin_client, workspace_with_members, project, queued_issue, grant_manual_access, admin_user
    ):
        grant_manual_access(project, admin_user)
        issue = queued_issue()
        response = admin_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "claim"))

        # Claiming is "I am doing this", not an administrative act.
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_item_with_no_area_cannot_be_claimed(
        self, member_client, workspace_with_members, project, make_issue, staffed
    ):
        issue = make_issue(project)
        response = member_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "claim"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_WORK_ITEM_HAS_NO_UNIT"]


@pytest.mark.unit
@pytest.mark.django_db
class TestHandingWorkOut:
    def test_a_coordinator_reassigns(
        self, workspace_with_members, project, unit, queued_issue, staffed, coordinator_client, guest_user
    ):
        plain_user, second_user = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)
        # The coordinator has to be able to see the project; coordinating one
        # of its areas is what grants that (item 2.1), and the reconciler runs
        # when the coordination is created through the API. Here the fixture
        # writes the row directly, so the access is granted the same way.
        ProjectMember.objects.create(
            project=project, member=guest_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
        )
        client = coordinator_client(guest_user)

        response = client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "reassign"),
            {"executor_id": str(second_user.id), "reason": "balancing"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert str(response.data["routing"]["primary_executor"]) == str(second_user.id)
        decision = AssignmentDecision.objects.filter(issue=issue).order_by("-created_at").first()
        assert decision.trigger == "ui_coordinator"
        assert decision.previous_primary_executor_id == plain_user.id

    def test_a_plain_member_cannot_reassign(
        self, member_client, workspace_with_members, project, queued_issue, staffed
    ):
        plain_user, second_user = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        response = member_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "reassign"),
            {"executor_id": str(second_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_stale_reassignment_is_refused(
        self, admin_client, admin_in_project, workspace_with_members, project, queued_issue, staffed
    ):
        plain_user, second_user = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        response = admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "reassign"),
            {
                "executor_id": str(second_user.id),
                "expected_decision_id": "00000000-0000-0000-0000-000000000000",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_DECISION_STALE"]

    def test_reassigning_to_somebody_outside_the_area_is_refused(
        self, admin_client, admin_in_project, workspace_with_members, project, queued_issue, staffed, guest_user
    ):
        plain_user, _ = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        response = admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "reassign"),
            {"executor_id": str(guest_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_EXECUTOR_NOT_ELIGIBLE"]


@pytest.mark.unit
@pytest.mark.django_db
class TestReturningAndSuspending:
    def test_the_executor_returns_their_own_item(
        self, member_client, workspace_with_members, project, queued_issue, staffed
    ):
        plain_user, _ = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        response = member_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "return"))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["routing"]["routing_state"] == RoutingState.QUEUED
        assert response.data["routing"]["queue_reason"] == "manually_returned"

    def test_a_member_cannot_return_somebody_elses_item(
        self, workspace_with_members, project, queued_issue, staffed, api_client_for
    ):
        plain_user, second_user = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        response = api_client_for(second_user).post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "return")
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_coordinator_suspends_and_resumes(
        self, admin_client, admin_in_project, workspace_with_members, project, queued_issue, staffed
    ):
        plain_user, _ = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)

        suspended = admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "suspend"),
            {"reason": "waiting on legal"},
            format="json",
        )
        assert suspended.status_code == status.HTTP_200_OK
        assert suspended.data["routing"]["routing_state"] == RoutingState.SUSPENDED
        assert suspended.data["routing"]["primary_executor"] is None

        resumed = admin_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "return"))
        assert resumed.data["routing"]["routing_state"] == RoutingState.QUEUED

    def test_suspending_twice_is_refused(
        self, admin_client, admin_in_project, workspace_with_members, project, queued_issue
    ):
        issue = queued_issue()
        admin_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "suspend"))
        again = admin_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "suspend"))

        assert again.status_code == status.HTTP_400_BAD_REQUEST
        assert again.data["error_code"] == ORCA_ERROR_CODES["ORG_INVALID_ROUTING_TRANSITION"]

    def test_a_member_cannot_suspend(self, member_client, workspace_with_members, project, queued_issue):
        issue = queued_issue()
        response = member_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "suspend"))

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.unit
@pytest.mark.django_db
class TestTransferring:
    def test_a_coordinator_transfers_to_an_area_that_covers_the_project(
        self,
        admin_client,
        admin_in_project,
        workspace_with_members,
        project,
        unit,
        second_unit,
        queued_issue,
        link_project,
    ):
        link_project(second_unit, project, ROLE_MEMBER)
        issue = queued_issue()

        response = admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "transfer"),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert str(response.data["routing"]["organizational_unit"]["id"]) == str(second_unit.id)

    def test_an_area_that_does_not_cover_the_project_is_refused(
        self, admin_client, admin_in_project, workspace_with_members, project, second_unit, queued_issue
    ):
        issue = queued_issue()
        response = admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "transfer"),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]

    def test_a_member_cannot_transfer(
        self, member_client, workspace_with_members, project, second_unit, queued_issue, link_project
    ):
        link_project(second_unit, project, ROLE_MEMBER)
        issue = queued_issue()

        response = member_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "transfer"),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.unit
@pytest.mark.django_db
class TestCandidates:
    def test_the_ranking_comes_back_with_the_load_behind_it(
        self, member_client, workspace_with_members, project, queued_issue, staffed
    ):
        plain_user, second_user = staffed
        busy = queued_issue("Already on it")
        claim_service(busy, plain_user)
        issue = queued_issue()

        response = member_client.get(issue_action_url(workspace_with_members.slug, project.id, issue.id, "candidates"))

        assert response.status_code == status.HTTP_200_OK
        eligible = [row for row in response.data["candidates"] if row["eligible"]]
        # Least loaded first: the person already holding an item comes second.
        assert [row["user_id"] for row in eligible] == [str(second_user.id), str(plain_user.id)]
        assert eligible[1]["total_open"] == 1

    def test_the_excluded_carry_their_reason(
        self, member_client, workspace_with_members, project, queued_issue, staffed, add_member, guest_user
    ):
        add_member(project.workspace.organizational_units.first(), guest_user)
        issue = queued_issue()

        response = member_client.get(issue_action_url(workspace_with_members.slug, project.id, issue.id, "candidates"))

        excluded = [row for row in response.data["candidates"] if not row["eligible"]]
        assert [row["excluded_reason"] for row in excluded] == ["not_a_project_member"]


@pytest.mark.unit
@pytest.mark.django_db
class TestTheDecisionLog:
    def test_a_coordinator_reads_it_newest_first_with_what_each_replaced(
        self, admin_client, workspace_with_members, unit, project, queued_issue, staffed
    ):
        plain_user, second_user = staffed
        issue = queued_issue()
        claim_service(issue, plain_user)
        allocate(issue, unit, requested_mode=AssignmentMode.LEAST_LOADED, exclude_user_ids=[plain_user.id])

        response = admin_client.get(unit_decisions_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_200_OK
        rows = response.data["results"]
        assert len(rows) == 2
        assert rows[0]["supersedes_detail"]["id"] == rows[1]["id"]

    def test_a_member_of_the_area_cannot_read_it(self, member_client, workspace_with_members, unit, queued_issue):
        queued_issue()
        response = member_client.get(unit_decisions_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.unit
@pytest.mark.django_db
class TestCoordinatorAdministration:
    def test_an_admin_appoints_a_coordinator_and_the_access_follows(
        self, admin_client, workspace_with_members, unit, project, covered, second_user
    ):
        response = admin_client.post(
            unit_coordinators_url(workspace_with_members.slug, unit.id),
            {"member_id": str(second_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ProjectMember.objects.filter(project=project, member=second_user, is_active=True).exists()

    def test_appointing_twice_reuses_the_row(self, admin_client, workspace_with_members, unit, covered, second_user):
        url = unit_coordinators_url(workspace_with_members.slug, unit.id)
        admin_client.post(url, {"member_id": str(second_user.id)}, format="json")
        again = admin_client.post(url, {"member_id": str(second_user.id)}, format="json")

        assert again.status_code == status.HTTP_200_OK
        assert OrganizationalUnitCoordinator.objects.filter(organizational_unit=unit).count() == 1

    def test_removing_a_coordinator_withdraws_what_it_granted(
        self, admin_client, workspace_with_members, unit, project, covered, second_user
    ):
        created = admin_client.post(
            unit_coordinators_url(workspace_with_members.slug, unit.id),
            {"member_id": str(second_user.id)},
            format="json",
        )
        response = admin_client.delete(unit_coordinator_url(workspace_with_members.slug, unit.id, created.data["id"]))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not ProjectMember.objects.filter(project=project, member=second_user, is_active=True).exists()

    def test_somebody_outside_the_workspace_cannot_be_appointed(
        self, admin_client, workspace_with_members, unit, outsider_user
    ):
        response = admin_client.post(
            unit_coordinators_url(workspace_with_members.slug, unit.id),
            {"member_id": str(outsider_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE"]

    def test_a_member_cannot_appoint(self, member_client, workspace_with_members, unit, second_user):
        response = member_client.post(
            unit_coordinators_url(workspace_with_members.slug, unit.id),
            {"member_id": str(second_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_everyone_in_the_workspace_can_read_the_list(
        self, member_client, workspace_with_members, unit, add_coordinator, second_user
    ):
        add_coordinator(unit, second_user)
        response = member_client.get(unit_coordinators_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_200_OK
        assert [row["member_id"] for row in response.data] == [str(second_user.id)]


@pytest.mark.unit
@pytest.mark.django_db
class TestWritingThePolicy:
    def test_an_admin_writes_the_areas_policy(self, admin_client, workspace_with_members, unit):
        response = admin_client.put(
            unit_policy_write_url(workspace_with_members.slug, unit.id),
            {
                "default_mode": "least_loaded",
                "allowed_modes": ["least_loaded", "manual"],
                "assignment_sla_seconds": 3600,
                "max_open_items_per_member": 5,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        policy = OrganizationalUnitAssignmentPolicy.objects.get(organizational_unit=unit, unit_project__isnull=True)
        assert policy.default_mode == "least_loaded"
        assert policy.assignment_sla_seconds == 3600
        # Version starts at 1 and every save moves it, because a decision
        # freezes the number it was decided under.
        assert policy.version == 2

    def test_a_project_policy_is_written_against_its_link(
        self, admin_client, workspace_with_members, unit, project, covered
    ):
        response = admin_client.put(
            unit_project_policy_write_url(workspace_with_members.slug, unit.id, project.id),
            {"default_mode": "self_claim", "allowed_modes": ["self_claim"]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert OrganizationalUnitAssignmentPolicy.objects.filter(
            organizational_unit=unit, unit_project=covered
        ).exists()

    def test_a_project_the_area_does_not_cover_has_no_policy_to_write(
        self, admin_client, workspace_with_members, unit, second_project
    ):
        response = admin_client.put(
            unit_project_policy_write_url(workspace_with_members.slug, unit.id, second_project.id),
            {"default_mode": "manual", "allowed_modes": ["manual"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_LINK_NOT_FOUND"]

    def test_a_mode_that_does_not_exist_is_refused(self, admin_client, workspace_with_members, unit):
        response = admin_client.put(
            unit_policy_write_url(workspace_with_members.slug, unit.id),
            {"default_mode": "whatever", "allowed_modes": ["manual"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_POLICY_INVALID_MODE"]

    def test_explicit_is_not_a_policy_mode(self, admin_client, workspace_with_members, unit):
        # `explicit` bypasses policy resolution entirely (RFC §6.3), so a
        # policy that named it as its default would describe nothing.
        response = admin_client.put(
            unit_policy_write_url(workspace_with_members.slug, unit.id),
            {"default_mode": "explicit", "allowed_modes": ["explicit"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_default_outside_the_allowed_list_is_refused(self, admin_client, workspace_with_members, unit):
        response = admin_client.put(
            unit_policy_write_url(workspace_with_members.slug, unit.id),
            {"default_mode": "least_loaded", "allowed_modes": ["manual"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_POLICY_DEFAULT_MODE_NOT_ALLOWED"]

    def test_a_later_write_is_judged_against_the_row_it_updates(self, admin_client, workspace_with_members, unit):
        url = unit_policy_write_url(workspace_with_members.slug, unit.id)
        admin_client.put(url, {"default_mode": "manual", "allowed_modes": ["manual"]}, format="json")

        response = admin_client.put(url, {"default_mode": "least_loaded"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_POLICY_DEFAULT_MODE_NOT_ALLOWED"]

    def test_a_negative_limit_is_refused(self, admin_client, workspace_with_members, unit):
        response = admin_client.put(
            unit_policy_write_url(workspace_with_members.slug, unit.id),
            {"max_open_items_per_member": -1},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_POLICY_INVALID_VALUE"]

    def test_a_member_cannot_write_a_policy(self, member_client, workspace_with_members, unit):
        response = member_client.put(
            unit_policy_write_url(workspace_with_members.slug, unit.id),
            {"default_mode": "manual", "allowed_modes": ["manual"]},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
