# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
HTTP contract tests for item 2.2: the coordinator's routes.

Everything here is the permission matrix of RFC §10 applied endpoint by
endpoint (Admin ws, Member of the project, Member of another project, Guest,
coordinator of the area, coordinator of another area, a lead with no
coordination, a member under self_claim vs. manual), plus the invariants the
night's plan calls out by name: optimistic concurrency (two claims, a stale
``expected_decision_id``), exactly one ``AssignmentDecision`` per action, the
flag closing every route, and the one that matters most —
``claim → return → reassign`` leaving ``ProjectMember`` exactly as it found
it, because deciding who executes an item must never be a way to grant
project access.
"""

import pytest

from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitCoordinator,
    ProjectMember,
    RoutingState,
)

from .conftest import (
    ROLE_MEMBER,
    coordinator_url,
    coordinators_url,
    issue_claim_url,
    issue_reassign_url,
    issue_return_url,
    issue_transfer_url,
    unit_decisions_url,
    unit_policy_url,
    unit_queue_url,
)


# --- shared fixtures -----------------------------------------------------


@pytest.fixture
def covered(unit, project, link_project):
    """The area covers the project — precondition for owning work in it."""
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def project_member_of(workspace_with_members):
    """Make a user a native project member, without going through a unit."""

    def _add(project, user, role=ROLE_MEMBER):
        return ProjectMember.objects.create(
            project=project, member=user, workspace=workspace_with_members, role=role, is_active=True
        )

    return _add


@pytest.fixture
def eligible_member(covered, unit, project, add_member, grant_manual_access, plain_user):
    """A person who is in the area and can hold work on the covered project."""
    add_member(unit, plain_user)
    grant_manual_access(project, plain_user)
    return plain_user


@pytest.fixture
def queued_link(covered, unit, project, make_issue):
    issue = make_issue(project)
    return IssueOrganizationalUnit.objects.create(
        issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
    )


@pytest.fixture
def assigned_link(queued_link, eligible_member):
    queued_link.routing_state = RoutingState.ASSIGNED
    queued_link.primary_executor = eligible_member
    queued_link.save()
    return queued_link


def decision_count(issue):
    return AssignmentDecision.objects.filter(issue=issue).count()


@pytest.mark.unit
class TestFeatureFlag:
    def test_a_disabled_layer_closes_the_queue_route(self, settings, admin_client, workspace_with_members, unit):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.get(unit_queue_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 404

    def test_a_disabled_layer_closes_the_claim_route(
        self, settings, admin_client, workspace_with_members, project, queued_link
    ):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert response.status_code == 404


@pytest.mark.unit
class TestClaim:
    """RFC §10: claim is gated by native project permission, not unit role."""

    def test_a_project_member_claims(
        self, member_client, workspace_with_members, project, queued_link, add_member, grant_manual_access, plain_user
    ):
        add_member(queued_link.organizational_unit, plain_user)
        grant_manual_access(project, plain_user)

        response = member_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert response.status_code == 200
        assert response.data["routing_state"] == RoutingState.ASSIGNED
        queued_link.refresh_from_db()
        assert queued_link.primary_executor_id == plain_user.id
        assert decision_count(queued_link.issue) == 1

    def test_a_workspace_admin_who_is_not_a_project_member_cannot(
        self, admin_client, workspace_with_members, project, queued_link
    ):
        """These routes are project-scoped like the other issue routes (see
        test_issue_organizational_unit_http.py): workspace admin alone is not
        automatically a project member."""
        response = admin_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert response.status_code == 403

    def test_a_member_of_another_project_cannot(
        self,
        workspace_with_members,
        project,
        second_project,
        queued_link,
        project_member_of,
        second_client,
        second_user,
    ):
        project_member_of(second_project, second_user)

        response = second_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert response.status_code == 403

    def test_a_guest_cannot(
        self, guest_client, workspace_with_members, project, queued_link, project_member_of, guest_user
    ):
        project_member_of(project, guest_user, role=5)

        response = guest_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert response.status_code == 403

    def test_an_outsider_cannot(self, outsider_client, workspace_with_members, project, queued_link):
        response = outsider_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert response.status_code == 403

    def test_a_work_item_with_no_responsible_unit_is_not_found(
        self, member_client, workspace_with_members, project, make_issue, project_member_of, plain_user
    ):
        project_member_of(project, plain_user)
        issue = make_issue(project)

        response = member_client.post(issue_claim_url(workspace_with_members.slug, project.id, issue.id))

        assert response.status_code == 404
        assert response.data["error_message"] == "ORG_WORK_ITEM_HAS_NO_UNIT"

    def test_a_second_claim_answers_409(
        self,
        member_client,
        second_client,
        workspace_with_members,
        project,
        queued_link,
        eligible_member,
        project_member_of,
        second_user,
    ):
        # The state check in claim() fires before eligibility is checked, so
        # the second caller only needs the native project permission that
        # gets them past the route's own guard — being "already claimed" does
        # not depend on whether the loser could have held the work at all.
        project_member_of(project, second_user)

        first = member_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))
        assert first.status_code == 200

        second = second_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert second.status_code == 409
        assert second.data["error_message"] == "ORG_WORK_ITEM_ALREADY_CLAIMED"
        assert decision_count(queued_link.issue) == 1

    def test_self_claim_forbidden_by_policy_is_refused(
        self, member_client, workspace_with_members, project, queued_link, eligible_member
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=queued_link.organizational_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )

        response = member_client.post(issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_ASSIGNMENT_MODE_NOT_ALLOWED"

    def test_manual_only_policy_still_lets_a_coordinator_reassign(
        self, admin_client, workspace_with_members, project, queued_link, eligible_member, add_coordinator, admin_user
    ):
        """The policy governs self_claim; a coordinator's own reassign is a
        different route with its own permission, unaffected by allowed_modes."""
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=queued_link.organizational_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )
        add_coordinator(queued_link.organizational_unit, admin_user)

        response = admin_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, queued_link.issue_id),
            {"executor_id": str(eligible_member.id)},
        )

        assert response.status_code == 200


@pytest.mark.unit
class TestReassign:
    def test_the_coordinator_reassigns(
        self,
        admin_client,
        workspace_with_members,
        project,
        assigned_link,
        add_coordinator,
        admin_user,
        second_user,
        add_member,
        grant_manual_access,
    ):
        add_coordinator(assigned_link.organizational_unit, admin_user)
        add_member(assigned_link.organizational_unit, second_user)
        grant_manual_access(project, second_user)

        response = admin_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, assigned_link.issue_id),
            {"executor_id": str(second_user.id)},
        )

        assert response.status_code == 200
        assigned_link.refresh_from_db()
        assert assigned_link.primary_executor_id == second_user.id
        assert decision_count(assigned_link.issue) == 1

    def test_a_member_who_is_not_coordinator_cannot(
        self, member_client, workspace_with_members, project, queued_link, add_member, plain_user
    ):
        add_member(queued_link.organizational_unit, plain_user)

        response = member_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, queued_link.issue_id),
            {"executor_id": str(plain_user.id)},
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"

    def test_a_coordinator_of_another_area_cannot(
        self, second_client, workspace_with_members, project, assigned_link, second_unit, add_coordinator, second_user
    ):
        add_coordinator(second_unit, second_user)

        response = second_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, assigned_link.issue_id),
            {"executor_id": str(second_user.id)},
        )

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"

    def test_a_lead_without_coordination_cannot(
        self, member_client, workspace_with_members, project, queued_link, add_member, plain_user
    ):
        add_member(queued_link.organizational_unit, plain_user, role="lead")

        response = member_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, queued_link.issue_id),
            {"executor_id": str(plain_user.id)},
        )

        assert response.status_code == 403

    def test_a_workspace_admin_may_without_being_a_member(
        self, admin_client, workspace_with_members, project, assigned_link, second_user, add_member, grant_manual_access
    ):
        add_member(assigned_link.organizational_unit, second_user)
        grant_manual_access(project, second_user)

        response = admin_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, assigned_link.issue_id),
            {"executor_id": str(second_user.id)},
        )

        assert response.status_code == 200

    def test_a_guest_cannot(self, guest_client, workspace_with_members, project, assigned_link):
        response = guest_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, assigned_link.issue_id),
            {"executor_id": str(assigned_link.primary_executor_id)},
        )

        assert response.status_code == 403

    def test_an_ineligible_executor_is_refused(
        self, admin_client, workspace_with_members, project, assigned_link, add_coordinator, admin_user, second_user
    ):
        """second_user is neither a unit member nor a project member here."""
        add_coordinator(assigned_link.organizational_unit, admin_user)

        response = admin_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, assigned_link.issue_id),
            {"executor_id": str(second_user.id)},
        )

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_EXECUTOR_NOT_ELIGIBLE"

    def test_a_stale_expected_decision_id_is_refused(
        self,
        admin_client,
        workspace_with_members,
        project,
        assigned_link,
        add_coordinator,
        admin_user,
        second_user,
        add_member,
        grant_manual_access,
    ):
        add_coordinator(assigned_link.organizational_unit, admin_user)
        add_member(assigned_link.organizational_unit, second_user)
        grant_manual_access(project, second_user)
        import uuid

        response = admin_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, assigned_link.issue_id),
            {"executor_id": str(second_user.id), "expected_decision_id": str(uuid.uuid4())},
        )

        assert response.status_code == 409
        assert response.data["error_message"] == "ORG_DECISION_STALE"
        assert decision_count(assigned_link.issue) == 0


@pytest.mark.unit
class TestReturn:
    def test_the_current_executor_returns_their_own_item(
        self, member_client, workspace_with_members, project, assigned_link, eligible_member
    ):
        response = member_client.post(issue_return_url(workspace_with_members.slug, project.id, assigned_link.issue_id))

        assert response.status_code == 200
        assigned_link.refresh_from_db()
        assert assigned_link.routing_state == RoutingState.QUEUED
        assert decision_count(assigned_link.issue) == 1

    def test_the_coordinator_returns_somebody_elses_item(
        self, admin_client, workspace_with_members, project, assigned_link, add_coordinator, admin_user
    ):
        add_coordinator(assigned_link.organizational_unit, admin_user)

        response = admin_client.post(issue_return_url(workspace_with_members.slug, project.id, assigned_link.issue_id))

        assert response.status_code == 200

    def test_a_workspace_admin_without_coordination_may(
        self, admin_client, workspace_with_members, project, assigned_link
    ):
        response = admin_client.post(issue_return_url(workspace_with_members.slug, project.id, assigned_link.issue_id))

        assert response.status_code == 200

    def test_a_bystander_member_of_the_area_cannot(
        self, second_client, workspace_with_members, project, assigned_link, add_member, second_user
    ):
        """A member of the area who is neither coordinator nor the executor."""
        add_member(assigned_link.organizational_unit, second_user)

        response = second_client.post(issue_return_url(workspace_with_members.slug, project.id, assigned_link.issue_id))

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_THE_EXECUTOR"

    def test_a_guest_cannot(self, guest_client, workspace_with_members, project, assigned_link):
        response = guest_client.post(issue_return_url(workspace_with_members.slug, project.id, assigned_link.issue_id))

        assert response.status_code == 403

    def test_returning_a_queued_item_is_an_invalid_transition(
        self, admin_client, workspace_with_members, project, queued_link
    ):
        """A workspace admin has standing to attempt this; the item's own
        state, not the caller's permission, is what makes it a 409 here."""
        response = admin_client.post(issue_return_url(workspace_with_members.slug, project.id, queued_link.issue_id))

        # InvalidTransition answers 400 everywhere else in this codebase (see
        # test_public_reassign_transfer.py); kept consistent here rather than
        # special-cased to the 409 the night's plan sketched for this route.
        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_INVALID_ROUTING_TRANSITION"


