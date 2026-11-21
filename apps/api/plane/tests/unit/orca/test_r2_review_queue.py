# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Review follow-up for the area queue: it must agree with native Plane.

Pins the batch-B gaps of the R2 review: notification payloads the web inbox can
render (R2.08), native-assignee adoption and the queue-returned executor
(R2.05), the public-API seam (R2.05), departed members and drift-sweep alerts
(R2.09 / R2.05), and executive metrics that agree with the queue (R2.06).
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status

from plane.app.services.orca import return_to_queue, set_responsibility
from plane.app.services.orca.alerts import (
    ALERT_ALLOCATION_FAILED,
    ALERT_ASSIGNMENT_SLA,
    ALERT_EXECUTOR_UNAVAILABLE,
    build_notification_data,
    notify,
)
from plane.app.services.orca.assignment_service import _emit_assignee_activity_safely
from plane.app.services.orca.executive_metrics import drill_down_queryset, unit_metrics
from plane.bgtasks.organizational_queue_task import sweep_routing_drift
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitAssignmentPolicy,
    QueueReason,
    RoutingState,
    State,
    StateGroup,
    WorkspaceMember,
)

from .conftest import ROLE_ADMIN, ROLE_MEMBER

# What the web inbox (notification-card/item.tsx and content.tsx) dereferences.
CARD_ISSUE_KEYS = {"id", "name", "identifier", "sequence_id", "state_name", "state_group"}
CARD_ACTIVITY_KEYS = {"id", "verb", "field", "actor", "new_value", "old_value", "issue_comment"}


@pytest.fixture
def staffed_unit(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user, admin_user):
    link_project(unit, project, ROLE_MEMBER)
    grant_manual_access(project, admin_user, ROLE_ADMIN)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user, ROLE_MEMBER)
    return unit


def issue_url(workspace, project, issue):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"


def decisions_for(issue):
    return AssignmentDecision.objects.filter(issue=issue).count()


def assert_card_can_render(data):
    """Every property the inbox reads exists, and the card is not hidden (non-empty field)."""
    assert CARD_ISSUE_KEYS <= set(data["issue"])
    assert CARD_ACTIVITY_KEYS <= set(data["issue_activity"])
    assert data["issue_activity"]["field"]
    assert data["issue_activity"]["verb"] in ("created", "updated", "deleted")
    assert isinstance(data["issue_activity"]["new_value"], str)
    assert isinstance(data["issue_activity"]["old_value"], str)


@pytest.mark.unit
@pytest.mark.django_db
class TestNotificationPayloadRendersInTheInbox:
    """Item 1: a Notification without ``issue_activity`` crashes the whole /notifications page."""

    def test_builder_mirrors_the_native_notification_task(self, project, make_issue):
        issue = make_issue(project)

        data = build_notification_data(
            issue, project, field="assignees", verb="updated", new_value="Ana", new_identifier="abc"
        )

        assert_card_can_render(data)
        assert data["issue"]["state_group"] == StateGroup.UNSTARTED.value
        assert data["issue"]["identifier"] == project.identifier
        assert data["issue_activity"]["actor"] is None
        assert data["issue_activity"]["new_identifier"] == "abc"

    def test_system_allocation_notification_has_issue_activity(
        self, workspace_with_members, project, staffed_unit, make_issue, plain_user
    ):
        issue = make_issue(project)

        _emit_assignee_activity_safely(
            issue_id=issue.id,
            project_id=project.id,
            workspace_id=workspace_with_members.id,
            user_id=plain_user.id,
            previous_assignee_ids=[],
            actor_id=None,
        )

        notification = Notification.objects.get(receiver=plain_user, entity_identifier=issue.id)
        assert_card_can_render(notification.data)
        assert notification.data["issue_activity"]["field"] == "assignees"
        assert notification.data["issue_activity"]["new_value"] == plain_user.display_name
        activity = IssueActivity.objects.get(issue=issue, field="assignees")
        assert notification.data["issue_activity"]["id"] == str(activity.id)
        assert notification.triggered_by_id is None

    @pytest.mark.parametrize("kind", [ALERT_ALLOCATION_FAILED, ALERT_ASSIGNMENT_SLA, ALERT_EXECUTOR_UNAVAILABLE])
    def test_alerts_have_issue_activity_and_keep_the_orca_keys(
        self, workspace_with_members, project, staffed_unit, make_issue, add_coordinator, second_user, kind
    ):
        add_coordinator(staffed_unit, second_user)
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)
        link = IssueOrganizationalUnit.objects.select_related("issue", "project", "organizational_unit").get(
            issue=issue
        )

        assert notify(link, kind) == 1

        data = Notification.objects.get(receiver=second_user, sender=f"in_app:orca:{kind}").data
        assert_card_can_render(data)
        assert data["organizational_unit"]["slug"] == staffed_unit.slug
        assert data["routing_state"] == link.routing_state
        assert data["queue_reason"] == link.queue_reason
        # The card ignores Notification.title, so the message rides in new_value.
        assert identifier_of(project, issue) in data["issue_activity"]["new_value"]


