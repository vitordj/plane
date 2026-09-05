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

from plane.db.models import (
    AssignmentMode,
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    OrganizationalUnitAssignmentPolicy,
    ProjectMember,
    QueueReason,
    RoutingState,
)

from .conftest import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    issue_assign_url,
    issue_unit_url,
    unit_policy_url,
    unit_project_policy_url,
)


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
        # Load is counted off the routing link's primary executor, not off any
        # assignee, so the busy item has to be owned by the area to count.
        busy = make_issue(project_with_admin, name="Busy work")
        IssueAssignee.objects.create(
            issue=busy, assignee=plain_user, project=project_with_admin, workspace=workspace_with_members
        )
        IssueOrganizationalUnit.objects.create(
            issue=busy,
            organizational_unit=staffed_unit,
            project=project_with_admin,
            workspace=workspace_with_members,
            routing_state=RoutingState.ASSIGNED,
            primary_executor=plain_user,
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

    def test_assigning_makes_the_area_responsible_and_records_the_decision(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        staffed_unit,
        plain_user,
        second_user,
        make_issue,
    ):
        """
        The route goes through the service now, so an assignment made from it
        leaves the same trail as one made anywhere else: a responsibility link,
        a routing state, and a decision saying who was considered. Before, an
        item the caller named an area for was given an assignee and nothing
        else — no record that the area owned it at all.
        """
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        assert response.status_code == 200
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.organizational_unit_id == staffed_unit.id
        assert link.routing_state == RoutingState.ASSIGNED
        assert str(link.primary_executor_id) == response.data["assigned"]["user_id"]
        assert response.data["routing"]["routing_state"] == RoutingState.ASSIGNED
        decision = link.current_assignment_decision
        assert decision.effective_mode == AssignmentMode.LEAST_LOADED
        assert decision.outcome == "assigned"
        assert {row["user_id"] for row in decision.candidates_snapshot} == {
            str(plain_user.id),
            str(second_user.id),
        }
        assert IssueResponsibilityEvent.objects.filter(issue=issue, to_unit=staffed_unit).exists()

    def test_an_area_that_forbids_the_ranking_refuses_the_button(
        self, admin_client, workspace_with_members, project_with_admin, staffed_unit, make_issue
    ):
        """
        The button asks for `least_loaded`. An area with no policy permits it;
        one that configured `allowed_modes` without it has said so on purpose,
        and the refusal names the reason rather than quietly queueing (I7).
        """
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=staffed_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id)},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error_code"] == 4917
        assert not IssueAssignee.objects.filter(issue=issue).exists()

    def test_asking_for_a_mode_that_queues_reports_it_as_queued(
        self, admin_client, workspace_with_members, project_with_admin, staffed_unit, make_issue
    ):
        """`self_claim` leaves the item waiting for someone to take it, which
        is an outcome and not a failure — the old route had no word for it."""
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(staffed_unit.id), "assignment_mode": AssignmentMode.SELF_CLAIM.value},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["assigned"] is None
        assert response.data["reason"] == "queued"
        assert response.data["routing"]["queue_reason"] == QueueReason.AWAITING_CLAIM
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
            # Both rows go, and hard: an assignment now leaves a routing link
            # whose executor is loaded work, so leaving it behind would make
            # the second run rank different state, not the same state twice.
            IssueAssignee.objects.filter(issue=issue).delete(soft=False)
            IssueOrganizationalUnit.objects.filter(issue=issue).delete(soft=False)

        assert chosen[0] == chosen[1]


