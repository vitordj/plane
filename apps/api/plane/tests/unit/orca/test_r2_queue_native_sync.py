# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Regression tests for R2 Frente F3 (queue, native Plane sync, lifecycle, notifications).

Pins R2.05, R2.06 (D3), R2.07, R2.08, R2.11, R2.17, R2.19, and R2.20 (signals).
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework import status

from plane.app.services.orca import (
    InvalidTransition,
    allocate,
    audit_routing,
    queue_queryset,
    reassign,
    return_to_queue,
    set_responsibility,
)
from plane.app.services.orca.alerts import recipients_for
from plane.bgtasks.organizational_availability_task import sweep_unavailable_executors, unusable_executor_links
from plane.bgtasks.organizational_queue_task import sweep_assignment_sla, sweep_routing_drift
from plane.db.models import (
    AssignmentMode,
    IssueActivity,
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueSubscriber,
    Notification,
    OrganizationalUnitAssignmentPolicy,
    QueueReason,
    RoutingState,
    State,
    StateGroup,
    WorkspaceMember,
    WorkspaceMemberAvailability,
)

from .conftest import ROLE_ADMIN, ROLE_GUEST, ROLE_MEMBER, issue_assign_url


@pytest.fixture
def staffed_unit(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user, admin_user):
    link_project(unit, project, ROLE_MEMBER)
    grant_manual_access(project, admin_user, ROLE_ADMIN)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user, ROLE_MEMBER)
    return unit