def identifier_of(project, issue):
    return f"{project.identifier}-{issue.sequence_id}"


@pytest.mark.unit
@pytest.mark.django_db
class TestAdoptingTheNativeAssignee:
    """Item 2: the link follows native additions, not only removals."""

    def test_replacing_the_executor_with_an_eligible_member_adopts_them(
        self,
        admin_client,
        admin_user,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
        second_user,
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)
        before = decisions_for(issue)

        response = admin_client.patch(
            issue_url(workspace_with_members, project, issue), {"assignee_ids": [str(second_user.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == second_user.id
        assert link.queue_reason == ""
        # One decision, like any reassignment, naming the HTTP actor.
        assert decisions_for(issue) == before + 1
        decision = link.current_assignment_decision
        assert decision.chosen_assignee_id == second_user.id
        assert decision.previous_primary_executor_id == plain_user.id
        assert decision.decided_by_id == admin_user.id
        # Native history was written once, by the native edit; the adoption added none.
        added = IssueActivity.objects.filter(issue=issue, field="assignees", comment="added assignee ")
        assert added.filter(new_value=second_user.display_name).count() == 1

    def test_two_eligible_assignees_is_ambiguous_and_goes_to_the_queue(
        self,
        admin_client,
        workspace_with_members,
        project,
        staffed_unit,
        add_member,
        make_issue,
        plain_user,
        second_user,
        admin_user,
    ):
        add_member(staffed_unit, admin_user)
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)

        admin_client.patch(
            issue_url(workspace_with_members, project, issue),
            {"assignee_ids": [str(second_user.id), str(admin_user.id)]},
            format="json",
        )

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None
        assert link.queue_reason == QueueReason.EXECUTOR_UNAVAILABLE

    def test_a_waiting_item_adopts_the_member_assigned_from_the_item(
        self, admin_client, admin_user, workspace_with_members, project, staffed_unit, make_issue, second_user
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.QUEUED
        before = decisions_for(issue)

        admin_client.patch(
            issue_url(workspace_with_members, project, issue), {"assignee_ids": [str(second_user.id)]}, format="json"
        )

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == second_user.id
        assert decisions_for(issue) == before + 1
        assert link.current_assignment_decision.decided_by_id == admin_user.id

    def test_a_waiting_item_ignores_an_assignee_who_cannot_execute_for_the_area(
        self, admin_client, workspace_with_members, project, staffed_unit, make_issue, admin_user
    ):
        # admin is on the project but not in the area.
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)

        admin_client.patch(
            issue_url(workspace_with_members, project, issue), {"assignee_ids": [str(admin_user.id)]}, format="json"
        )

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None

    def test_a_policy_without_manual_assignment_blocks_adoption(
        self, admin_client, workspace_with_members, project, staffed_unit, make_issue, second_user
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=staffed_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.SELF_CLAIM,
            allowed_modes=[AssignmentMode.SELF_CLAIM.value, AssignmentMode.LEAST_LOADED.value],
        )
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)
        before = decisions_for(issue)

        admin_client.patch(
            issue_url(workspace_with_members, project, issue), {"assignee_ids": [str(second_user.id)]}, format="json"
        )

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert decisions_for(issue) == before

    def test_self_claim_policy_adopts_the_person_who_assigned_themselves(
        self, second_client, workspace_with_members, project, staffed_unit, make_issue, second_user
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=staffed_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.SELF_CLAIM,
            allowed_modes=[AssignmentMode.SELF_CLAIM.value],
        )
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)

        response = second_client.patch(
            issue_url(workspace_with_members, project, issue), {"assignee_ids": [str(second_user.id)]}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == second_user.id

    def test_single_row_removal_adopts_the_remaining_eligible_assignee(
        self, project, staffed_unit, make_issue, plain_user, second_user
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user, collaborators=[second_user])

        IssueAssignee.objects.get(issue=issue, assignee=plain_user).delete()

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == second_user.id

    def test_orcas_own_writes_do_not_adopt_or_double_write(
        self, project, staffed_unit, make_issue, plain_user, second_user, admin_user
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user, collaborators=[second_user])
        before = decisions_for(issue)

        # Returning the item removes the executor's row; the collaborator is an
        # eligible area member, but nothing may adopt them behind the return.
        return_to_queue(issue, actor=admin_user, reason="wrong area")

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None
        assert decisions_for(issue) == before + 1


@pytest.mark.unit
@pytest.mark.django_db
class TestPublicApiPatchIsReconciled:
    """Item 3: the public ``/api/v1`` issue PATCH replaces assignees the same way."""

    def test_patching_assignees_through_the_public_api_follows_the_link(
        self,
        token_client,
        admin_user,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
        second_user,
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)
        client = token_client(admin_user, workspace_with_members)

        response = client.patch(
            f"/api/v1/workspaces/{workspace_with_members.slug}/projects/{project.id}/issues/{issue.id}/",
            {"assignees": [str(second_user.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.primary_executor_id == second_user.id


@pytest.mark.unit
@pytest.mark.django_db
class TestQueueReturnedExecutorLeavesTheItem:
    """Item 4: a queued item must not keep showing the previous executor."""

    def test_only_the_executors_row_goes_and_history_shows_it(
        self, project, staffed_unit, make_issue, plain_user, second_user, admin_user, django_capture_on_commit_callbacks
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user, collaborators=[second_user])

        with django_capture_on_commit_callbacks(execute=True):
            return_to_queue(issue, actor=admin_user, reason="wrong area")

        assert not IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()
        assert IssueAssignee.objects.filter(issue=issue, assignee=second_user).exists()
        removed = IssueActivity.objects.filter(issue=issue, field="assignees", comment="removed assignee ")
        assert removed.filter(old_value=plain_user.display_name).count() == 1

    def test_system_return_writes_history_and_notifies_the_person(
        self, project, staffed_unit, make_issue, plain_user, django_capture_on_commit_callbacks
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)

        with django_capture_on_commit_callbacks(execute=True):
            return_to_queue(issue, actor=None, queue_reason=QueueReason.EXECUTOR_UNAVAILABLE, trigger="availability")

        removed = IssueActivity.objects.get(issue=issue, field="assignees", comment="removed assignee ")
        assert removed.actor_id is None
        data = Notification.objects.get(receiver=plain_user, entity_identifier=issue.id).data
        assert_card_can_render(data)
        assert data["issue_activity"]["old_value"] == plain_user.display_name
        assert data["issue_activity"]["new_value"] == ""


@pytest.mark.unit
@pytest.mark.django_db
class TestDepartedMemberKeepsNoItems:
    """Item 5: workspace removal returns the executor's items to the queue."""

    def test_removed_member_items_go_back_and_coordinators_hear_once(
        self,
        admin_client,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        add_coordinator,
        plain_user,
        second_user,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(staffed_unit, second_user)
        first, second = make_issue(project), make_issue(project)
        done = make_issue(project, state_group=StateGroup.COMPLETED.value)
        for issue in (first, second, done):
            set_responsibility(issue, staffed_unit, explicit_executor=plain_user)
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)

        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.delete(f"/api/workspaces/{workspace_with_members.slug}/members/{wm.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data

        for issue in (first, second):
            link = IssueOrganizationalUnit.objects.get(issue=issue)
            assert link.routing_state == RoutingState.QUEUED
            assert link.primary_executor_id is None
            assert link.queue_reason == QueueReason.EXECUTOR_UNAVAILABLE
            assert not IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()
        # Closed work is out of routing and is left alone.
        assert IssueOrganizationalUnit.objects.get(issue=done).routing_state == RoutingState.ASSIGNED
        alerts = Notification.objects.filter(receiver=second_user, sender=f"in_app:orca:{ALERT_EXECUTOR_UNAVAILABLE}")
        assert alerts.count() == 2
        for alert in alerts:
            assert_card_can_render(alert.data)

    def test_member_save_does_not_read_the_row_when_the_layer_is_off(
        self, settings, workspace_with_members, plain_user
    ):
        settings.ORCA_ORG_UNITS_ENABLED = False
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)

        with CaptureQueriesContext(connection) as queries:
            wm.save()

        assert not [q for q in queries if q["sql"].startswith("SELECT") and '"workspace_members"' in q["sql"]]

    def test_member_save_skips_the_read_when_update_fields_cannot_deactivate(
        self, settings, workspace_with_members, plain_user
    ):
        settings.ORCA_ORG_UNITS_ENABLED = True
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)

        with CaptureQueriesContext(connection) as queries:
            wm.save(update_fields=["role"])

        assert not [q for q in queries if q["sql"].startswith("SELECT") and '"workspace_members"' in q["sql"]]


@pytest.mark.unit
@pytest.mark.django_db
class TestExecutiveMetricsAgreeWithTheQueue:
    """Item 6: closed items are not queued work, and a reopened one restarts its clock."""

    def _close(self, issue, group):
        state, _ = State.objects.get_or_create(project=issue.project, group=group, defaults={"name": f"{group}-state"})
        issue.state = state
        issue.save()

    def test_a_queued_item_that_was_completed_is_not_counted(
        self, workspace_with_members, project, staffed_unit, make_issue
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)
        now = timezone.now()
        IssueOrganizationalUnit.objects.filter(issue=issue).update(assignment_due_at=now - timedelta(hours=1))
        kwargs = {"period_start": now - timedelta(days=30), "now": now, "visible_project_ids": {project.id}}
        assert unit_metrics(staffed_unit, **kwargs)["queued"] == 1

        self._close(issue, StateGroup.COMPLETED.value)

        metrics = unit_metrics(staffed_unit, **kwargs)
        assert metrics["queued"] == 0
        assert metrics["assignment_overdue"] == 0
        for metric in ("queued", "assignment_overdue"):
            rows, hidden = drill_down_queryset(
                staffed_unit, metric, period="30d", now=now, visible_project_ids={project.id}
            )
            assert rows.count() == 0
            assert hidden == 0

    def test_reopening_a_queued_item_restarts_the_waiting_clock(
        self, workspace_with_members, project, staffed_unit, make_issue
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=staffed_unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            assignment_sla_seconds=3600,
        )
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)
        old = timezone.now() - timedelta(days=3)
        IssueOrganizationalUnit.objects.filter(issue=issue).update(
            queued_at=old, assignment_due_at=old + timedelta(hours=1), last_alerted_at=old
        )
        self._close(issue, StateGroup.CANCELLED.value)
        # Closing alone must not touch the clock.
        assert IssueOrganizationalUnit.objects.get(issue=issue).queued_at == old

        issue = Issue.objects.get(pk=issue.pk)
        self._close(issue, StateGroup.STARTED.value)

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.queued_at > timezone.now() - timedelta(minutes=5)
        assert link.assignment_due_at > timezone.now()
        assert link.last_alerted_at is None


@pytest.mark.unit
@pytest.mark.django_db
class TestDriftSweepAlertsTheCoordinators:
    """Item 7: an item the drift sweep returns raises the same alert the availability sweep does."""

    def test_drift_sweep_alerts_once_per_returned_item(
        self,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        add_coordinator,
        plain_user,
        second_user,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(staffed_unit, second_user)
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)
        # The executor leaves the area behind the service's back.
        staffed_unit.memberships.filter(workspace_member__member=plain_user).update(is_active=False)

        with django_capture_on_commit_callbacks(execute=True):
            summary = sweep_routing_drift()

        assert summary["repaired"] == 1
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.QUEUED
        alerts = Notification.objects.filter(receiver=second_user, sender=f"in_app:orca:{ALERT_EXECUTOR_UNAVAILABLE}")
        assert alerts.count() == 1

        with django_capture_on_commit_callbacks(execute=True):
            sweep_routing_drift()
        assert alerts.count() == 1
