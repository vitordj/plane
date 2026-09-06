# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The alerts that tell an area its queue needs a person (item 2.4).

Two moments, tested separately because they fail differently. The immediate
one — an allocation that found nobody — has an event behind it, so the risk is
that the alert fires on a transaction that rolled back or on an item somebody
has meanwhile claimed. The swept one has no event at all, so the risks are the
opposite: never noticing, or noticing the same breach every fifteen minutes
until the coordinator stops reading notifications.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.app.services.orca import allocate, alert_recipients, claim
from plane.app.services.orca.alerts import (
    SENDER_ALLOCATION_FAILED,
    SENDER_ASSIGNMENT_OVERDUE,
    may_alert_again,
)
from plane.bgtasks.organizational_queue_task import sweep_assignment_sla
from plane.db.models import (
    AssignmentMode,
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitMemberRole,
    RoutingState,
    StateGroup,
)

from .conftest import ROLE_MEMBER


@pytest.fixture
def covered(unit, project, link_project):
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def overdue_item(unit, project, make_issue, covered):
    """A queued item whose assignment deadline has already passed."""

    def _make(*, due_minutes_ago=30, last_alerted_at=None, state_group=StateGroup.UNSTARTED.value):
        issue = make_issue(project, state_group=state_group)
        return IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.QUEUED,
            queue_reason="awaiting_coordinator",
            queued_at=timezone.now() - timedelta(hours=2),
            assignment_due_at=timezone.now() - timedelta(minutes=due_minutes_ago),
            last_alerted_at=last_alerted_at,
        )

    return _make


@pytest.mark.unit
@pytest.mark.django_db
class TestWhoHearsAboutIt:
    def test_the_coordinators_do(self, unit, add_coordinator, second_user, guest_user):
        add_coordinator(unit, second_user)
        add_coordinator(unit, guest_user)

        assert set(alert_recipients(unit)) == {second_user.id, guest_user.id}

    def test_the_lead_does_when_there_is_no_coordinator(self, unit, add_member, plain_user, second_user):
        add_member(unit, plain_user, role=OrganizationalUnitMemberRole.LEAD)
        add_member(unit, second_user)

        assert alert_recipients(unit) == [plain_user.id]

    def test_a_coordinator_replaces_the_lead_rather_than_joining_them(
        self, unit, add_member, add_coordinator, plain_user, second_user
    ):
        # An area with a coordinator has somebody whose job this is; telling the
        # lead as well makes the alert everybody's and nobody's.
        add_member(unit, plain_user, role=OrganizationalUnitMemberRole.LEAD)
        add_coordinator(unit, second_user)

        assert alert_recipients(unit) == [second_user.id]

    def test_an_inactive_coordination_is_not_told(self, unit, add_coordinator, second_user):
        coordinator = add_coordinator(unit, second_user)
        coordinator.is_active = False
        coordinator.save()

        assert alert_recipients(unit) == []


@pytest.mark.unit
@pytest.mark.django_db
class TestTheImmediateAlert:
    def test_an_allocation_that_finds_nobody_alerts_the_coordinator(
        self,
        unit,
        project,
        make_issue,
        covered,
        add_coordinator,
        second_user,
        admin_user,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(unit, second_user)
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )

        # The alert is queued for after commit, so an allocation that rolls
        # back tells nobody. A test wrapped in a transaction never commits,
        # which is exactly what this fixture is for.
        with django_capture_on_commit_callbacks(execute=True):
            allocate(issue, unit, requested_mode=AssignmentMode.LEAST_LOADED, actor=admin_user)

        alert = Notification.objects.filter(sender=SENDER_ALLOCATION_FAILED).first()
        assert alert is not None
        assert alert.receiver_id == second_user.id
        assert alert.entity_identifier == issue.id
        assert alert.data["orca"]["unit_slug"] == unit.slug
        assert alert.triggered_by_id == admin_user.id

    def test_an_allocation_that_succeeds_alerts_nobody(
        self,
        unit,
        project,
        make_issue,
        covered,
        add_coordinator,
        add_member,
        grant_manual_access,
        plain_user,
        second_user,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(unit, second_user)
        add_member(unit, plain_user)
        grant_manual_access(project, plain_user)
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )

        with django_capture_on_commit_callbacks(execute=True):
            allocate(issue, unit, requested_mode=AssignmentMode.LEAST_LOADED)

        assert Notification.objects.filter(sender=SENDER_ALLOCATION_FAILED).count() == 0

    def test_an_area_with_nobody_to_tell_writes_no_alert(
        self, unit, project, make_issue, covered, django_capture_on_commit_callbacks
    ):
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )

        with django_capture_on_commit_callbacks(execute=True):
            allocate(issue, unit, requested_mode=AssignmentMode.LEAST_LOADED)

        assert Notification.objects.filter(sender=SENDER_ALLOCATION_FAILED).count() == 0


