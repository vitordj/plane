# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Work whose executor is no longer there.

The thing being pinned is what the sweep does *not* do. It never picks the
next person: a holiday that silently reassigns three work items is worse than
one that asks. And coming back does not undo anything — by then somebody may
have done the work, so returning is a decision too.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.app.services.orca import set_responsibility
from plane.bgtasks.organizational_availability_task import (
    sweep_unavailable_executors,
    unusable_executor_links,
)
from plane.db.models import (
    AssignmentDecision,
    IssueAssignee,
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitCoordinator,
    ProjectMember,
    WorkspaceMember,
    WorkspaceMemberAvailability,
)
from plane.db.models.organizational_unit import QueueReason, RoutingState


@pytest.fixture
def availability_on(settings):
    settings.ORCA_AVAILABILITY_ENABLED = True


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def executor(covered_unit, project, workspace_with_members, add_member, grant_manual_access, plain_user):
    add_member(covered_unit, plain_user)
    grant_manual_access(project, plain_user)
    return plain_user


@pytest.fixture
def assigned_issue(covered_unit, project, make_issue, executor):
    issue = make_issue(project)
    set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")
    return issue


@pytest.fixture
def send_them_away(workspace_with_members, workspace_member_of):
    def _away(user, days_ahead=7):
        return WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(user),
            workspace=workspace_with_members,
            unavailable_from=timezone.now() - timedelta(hours=1),
            unavailable_until=timezone.now() + timedelta(days=days_ahead),
            reason="vacation",
        )

    return _away


@pytest.mark.unit
@pytest.mark.django_db
class TestFindingUnusableExecutors:
    def test_somebody_on_holiday_is_found(self, availability_on, assigned_issue, send_them_away, executor):
        send_them_away(executor)

        found = unusable_executor_links()

        assert [link.issue_id for link in found] == [assigned_issue.id]
        assert found[0].reason == "away"

    def test_somebody_present_is_not(self, availability_on, assigned_issue):
        assert unusable_executor_links() == []

    def test_somebody_who_left_the_area_is_found(self, availability_on, assigned_issue, covered_unit, executor):
        covered_unit.memberships.update(is_active=False)

        found = unusable_executor_links()

        assert [link.reason for link in found] == ["left_area"]

    def test_somebody_who_lost_project_access_is_found(self, availability_on, assigned_issue, project, executor):
        ProjectMember.objects.filter(project=project, member=executor).update(is_active=False)

        found = unusable_executor_links()

        assert [link.reason for link in found] == ["left_project"]

    def test_somebody_deactivated_in_the_workspace_is_found(
        self, availability_on, assigned_issue, workspace_member_of, executor
    ):
        WorkspaceMember.objects.filter(pk=workspace_member_of(executor).pk).update(is_active=False)

        found = unusable_executor_links()

        assert [link.reason for link in found] == ["left_workspace"]

    def test_a_holiday_that_has_not_started_is_not_found(
        self, availability_on, assigned_issue, workspace_with_members, workspace_member_of, executor
    ):
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(executor),
            workspace=workspace_with_members,
            unavailable_from=timezone.now() + timedelta(days=3),
            unavailable_until=timezone.now() + timedelta(days=10),
        )

        assert unusable_executor_links() == []

    def test_queued_work_is_never_in_it(self, availability_on, covered_unit, project, make_issue, executor):
        """Nothing to return: it is already in the queue."""
        set_responsibility(make_issue(project), covered_unit, trigger="internal_api")

        assert unusable_executor_links() == []


