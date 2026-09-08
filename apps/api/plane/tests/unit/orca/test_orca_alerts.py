# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Alerts for a stuck queue: who they reach, the SLA sweep, and the immediate
allocation_failed hook.

The sweep must not page anyone when the layer is off, must not duplicate
inside four hours, and must fall back to the lead when the area has no
coordinator. The immediate hook must never fail the allocation itself.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from plane.app.services.orca.alerts import recipients_for
from plane.app.services.orca.assignment_service import allocate
from plane.bgtasks.organizational_queue_task import sweep_assignment_sla
from plane.db.models import (
    AssignmentMode,
    DecisionOutcome,
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitAssignmentPolicy,
    QueueReason,
    RoutingState,
)

from .conftest import ROLE_MEMBER


@pytest.fixture
def covered(unit, project, link_project):
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def make_link(unit, project, make_issue):
    def _make(issue=None, **fields):
        issue = issue or make_issue(project)
        link = IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )
        if fields:
            IssueOrganizationalUnit.objects.filter(pk=link.pk).update(**fields)
            link.refresh_from_db()
        return link

    return _make


def policy_for(unit, workspace, **kwargs):
    return OrganizationalUnitAssignmentPolicy.objects.create(organizational_unit=unit, workspace=workspace, **kwargs)


@pytest.mark.unit
class TestWhoTheAlertReaches:
    def test_coordinators_are_the_recipients(self, unit, add_coordinator, admin_user, plain_user):
        add_coordinator(unit, admin_user)
        add_coordinator(unit, plain_user)

        assert set(recipients_for(unit)) == {admin_user.id, plain_user.id}

    def test_without_a_coordinator_the_lead_hears(self, unit, add_member, plain_user):
        add_member(unit, plain_user, role="lead")

        assert recipients_for(unit) == [plain_user.id]

    def test_a_coordinator_beats_the_lead(self, unit, add_coordinator, add_member, admin_user, plain_user):
        add_coordinator(unit, admin_user)
        add_member(unit, plain_user, role="lead")

        assert recipients_for(unit) == [admin_user.id]

    def test_nobody_means_an_empty_list(self, unit):
        assert recipients_for(unit) == []


@pytest.mark.unit
class TestTheAssignmentSlaSweep:
    def test_an_overdue_item_notifies_the_coordinator_once(
        self, unit, project, covered, make_link, add_coordinator, admin_user
    ):
        add_coordinator(unit, admin_user)
        make_link(assignment_due_at=timezone.now() - timedelta(minutes=5))

        sweep_assignment_sla()
        sweep_assignment_sla()

        assert Notification.objects.filter(receiver=admin_user, sender="in_app:orca:assignment_sla").count() == 1
        link = IssueOrganizationalUnit.objects.get()
        assert link.last_alerted_at is not None

    def test_repetition_inside_four_hours_does_not_duplicate(
        self, unit, project, covered, make_link, add_coordinator, admin_user
    ):
        add_coordinator(unit, admin_user)
        make_link(
            assignment_due_at=timezone.now() - timedelta(hours=1),
            last_alerted_at=timezone.now() - timedelta(hours=1),
        )

        sweep_assignment_sla()

        assert Notification.objects.count() == 0

    def test_after_four_hours_the_area_hears_again(
        self, unit, project, covered, make_link, add_coordinator, admin_user
    ):
        add_coordinator(unit, admin_user)
        make_link(
            assignment_due_at=timezone.now() - timedelta(hours=6),
            last_alerted_at=timezone.now() - timedelta(hours=5),
        )

        sweep_assignment_sla()

        assert Notification.objects.filter(receiver=admin_user, sender="in_app:orca:assignment_sla").count() == 1

    def test_without_a_coordinator_the_lead_is_notified(
        self, unit, project, covered, make_link, add_member, plain_user
    ):
        add_member(unit, plain_user, role="lead")
        make_link(assignment_due_at=timezone.now() - timedelta(minutes=1))

        sweep_assignment_sla()

        assert Notification.objects.filter(receiver=plain_user).count() == 1

    def test_the_kill_switch_makes_the_sweep_a_no_op(
        self, settings, unit, project, covered, make_link, add_coordinator, admin_user
    ):
        settings.ORCA_ORG_UNITS_ENABLED = False
        add_coordinator(unit, admin_user)
        make_link(assignment_due_at=timezone.now() - timedelta(minutes=1))

        sweep_assignment_sla()

        assert Notification.objects.count() == 0
        assert IssueOrganizationalUnit.objects.get().last_alerted_at is None

    def test_an_item_still_inside_its_sla_is_ignored(
        self, unit, project, covered, make_link, add_coordinator, admin_user
    ):
        add_coordinator(unit, admin_user)
        make_link(assignment_due_at=timezone.now() + timedelta(hours=1))

        sweep_assignment_sla()

        assert Notification.objects.count() == 0


@pytest.mark.unit
class TestAllocationFailedAlertsImmediately:
    def test_the_coordinator_hears_when_least_loaded_finds_nobody(
        self,
        unit,
        project,
        covered,
        make_link,
        make_issue,
        workspace_with_members,
        add_coordinator,
        admin_user,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(unit, admin_user)
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        issue = make_issue(project)
        make_link(issue)

        with django_capture_on_commit_callbacks(execute=True):
            result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.ALLOCATION_FAILED
        assert result.link.routing_state == RoutingState.ALLOCATION_FAILED
        assert result.link.queue_reason == QueueReason.NO_ELIGIBLE_MEMBER
        assert Notification.objects.filter(receiver=admin_user, sender="in_app:orca:allocation_failed").count() == 1
        # The immediate alert is not an SLA alert: last_alerted_at stays empty
        # so the sweep can still notice the deadline later.
        assert IssueOrganizationalUnit.objects.get(pk=result.link.id).last_alerted_at is None

    def test_a_notification_failure_does_not_fail_the_allocation(
        self,
        unit,
        project,
        covered,
        make_link,
        make_issue,
        workspace_with_members,
        add_coordinator,
        admin_user,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(unit, admin_user)
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        issue = make_issue(project)
        make_link(issue)

        with mock.patch("plane.app.services.orca.alerts.notify", side_effect=RuntimeError("broker down")):
            with django_capture_on_commit_callbacks(execute=True):
                result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.ALLOCATION_FAILED
        assert result.link.routing_state == RoutingState.ALLOCATION_FAILED
        assert Notification.objects.count() == 0