@pytest.mark.unit
class TestClaimReturnReassignFlow:
    """
    The invariant the whole architecture rests on: deciding who executes an
    item is not a way to grant project access. ``ProjectMember`` is written
    only by the reconciler (regra 8) — never by claim, return, or reassign.
    """

    def test_project_member_rows_are_unchanged_start_to_finish(
        self,
        member_client,
        admin_client,
        workspace_with_members,
        project,
        queued_link,
        eligible_member,
        add_coordinator,
        admin_user,
        second_user,
        add_member,
        grant_manual_access,
    ):
        add_coordinator(queued_link.organizational_unit, admin_user)
        add_member(queued_link.organizational_unit, second_user)
        grant_manual_access(project, second_user)

        before = list(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active").order_by("id"))

        claim_response = member_client.post(
            issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id)
        )
        assert claim_response.status_code == 200

        return_response = member_client.post(
            issue_return_url(workspace_with_members.slug, project.id, queued_link.issue_id)
        )
        assert return_response.status_code == 200

        # Claim again so there is a current executor for reassign to move.
        second_claim = member_client.post(
            issue_claim_url(workspace_with_members.slug, project.id, queued_link.issue_id)
        )
        assert second_claim.status_code == 200

        reassign_response = admin_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, queued_link.issue_id),
            {"executor_id": str(second_user.id)},
        )
        assert reassign_response.status_code == 200

        after = list(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active").order_by("id"))
        assert before == after
        # claim, return, claim again, reassign: four actions, four decisions.
        assert decision_count(queued_link.issue) == 4


@pytest.mark.unit
class TestQueue:
    def test_a_member_sees_the_queue(
        self, member_client, workspace_with_members, project, queued_link, eligible_member
    ):
        response = member_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.status_code == 200
        assert response.data["viewer"] == {"is_admin": False, "is_coordinator": False, "is_member": True}
        assert len(response.data["results"]) == 1
        row = response.data["results"][0]
        assert row["issue_id"] == str(queued_link.issue_id)
        assert row["project"]["id"] == str(project.id)
        assert "state" in row and "priority" in row and "target_date" in row
        assert row["permissions"] == {"can_claim": True, "can_assign": False, "can_return": False}

    def test_a_coordinator_sees_it_with_assign_permission(
        self, admin_client, workspace_with_members, project, queued_link, add_coordinator, admin_user
    ):
        add_coordinator(queued_link.organizational_unit, admin_user)

        response = admin_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.status_code == 200
        row = response.data["results"][0]
        assert row["permissions"]["can_assign"] is True
        assert row["permissions"]["can_claim"] is False  # not a member, so not eligible to self-claim

    def test_the_executor_of_an_assigned_item_can_return_it(
        self, member_client, workspace_with_members, project, assigned_link, eligible_member
    ):
        response = member_client.get(unit_queue_url(workspace_with_members.slug, assigned_link.organizational_unit_id))

        assert response.status_code == 200
        response = member_client.get(
            unit_queue_url(workspace_with_members.slug, assigned_link.organizational_unit_id)
            + "?routing_state=assigned"
        )
        row = response.data["results"][0]
        assert row["permissions"]["can_return"] is True

    def test_a_guest_cannot_see_the_queue(self, guest_client, workspace_with_members, queued_link):
        response = guest_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.status_code == 403

    def test_a_member_of_the_workspace_outside_the_area_cannot(
        self, second_client, workspace_with_members, queued_link
    ):
        response = second_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.status_code == 403

    def test_a_coordinator_of_another_area_cannot(
        self, second_client, workspace_with_members, queued_link, second_unit, add_coordinator, second_user
    ):
        add_coordinator(second_unit, second_user)

        response = second_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.status_code == 403

    def test_self_claim_disallowed_by_policy_turns_off_can_claim(
        self, member_client, workspace_with_members, project, queued_link, eligible_member
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=queued_link.organizational_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )

        response = member_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.data["results"][0]["permissions"]["can_claim"] is False

    def test_an_unknown_routing_state_is_rejected(
        self, member_client, workspace_with_members, queued_link, eligible_member
    ):
        response = member_client.get(
            unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id) + "?routing_state=bogus"
        )

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_INVALID_QUEUE_FILTER"

    def test_a_process_step_carries_the_run_on_the_row(
        self,
        member_client,
        workspace_with_members,
        project,
        queued_link,
        eligible_member,
        make_issue,
    ):
        """Item 4.6: the inbox groups by ProcessInstanceReference, so the row
        has to name the run and the instance's n/m, not just this page."""
        from plane.db.models import ProcessInstanceItem, ProcessInstanceReference

        instance = ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="espo-onboarding",
            external_instance_id="client-9",
            template_name="onboarding",
            template_version="3",
        )
        ProcessInstanceItem.objects.create(
            process_instance=instance,
            issue=queued_link.issue,
            workspace=workspace_with_members,
            step_key="kyc",
        )
        sibling = make_issue(project, name="Interview")
        ProcessInstanceItem.objects.create(
            process_instance=instance,
            issue=sibling,
            workspace=workspace_with_members,
            step_key="interview",
        )

        response = member_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.status_code == 200
        row = response.data["results"][0]
        assert row["process"]["source"] == "espo-onboarding"
        assert row["process"]["instance_id"] == "client-9"
        assert row["process"]["template_name"] == "onboarding"
        assert row["process"]["step_key"] == "kyc"
        assert row["process"]["done"] == 0
        assert row["process"]["total"] == 2

    def test_an_ordinary_item_has_no_process(self, member_client, workspace_with_members, queued_link, eligible_member):
        response = member_client.get(unit_queue_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.data["results"][0]["process"] is None


@pytest.mark.unit
class TestDecisions:
    def test_the_coordinator_reads_the_log(
        self, admin_client, workspace_with_members, project, queued_link, add_coordinator, admin_user, eligible_member
    ):
        add_coordinator(queued_link.organizational_unit, admin_user)
        AssignmentDecision.objects.create(
            issue=queued_link.issue,
            organizational_unit=queued_link.organizational_unit,
            project=project,
            workspace=workspace_with_members,
            trigger="ui_claim",
            effective_mode="self_claim",
            policy_source="fallback",
            outcome="assigned",
            chosen_assignee=eligible_member,
        )

        response = admin_client.get(unit_decisions_url(workspace_with_members.slug, queued_link.organizational_unit_id))

        assert response.status_code == 200
        assert len(response.data["results"]) == 1
        assert str(response.data["results"][0]["issue"]["id"]) == str(queued_link.issue_id)

    def test_a_member_who_is_not_coordinator_cannot(
        self, member_client, workspace_with_members, queued_link, eligible_member
    ):
        response = member_client.get(
            unit_decisions_url(workspace_with_members.slug, queued_link.organizational_unit_id)
        )

        assert response.status_code == 403


@pytest.mark.unit
class TestCoordinators:
    def test_admin_adds_a_coordinator(
        self, admin_client, workspace_with_members, unit, plain_user, workspace_member_of
    ):
        response = admin_client.post(
            coordinators_url(workspace_with_members.slug, unit.id),
            {"workspace_member_id": str(workspace_member_of(plain_user).id)},
        )

        assert response.status_code == 201
        assert OrganizationalUnitCoordinator.objects.filter(organizational_unit=unit, is_active=True).count() == 1

    def test_accepts_a_user_id_too(self, admin_client, workspace_with_members, unit, plain_user):
        response = admin_client.post(
            coordinators_url(workspace_with_members.slug, unit.id), {"member_id": str(plain_user.id)}
        )

        assert response.status_code == 201

    def test_a_coordinator_reaches_the_areas_projects(
        self, admin_client, workspace_with_members, unit, project, link_project, plain_user
    ):
        link_project(unit, project)

        admin_client.post(coordinators_url(workspace_with_members.slug, unit.id), {"member_id": str(plain_user.id)})

        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_guest_cannot_be_made_coordinator(self, admin_client, workspace_with_members, unit, guest_user):
        response = admin_client.post(
            coordinators_url(workspace_with_members.slug, unit.id), {"member_id": str(guest_user.id)}
        )

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_COORDINATOR_MUST_BE_MEMBER"

    def test_a_duplicate_coordination_is_rejected(
        self, admin_client, workspace_with_members, unit, plain_user, add_coordinator
    ):
        add_coordinator(unit, plain_user)

        response = admin_client.post(
            coordinators_url(workspace_with_members.slug, unit.id), {"member_id": str(plain_user.id)}
        )

        assert response.status_code == 409
        assert response.data["error_message"] == "ORG_COORDINATOR_ALREADY_SET"

    def test_a_non_admin_cannot_add_a_coordinator(self, member_client, workspace_with_members, unit, plain_user):
        response = member_client.post(
            coordinators_url(workspace_with_members.slug, unit.id), {"member_id": str(plain_user.id)}
        )

        assert response.status_code == 403

    def test_a_member_or_coordinator_may_list(
        self, member_client, workspace_with_members, unit, add_member, plain_user
    ):
        add_member(unit, plain_user)

        response = member_client.get(coordinators_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 200

    def test_an_outsider_cannot_list(self, second_client, workspace_with_members, unit):
        response = second_client.get(coordinators_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 403

    def test_admin_removes_a_coordinator(
        self, admin_client, workspace_with_members, unit, plain_user, add_coordinator, project, link_project
    ):
        link_project(unit, project)
        coordinator = add_coordinator(unit, plain_user)
        from plane.app.services.orca import reconcile_coordinator

        reconcile_coordinator(coordinator, force_sync=True)
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

        response = admin_client.delete(coordinator_url(workspace_with_members.slug, unit.id, coordinator.id))

        assert response.status_code == 204
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_removing_an_unknown_coordinator_is_not_found(self, admin_client, workspace_with_members, unit):
        import uuid

        response = admin_client.delete(coordinator_url(workspace_with_members.slug, unit.id, uuid.uuid4()))

        assert response.status_code == 404
        assert response.data["error_message"] == "ORG_COORDINATOR_NOT_FOUND"


@pytest.mark.unit
class TestTransfer:
    def test_the_coordinator_transfers_to_another_covering_area(
        self,
        admin_client,
        workspace_with_members,
        project,
        queued_link,
        second_unit,
        link_project,
        add_coordinator,
        admin_user,
    ):
        link_project(second_unit, project)
        add_coordinator(queued_link.organizational_unit, admin_user)

        response = admin_client.post(
            issue_transfer_url(workspace_with_members.slug, project.id, queued_link.issue_id),
            {"unit_id": str(second_unit.id)},
        )

        assert response.status_code == 200
        queued_link.refresh_from_db()
        assert queued_link.organizational_unit_id == second_unit.id

    def test_a_non_coordinator_cannot_transfer(
        self, member_client, workspace_with_members, project, queued_link, add_member, plain_user
    ):
        add_member(queued_link.organizational_unit, plain_user)

        response = member_client.post(
            issue_transfer_url(workspace_with_members.slug, project.id, queued_link.issue_id),
            {"unit_id": str(queued_link.organizational_unit_id)},
        )

        assert response.status_code == 403

    def test_transferring_to_a_non_covering_area_is_rejected(
        self, admin_client, workspace_with_members, project, queued_link, second_unit, add_coordinator, admin_user
    ):
        add_coordinator(queued_link.organizational_unit, admin_user)

        response = admin_client.post(
            issue_transfer_url(workspace_with_members.slug, project.id, queued_link.issue_id),
            {"unit_id": str(second_unit.id)},
        )

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_UNIT_NOT_COVERING_PROJECT"


@pytest.mark.unit
class TestPolicyPut:
    def test_admin_sets_the_policy(self, admin_client, workspace_with_members, unit):
        response = admin_client.put(
            unit_policy_url(workspace_with_members.slug, unit.id),
            {"default_mode": "self_claim", "allowed_modes": ["self_claim", "manual"]},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["default_mode"] == "self_claim"
        assert response.data["version"] == 1

    def test_updating_increments_the_version(self, admin_client, workspace_with_members, unit):
        admin_client.put(
            unit_policy_url(workspace_with_members.slug, unit.id), {"default_mode": "manual"}, format="json"
        )

        response = admin_client.put(
            unit_policy_url(workspace_with_members.slug, unit.id),
            {"default_mode": "least_loaded", "allowed_modes": ["least_loaded"]},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["version"] == 2

    def test_an_unknown_mode_is_rejected(self, admin_client, workspace_with_members, unit):
        response = admin_client.put(unit_policy_url(workspace_with_members.slug, unit.id), {"default_mode": "bogus"})

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_INVALID_ASSIGNMENT_MODE"

    def test_default_mode_outside_allowed_modes_is_rejected(self, admin_client, workspace_with_members, unit):
        response = admin_client.put(
            unit_policy_url(workspace_with_members.slug, unit.id),
            {"default_mode": "manual", "allowed_modes": ["least_loaded"]},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_INVALID_ASSIGNMENT_MODE"

    def test_a_non_admin_cannot_set_the_policy(self, member_client, workspace_with_members, unit):
        response = member_client.put(unit_policy_url(workspace_with_members.slug, unit.id), {"default_mode": "manual"})

        assert response.status_code == 403

    def test_a_coordinator_who_is_not_admin_cannot_set_the_policy(
        self, member_client, workspace_with_members, unit, add_coordinator, plain_user
    ):
        add_coordinator(unit, plain_user)

        response = member_client.put(unit_policy_url(workspace_with_members.slug, unit.id), {"default_mode": "manual"})

        assert response.status_code == 403


@pytest.mark.unit
class TestClosingAFullInbox:
    """
    Item 2.6: a coordinator empties a thirty-item inbox through the same
    endpoints the Work tab uses. ``ProjectMember`` is identical afterwards,
    and each action writes exactly one ``AssignmentDecision``.
    """

    def test_a_coordinator_empties_thirty_items_without_touching_project_member(
        self,
        admin_client,
        workspace_with_members,
        project,
        unit,
        eligible_member,
        add_coordinator,
        admin_user,
        make_issue,
    ):
        add_coordinator(unit, admin_user)
        links = [
            IssueOrganizationalUnit.objects.create(
                issue=make_issue(project),
                organizational_unit=unit,
                project=project,
                workspace=project.workspace,
            )
            for _ in range(30)
        ]
        before = list(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active").order_by("id"))
        before_decisions = AssignmentDecision.objects.count()

        for link in links:
            response = admin_client.post(
                issue_reassign_url(workspace_with_members.slug, project.id, link.issue_id),
                {"executor_id": str(eligible_member.id)},
            )
            assert response.status_code == 200, response.data
            assert decision_count(link.issue) == 1

        after = list(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active").order_by("id"))
        assert before == after
        assert AssignmentDecision.objects.count() == before_decisions + 30
        assert not IssueOrganizationalUnit.objects.filter(
            organizational_unit=unit, routing_state=RoutingState.QUEUED
        ).exists()