@pytest.mark.unit
@pytest.mark.django_db
class TestTheSweep:
    def test_it_returns_the_work_to_the_queue(self, availability_on, assigned_issue, send_them_away, executor):
        send_them_away(executor)

        result = sweep_unavailable_executors()

        link = IssueOrganizationalUnit.objects.get(issue=assigned_issue)
        assert result == {"returned": 1}
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.EXECUTOR_UNAVAILABLE
        assert link.primary_executor_id is None

    def test_the_person_stays_on_the_work_item(self, availability_on, assigned_issue, send_them_away, executor):
        """So they still see it when they get back — two weeks away is survivable."""
        send_them_away(executor)

        sweep_unavailable_executors()

        assert IssueAssignee.objects.filter(issue=assigned_issue, assignee=executor).exists()

    def test_it_never_hands_the_work_to_somebody_else(
        self,
        availability_on,
        assigned_issue,
        covered_unit,
        project,
        add_member,
        grant_manual_access,
        send_them_away,
        executor,
        second_user,
    ):
        add_member(covered_unit, second_user)
        grant_manual_access(project, second_user)
        send_them_away(executor)

        sweep_unavailable_executors()

        assert IssueOrganizationalUnit.objects.get(issue=assigned_issue).primary_executor_id is None

    def test_the_return_is_recorded_as_a_decision(self, availability_on, assigned_issue, send_them_away, executor):
        send_them_away(executor)

        sweep_unavailable_executors()

        decision = AssignmentDecision.objects.filter(issue=assigned_issue).order_by("-created_at").first()
        assert decision.trigger == "availability"
        assert decision.outcome == "queued"
        assert decision.previous_primary_executor_id == executor.id

    def test_the_coordinators_are_told(
        self,
        availability_on,
        assigned_issue,
        covered_unit,
        workspace_with_members,
        workspace_member_of,
        send_them_away,
        executor,
        second_user,
    ):
        OrganizationalUnitCoordinator.objects.create(
            organizational_unit=covered_unit,
            workspace_member=workspace_member_of(second_user),
            workspace=workspace_with_members,
        )
        send_them_away(executor)

        sweep_unavailable_executors()

        assert Notification.objects.filter(receiver=second_user, entity_identifier=assigned_issue.id).exists()

    def test_a_second_pass_does_nothing(self, availability_on, assigned_issue, send_them_away, executor):
        """The work is queued now, so there is no executor left to find."""
        send_them_away(executor)
        sweep_unavailable_executors()
        before = AssignmentDecision.objects.filter(issue=assigned_issue).count()

        result = sweep_unavailable_executors()

        assert result == {"returned": 0}
        assert AssignmentDecision.objects.filter(issue=assigned_issue).count() == before

    def test_coming_back_gives_nothing_back(self, availability_on, assigned_issue, send_them_away, executor):
        """
        A person returning does not get their work item back. Somebody may have
        done it while they were away, so a coordinator decides — which is why
        the sweep only ever moves work in one direction.
        """
        absence = send_them_away(executor)
        sweep_unavailable_executors()
        absence.delete()

        sweep_unavailable_executors()

        link = IssueOrganizationalUnit.objects.get(issue=assigned_issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None

    def test_it_writes_nothing_while_availability_is_off(self, assigned_issue, send_them_away, executor, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        send_them_away(executor)
        settings.ORCA_AVAILABILITY_ENABLED = False

        result = sweep_unavailable_executors()

        assert result == {"skipped": "availability disabled"}
        assert IssueOrganizationalUnit.objects.get(issue=assigned_issue).routing_state == RoutingState.ASSIGNED

    def test_it_writes_nothing_while_the_layer_is_off(
        self, availability_on, assigned_issue, send_them_away, executor, settings
    ):
        send_them_away(executor)
        settings.ORCA_ORG_UNITS_ENABLED = False

        result = sweep_unavailable_executors()

        assert result == {"skipped": "organizational layer disabled"}


@pytest.mark.unit
@pytest.mark.django_db
class TestRemovingTheAssigneeNatively:
    """
    RFC §12's divergence risk: somebody clears the assignee in the app and the
    area's link goes on saying that person is executing it.
    """

    def test_removing_the_executor_returns_the_work(self, assigned_issue, executor):
        row = IssueAssignee.objects.get(issue=assigned_issue, assignee=executor)

        row.delete()

        link = IssueOrganizationalUnit.objects.get(issue=assigned_issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.EXECUTOR_UNAVAILABLE

    def test_removing_a_collaborator_changes_nothing(
        self, assigned_issue, project, covered_unit, add_member, grant_manual_access, executor, second_user
    ):
        """They were never the one answerable for it."""
        add_member(covered_unit, second_user)
        grant_manual_access(project, second_user)
        collaborator = IssueAssignee.objects.create(
            issue=assigned_issue,
            assignee=second_user,
            project_id=assigned_issue.project_id,
            workspace_id=assigned_issue.workspace_id,
        )

        collaborator.delete()

        assert IssueOrganizationalUnit.objects.get(issue=assigned_issue).routing_state == RoutingState.ASSIGNED

    def test_it_works_without_the_availability_feature(self, assigned_issue, executor):
        """The divergence is real whether or not holidays are switched on."""
        IssueAssignee.objects.get(issue=assigned_issue, assignee=executor).delete()

        assert IssueOrganizationalUnit.objects.get(issue=assigned_issue).routing_state == RoutingState.QUEUED


@pytest.mark.unit
@pytest.mark.django_db
class TestNothingMovesWithoutADecision:
    """
    Phase 3's closing check. Availability changes who the ranking picks and
    hands work back to queues, and every one of those movements has to be
    readable afterwards — otherwise "why is this in the queue again?" has no
    answer, and the feature becomes something people work around.
    """

    def test_every_availability_return_carries_the_availability_trigger(
        self,
        availability_on,
        covered_unit,
        project,
        make_issue,
        add_member,
        grant_manual_access,
        send_them_away,
        executor,
        second_user,
    ):
        add_member(covered_unit, second_user)
        grant_manual_access(project, second_user)
        issues = [make_issue(project, name=f"Item {index}") for index in range(4)]
        for index, issue in enumerate(issues):
            set_responsibility(
                issue,
                covered_unit,
                explicit_executor=executor if index % 2 == 0 else second_user,
                trigger="internal_api",
            )
        send_them_away(executor)

        sweep_unavailable_executors()

        moved = IssueOrganizationalUnit.objects.filter(issue__in=issues, routing_state=RoutingState.QUEUED).values_list(
            "issue_id", flat=True
        )
        assert len(moved) == 2
        for issue_id in moved:
            latest = AssignmentDecision.objects.filter(issue_id=issue_id).order_by("-created_at").first()
            assert latest.trigger == "availability", issue_id

    def test_the_sweep_writes_exactly_one_decision_per_work_item(
        self, availability_on, assigned_issue, send_them_away, executor
    ):
        send_them_away(executor)
        before = AssignmentDecision.objects.filter(issue=assigned_issue).count()

        sweep_unavailable_executors()

        assert AssignmentDecision.objects.filter(issue=assigned_issue).count() == before + 1

    def test_the_sweep_never_writes_project_access(self, availability_on, assigned_issue, send_them_away, executor):
        """I10 again, from the one place a sweep might be tempted to help."""
        send_them_away(executor)
        before = sorted(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active"))

        sweep_unavailable_executors()

        assert sorted(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active")) == before

    def test_accepting_a_suggestion_is_an_ordinary_assignment(
        self,
        availability_on,
        admin_client,
        workspace_with_members,
        covered_unit,
        project,
        assigned_issue,
        add_member,
        grant_manual_access,
        send_them_away,
        executor,
        admin_user,
        second_user,
    ):
        """
        The suggestion is not a separate mechanism — accepting it goes through
        assign-to like any other choice, and only the reason on the decision
        says it came from one.
        """
        add_member(covered_unit, second_user)
        grant_manual_access(project, second_user)
        grant_manual_access(project, admin_user)
        send_them_away(executor)
        sweep_unavailable_executors()

        response = admin_client.post(
            f"/api/orca/workspaces/{workspace_with_members.slug}/projects/{project.id}/issues/"
            f"{assigned_issue.id}/organizational-unit/assign-to/",
            {"primary_executor": str(second_user.id), "reason": "accepted_suggestion"},
            format="json",
        )

        assert response.status_code == 200, response.data
        latest = AssignmentDecision.objects.filter(issue=assigned_issue).order_by("-created_at").first()
        assert latest.reason == "accepted_suggestion"
        assert latest.chosen_assignee_id == second_user.id