@pytest.mark.unit
class TestTheRoutingPayload:
    """
    Marking an area is where its policy applies, so the response says what
    happened to the item — not only which area now owns it. Without that, "I
    set the area, why is nobody on it?" has no answer on screen.
    """

    def test_setting_an_area_reports_the_queue_state(
        self, admin_client, workspace_with_members, project_with_admin, covering_unit, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(covering_unit.id)},
            format="json",
        )

        assert response.status_code == 200
        routing = response.data["routing"]
        assert routing["routing_state"] == RoutingState.QUEUED
        assert routing["queue_reason"] == QueueReason.AWAITING_COORDINATOR
        assert routing["primary_executor"] is None
        assert routing["current_assignment_decision"]["outcome"] == "queued"

    def test_reading_it_back_reports_the_same_state(
        self, admin_client, workspace_with_members, project_with_admin, covering_unit, make_issue
    ):
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(covering_unit.id)}, format="json")

        response = admin_client.get(url)

        assert response.data["routing"]["routing_state"] == RoutingState.QUEUED

    def test_an_item_with_no_area_has_no_routing(
        self, admin_client, workspace_with_members, project_with_admin, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.get(issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id))

        assert response.data == {"organizational_unit": None, "routing": None}

    def test_least_loaded_assigns_on_the_spot(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        covering_unit,
        add_member,
        grant_manual_access,
        plain_user,
        make_issue,
    ):
        add_member(covering_unit, plain_user)
        grant_manual_access(project_with_admin, plain_user)
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covering_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(covering_unit.id)},
            format="json",
        )

        routing = response.data["routing"]
        assert routing["routing_state"] == RoutingState.ASSIGNED
        assert routing["primary_executor"] == str(plain_user.id)
        assert IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()

    def test_clearing_the_area_leaves_the_event_behind(
        self, admin_client, workspace_with_members, project_with_admin, covering_unit, make_issue
    ):
        """
        The link is gone, but "this used to belong to Support" is not (I6).
        Assignees stay: the item goes back to being an ordinary work item.
        """
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(covering_unit.id)}, format="json")

        response = admin_client.delete(url)

        assert response.status_code == 204
        event = IssueResponsibilityEvent.objects.filter(issue=issue).order_by("-created_at").first()
        assert event.from_unit_id == covering_unit.id
        assert event.to_unit_id is None

    def test_moving_an_item_between_areas_records_both(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        covering_unit,
        second_unit,
        link_project,
        make_issue,
    ):
        link_project(second_unit, project_with_admin, ROLE_MEMBER)
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(covering_unit.id)}, format="json")

        admin_client.post(url, {"organizational_unit_id": str(second_unit.id)}, format="json")

        events = IssueResponsibilityEvent.objects.filter(issue=issue).order_by("created_at")
        assert [(event.from_unit_id, event.to_unit_id) for event in events] == [
            (None, covering_unit.id),
            (covering_unit.id, second_unit.id),
        ]


@pytest.mark.unit
class TestThePolicyRoute:
    """What the interface has to ask before offering "assign automatically"."""

    def test_an_area_with_no_policy_answers_with_the_fallback(
        self, admin_client, workspace_with_members, covering_unit
    ):
        response = admin_client.get(unit_policy_url(workspace_with_members.slug, covering_unit.id))

        assert response.status_code == 200
        assert response.data["effective_mode"] == AssignmentMode.MANUAL
        assert response.data["policy_source"] == "fallback"
        assert response.data["policy"] is None
        # Nothing configured forbids nothing: the interface may offer any of
        # the three, and only the default is conservative.
        assert sorted(response.data["allowed_modes"]) == ["least_loaded", "manual", "self_claim"]

    def test_the_project_policy_is_reported_for_that_project(
        self, admin_client, workspace_with_members, project_with_admin, covering_unit, link_project
    ):
        unit_project = covering_unit.unit_projects.get(project=project_with_admin)
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covering_unit,
            workspace=workspace_with_members,
            unit_project=unit_project,
            default_mode=AssignmentMode.SELF_CLAIM,
        )

        response = admin_client.get(
            unit_project_policy_url(workspace_with_members.slug, covering_unit.id, project_with_admin.id)
        )

        assert response.data["effective_mode"] == AssignmentMode.SELF_CLAIM
        assert response.data["policy_source"] == "unit_project"
        assert response.data["policy"]["version"] == 1

    def test_an_unknown_area_is_not_found(self, admin_client, workspace_with_members):
        response = admin_client.get(unit_policy_url(workspace_with_members.slug, uuid.uuid4()))

        assert response.status_code == 404

    def test_a_guest_may_read_it(self, guest_client, workspace_with_members, covering_unit):
        """It is a read, like the rest of the layer's GETs: a Guest who can see
        the area can see how it hands work out."""
        response = guest_client.get(unit_policy_url(workspace_with_members.slug, covering_unit.id))

        assert response.status_code == 200

    def test_somebody_from_another_workspace_cannot(self, outsider_client, workspace_with_members, covering_unit):
        response = outsider_client.get(unit_policy_url(workspace_with_members.slug, covering_unit.id))

        assert response.status_code == 403

    def test_the_kill_switch_closes_it(self, settings, admin_client, workspace_with_members, covering_unit):
        """A disabled layer answers 404 on every one of its routes, the new
        ones included — otherwise the switch is a UI preference."""
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.get(unit_policy_url(workspace_with_members.slug, covering_unit.id))

        assert response.status_code == 404