@pytest.mark.unit
@pytest.mark.django_db
class TestTheSweep:
    def test_an_overdue_item_alerts_once(self, unit, overdue_item, add_coordinator, second_user):
        add_coordinator(unit, second_user)
        link = overdue_item()

        assert sweep_assignment_sla() == 1

        alerts = Notification.objects.filter(sender=SENDER_ASSIGNMENT_OVERDUE)
        assert alerts.count() == 1
        assert alerts.first().receiver_id == second_user.id
        link.refresh_from_db()
        assert link.last_alerted_at is not None

    def test_a_second_pass_within_four_hours_does_not_repeat_it(self, unit, overdue_item, add_coordinator, second_user):
        add_coordinator(unit, second_user)
        overdue_item()

        sweep_assignment_sla()
        sweep_assignment_sla()

        assert Notification.objects.filter(sender=SENDER_ASSIGNMENT_OVERDUE).count() == 1

    def test_a_breach_still_open_after_the_quiet_period_is_mentioned_again(
        self, unit, overdue_item, add_coordinator, second_user
    ):
        add_coordinator(unit, second_user)
        overdue_item(last_alerted_at=timezone.now() - timedelta(hours=5))

        assert sweep_assignment_sla() == 1
        assert Notification.objects.filter(sender=SENDER_ASSIGNMENT_OVERDUE).count() == 1

    def test_an_item_that_is_not_overdue_is_left_alone(
        self, unit, project, make_issue, covered, add_coordinator, second_user
    ):
        add_coordinator(unit, second_user)
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.QUEUED,
            queued_at=timezone.now(),
            assignment_due_at=timezone.now() + timedelta(hours=1),
        )

        assert sweep_assignment_sla() == 0

    def test_an_assigned_item_is_not_the_queues_problem(
        self,
        unit,
        project,
        make_issue,
        covered,
        add_coordinator,
        add_member,
        grant_manual_access,
        plain_user,
        second_user,
    ):
        add_coordinator(unit, second_user)
        add_member(unit, plain_user)
        grant_manual_access(project, plain_user)
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.QUEUED,
            queued_at=timezone.now() - timedelta(hours=2),
            assignment_due_at=timezone.now() - timedelta(hours=1),
        )
        claim(issue, plain_user)

        assert sweep_assignment_sla() == 0

    def test_a_finished_item_is_not_waiting_for_anybody(self, unit, overdue_item, add_coordinator, second_user):
        # Routing state and native state can diverge — somebody closes an item
        # straight from the board — and a closed item is not waiting for a
        # person however the queue reads.
        add_coordinator(unit, second_user)
        overdue_item(state_group=StateGroup.COMPLETED.value)

        assert sweep_assignment_sla() == 0

    def test_an_item_nobody_can_be_told_about_stays_alertable(self, unit, overdue_item):
        link = overdue_item()

        assert sweep_assignment_sla() == 0
        link.refresh_from_db()
        # Untouched, so the item is picked up as soon as the area has somebody
        # to notify rather than being quietly written off.
        assert link.last_alerted_at is None

    def test_the_kill_switch_stops_the_sweep(self, unit, overdue_item, add_coordinator, second_user, settings):
        add_coordinator(unit, second_user)
        overdue_item()
        settings.ORCA_ORG_UNITS_ENABLED = False

        assert sweep_assignment_sla() == 0
        assert Notification.objects.filter(sender=SENDER_ASSIGNMENT_OVERDUE).count() == 0

    def test_an_inactive_area_is_not_swept(self, unit, overdue_item, add_coordinator, second_user):
        add_coordinator(unit, second_user)
        overdue_item()
        unit.is_active = False
        unit.save()

        assert sweep_assignment_sla() == 0


@pytest.mark.unit
@pytest.mark.django_db
class TestTheQuietPeriod:
    def test_an_item_never_alerted_may_be(self, unit, overdue_item):
        assert may_alert_again(overdue_item()) is True

    def test_an_item_alerted_just_now_may_not(self, unit, overdue_item):
        assert may_alert_again(overdue_item(last_alerted_at=timezone.now())) is False

    def test_an_item_alerted_five_hours_ago_may(self, unit, overdue_item):
        assert may_alert_again(overdue_item(last_alerted_at=timezone.now() - timedelta(hours=5))) is True