@pytest.mark.unit
@pytest.mark.django_db
class TestR205NativeAssigneeSyncAndDriftSweep:
    """R2.05: native assignee replacement reaches area routing immediately and via hourly beat sweep."""

    def test_native_patch_replacing_executor_returns_item_to_queue(
        self,
        admin_client,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
        admin_user,
    ):
        # The replacement (admin) is a project member but not a member of the
        # area, so nobody eligible is left on the item: it goes back to the queue.
        # (When the replacement is an eligible area member the item is adopted
        # instead; see test_r2_review_queue.py.)
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)

        response = admin_client.patch(
            f"/api/workspaces/{workspace_with_members.slug}/projects/{project.id}/issues/{issue.id}/",
            {"assignee_ids": [str(admin_user.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None
        assert link.queue_reason == QueueReason.EXECUTOR_UNAVAILABLE

    def test_hourly_sweep_routing_drift_repairs_even_with_availability_off(
        self,
        settings,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
    ):
        settings.ORCA_AVAILABILITY_ENABLED = False
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)
        # Simulate an out-of-band queryset delete bypassing signals.
        IssueAssignee.objects.filter(issue=issue, assignee=plain_user).delete()

        summary = sweep_routing_drift()

        assert summary["repaired"] == 1
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None


@pytest.mark.unit
@pytest.mark.django_db
class TestR206ClosedAndArchivedItemsOutOfRouting:
    """R2.06 (D3): completed, cancelled, archived, and draft items stay out of queue, sweeps, and audit."""

    def test_completed_item_leaves_queue_sla_sweep_availability_sweep_and_audit(
        self,
        settings,
        workspace_with_members,
        project,
        staffed_unit,
        add_coordinator,
        admin_user,
        plain_user,
        make_issue,
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        add_coordinator(staffed_unit, admin_user)
        done_state = State.objects.create(
            project=project, workspace=workspace_with_members, name="Done", group=StateGroup.COMPLETED.value
        )
        cancelled_state = State.objects.create(
            project=project, workspace=workspace_with_members, name="Cancelled", group=StateGroup.CANCELLED.value
        )

        # Queued completed item: must not show in queue or fire SLA alerts.
        queued_done = make_issue(project, name="Already done")
        set_responsibility(queued_done, staffed_unit)
        IssueOrganizationalUnit.objects.filter(issue=queued_done).update(
            assignment_due_at=timezone.now() - timedelta(hours=2)
        )
        queued_done.state = done_state
        queued_done.save()

        # Assigned completed item with away executor and dropped assignee: must not be returned or audited.
        assigned_done = make_issue(project, name="Done by away executor")
        set_responsibility(assigned_done, staffed_unit, explicit_executor=plain_user)
        assigned_done.state = cancelled_state
        assigned_done.save()
        IssueAssignee.objects.filter(issue=assigned_done, assignee=plain_user).delete()

        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        WorkspaceMemberAvailability.objects.create(
            workspace=workspace_with_members,
            workspace_member=wm,
            unavailable_from=timezone.now() - timedelta(hours=1),
            unavailable_until=timezone.now() + timedelta(days=5),
        )

        assert list(queue_queryset(staffed_unit, routing_state="all")) == []
        sweep_assignment_sla()
        assert Notification.objects.count() == 0
        assert unusable_executor_links() == []
        assert sweep_unavailable_executors() == {"returned": 0}
        assert audit_routing(workspace_with_members.id) == []


@pytest.mark.unit
@pytest.mark.django_db
class TestR207HumanOverrideDuringAbsenceSurvivesSweep:
    """R2.07: assigning someone who is already in an absence window must not be undone by the sweep."""

    def test_coordinator_reassign_after_absence_start_is_not_requeued(
        self,
        settings,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
        admin_user,
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        # Absence started an hour ago.
        WorkspaceMemberAvailability.objects.create(
            workspace=workspace_with_members,
            workspace_member=wm,
            unavailable_from=timezone.now() - timedelta(hours=1),
            unavailable_until=timezone.now() + timedelta(days=3),
        )

        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)
        # Coordinator intentionally assigns plain_user despite the active absence window.
        reassign(issue, plain_user, actor=admin_user, reason="urgent handover")

        assert unusable_executor_links() == []
        assert sweep_unavailable_executors() == {"returned": 0}
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == plain_user.id


@pytest.mark.unit
@pytest.mark.django_db
class TestR208AssigneeActivityAndNotification:
    """R2.08: Orca assignments emit native activity and notify the chosen executor."""

    def test_system_auto_assignment_records_activity_subscriber_and_notification(
        self,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        django_capture_on_commit_callbacks,
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            workspace=workspace_with_members,
            organizational_unit=staffed_unit,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        issue = make_issue(project, name="Auto-routed item")

        with django_capture_on_commit_callbacks(execute=True):
            result = set_responsibility(issue, staffed_unit, actor=None)

        assert result.chosen_user_id is not None
        assert IssueActivity.objects.filter(
            issue=issue, field="assignees", new_identifier=result.chosen_user_id
        ).exists()
        assert IssueSubscriber.objects.filter(issue=issue, subscriber_id=result.chosen_user_id).exists()
        assert Notification.objects.filter(
            receiver_id=result.chosen_user_id,
            entity_identifier=issue.id,
            sender="in_app:issue_activities:assigned",
        ).exists()

    def test_human_reassign_dispatches_native_issue_activity_with_notification(
        self,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
        admin_user,
        django_capture_on_commit_callbacks,
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)

        with mock.patch("plane.bgtasks.issue_activities_task.issue_activity.delay") as mock_delay:
            with django_capture_on_commit_callbacks(execute=True):
                reassign(issue, plain_user, actor=admin_user)

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs["type"] == "issue.activity.updated"
        assert kwargs["actor_id"] == str(admin_user.id)
        assert kwargs["issue_id"] == str(issue.id)
        assert kwargs["notification"] is True
        assert str(plain_user.id) in kwargs["requested_data"]


@pytest.mark.unit
@pytest.mark.django_db
class TestR211ReturnToQueueSlaAndReassign:
    """R2.11: returning an item resets its SLA clock and allows organizational-unit-assign again."""

    def test_return_to_queue_recomputes_sla_instead_of_keeping_expired_deadline(
        self,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
        admin_user,
    ):
        OrganizationalUnitAssignmentPolicy.objects.create(
            workspace=workspace_with_members,
            organizational_unit=staffed_unit,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value, AssignmentMode.LEAST_LOADED.value],
            assignment_sla_seconds=3600,
        )
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit)
        # Simulate an expired SLA before assignment.
        IssueOrganizationalUnit.objects.filter(issue=issue).update(
            assignment_due_at=timezone.now() - timedelta(hours=3),
            last_alerted_at=timezone.now() - timedelta(minutes=10),
        )
        reassign(issue, plain_user, actor=admin_user)

        assigned_link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert assigned_link.assignment_due_at is None
        assert assigned_link.last_alerted_at is None

        return_to_queue(issue, actor=admin_user)

        returned_link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert returned_link.routing_state == RoutingState.QUEUED
        assert returned_link.assignment_due_at is not None
        assert returned_link.assignment_due_at > timezone.now()
        assert returned_link.last_alerted_at is None

    def test_organizational_unit_assign_allocates_returned_item_despite_leftover_assignee(
        self,
        admin_client,
        workspace_with_members,
        project,
        staffed_unit,
        make_issue,
        plain_user,
        admin_user,
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)
        return_to_queue(issue, actor=admin_user)
        # The previous executor is taken off the issue by return_to_queue (R2.05).
        assert not IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id),
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["reason"] == "assigned"
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED


@pytest.mark.unit
@pytest.mark.django_db
class TestR217AllocateRechecksUnitAfterLock:
    """R2.17: allocate must refuse if the link moved to another unit before acquiring the lock."""

    def test_allocate_refuses_when_link_belongs_to_different_unit(
        self,
        workspace_with_members,
        project,
        staffed_unit,
        second_unit,
        link_project,
        make_issue,
    ):
        link_project(second_unit, project, ROLE_MEMBER)
        issue = make_issue(project)
        set_responsibility(issue, second_unit)

        with pytest.raises(InvalidTransition):
            allocate(issue, staffed_unit)


@pytest.mark.unit
@pytest.mark.django_db
class TestR219AlertsFilterInactiveAndSkipZeroNotificationStamp:
    """R2.19: inactive coordinators are excluded and zero-recipient runs do not stamp last_alerted_at."""

    def test_inactive_coordinator_is_skipped_and_falls_back_to_active_lead(
        self,
        workspace_with_members,
        unit,
        add_coordinator,
        add_member,
        plain_user,
        second_user,
    ):
        add_coordinator(unit, plain_user)
        add_member(unit, second_user, role="lead")

        # Deactivate coordinator's workspace membership -> fallback to active lead.
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        wm.is_active = False
        wm.save()

        assert recipients_for(unit) == [second_user.id]

    def test_guest_demoted_coordinator_is_excluded_from_recipients(
        self,
        workspace_with_members,
        unit,
        add_coordinator,
        plain_user,
    ):
        add_coordinator(unit, plain_user)
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        wm.role = ROLE_GUEST
        wm.save()

        assert recipients_for(unit) == []

    def test_sla_sweep_does_not_stamp_last_alerted_when_zero_recipients(
        self,
        workspace_with_members,
        project,
        unit,
        link_project,
        make_issue,
    ):
        link_project(unit, project, ROLE_MEMBER)
        issue = make_issue(project)
        set_responsibility(issue, unit)
        IssueOrganizationalUnit.objects.filter(issue=issue).update(
            assignment_due_at=timezone.now() - timedelta(hours=1)
        )

        sweep_assignment_sla()

        assert Notification.objects.count() == 0
        assert IssueOrganizationalUnit.objects.get(issue=issue).last_alerted_at is None


@pytest.mark.unit
@pytest.mark.django_db
class TestR220SignalRespectsMasterKillSwitch:
    """R2.20: IssueAssignee receiver does not requeue while ORCA_ORG_UNITS_ENABLED is off."""

    def test_deleting_assignee_does_nothing_when_org_units_disabled(
        self,
        settings,
        project,
        staffed_unit,
        make_issue,
        plain_user,
    ):
        issue = make_issue(project)
        set_responsibility(issue, staffed_unit, explicit_executor=plain_user)
        settings.ORCA_ORG_UNITS_ENABLED = False

        assignee = IssueAssignee.objects.get(issue=issue, assignee=plain_user)
        assignee.delete()

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == plain_user.id
