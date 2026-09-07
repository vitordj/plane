# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The pass that notices an assignment deadline came and went.

Three properties matter more than the happy path. It must alert *once* and
then stay quiet for the re-alert window, because a task on a fifteen-minute
beat that alerts every tick trains its recipients to ignore it. It must come
back after that window, because an item forgotten overnight has to be raised
again. And it must do nothing at all with the layer's kill switch off, since a
disabled layer that still writes notifications is the one door left open.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.bgtasks.organizational_queue_task import REALERT_AFTER, sweep_assignment_sla
from plane.db.models import (
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitMemberRole,
    QueueReason,
    RoutingState,
)


@pytest.fixture
def overdue_link(unit, workspace_with_members, project, make_issue, link_project):
    """One item this area owns, waiting past its assignment deadline."""
    link_project(unit, project)
    issue = make_issue(project)
    return IssueOrganizationalUnit.objects.create(
        issue=issue,
        organizational_unit=unit,
        project=project,
        workspace=workspace_with_members,
        routing_state=RoutingState.ALLOCATION_FAILED,
        queue_reason=QueueReason.NO_ELIGIBLE_MEMBER,
        queued_at=timezone.now() - timedelta(hours=6),
        assignment_due_at=timezone.now() - timedelta(hours=1),
    )


def _alerts_for(link):
    return Notification.objects.filter(entity_identifier=link.issue_id, sender="in_app:orca:assignment_overdue")


@pytest.mark.unit
@pytest.mark.django_db
class TestSweepAssignmentSla:
    def test_an_overdue_item_alerts_its_coordinator_once(self, overdue_link, plain_user, add_coordinator):
        add_coordinator(overdue_link.organizational_unit, plain_user)

        assert sweep_assignment_sla() == 1

        assert _alerts_for(overdue_link).count() == 1
        assert _alerts_for(overdue_link).first().receiver_id == plain_user.id
        overdue_link.refresh_from_db()
        assert overdue_link.last_alerted_at is not None

    def test_a_second_pass_inside_the_window_does_not_alert_again(self, overdue_link, plain_user, add_coordinator):
        add_coordinator(overdue_link.organizational_unit, plain_user)

        assert sweep_assignment_sla() == 1
        # The beat would run this fifteen minutes later, and again, and again.
        assert sweep_assignment_sla() == 0
        assert sweep_assignment_sla() == 0

        assert _alerts_for(overdue_link).count() == 1

    def test_the_window_expiring_alerts_again(self, overdue_link, plain_user, add_coordinator):
        add_coordinator(overdue_link.organizational_unit, plain_user)
        sweep_assignment_sla()

        # Rewind the stamp past the window rather than sleeping: the item was
        # alerted about, nobody picked it up, and four hours went by.
        IssueOrganizationalUnit.objects.filter(pk=overdue_link.pk).update(
            last_alerted_at=timezone.now() - REALERT_AFTER - timedelta(minutes=1)
        )

        assert sweep_assignment_sla() == 1
        assert _alerts_for(overdue_link).count() == 2

    def test_an_area_with_no_coordinator_falls_back_to_the_lead(self, overdue_link, admin_user, add_member):
        add_member(overdue_link.organizational_unit, admin_user, role=OrganizationalUnitMemberRole.LEAD)

        assert sweep_assignment_sla() == 1

        assert _alerts_for(overdue_link).get().receiver_id == admin_user.id

    def test_an_area_with_nobody_is_not_stamped(self, overdue_link):
        # Nothing was sent, so nothing may start the four-hour silence: the
        # tick after a coordinator is finally appointed has to reach them.
        assert sweep_assignment_sla() == 0

        assert not _alerts_for(overdue_link).exists()
        overdue_link.refresh_from_db()
        assert overdue_link.last_alerted_at is None

    def test_the_kill_switch_makes_the_pass_a_no_op(self, settings, overdue_link, plain_user, add_coordinator):
        add_coordinator(overdue_link.organizational_unit, plain_user)
        settings.ORCA_ORG_UNITS_ENABLED = False

        assert sweep_assignment_sla() == 0

        assert not _alerts_for(overdue_link).exists()
        overdue_link.refresh_from_db()
        assert overdue_link.last_alerted_at is None

    def test_an_item_still_inside_its_deadline_is_left_alone(self, overdue_link, plain_user, add_coordinator):
        add_coordinator(overdue_link.organizational_unit, plain_user)
        IssueOrganizationalUnit.objects.filter(pk=overdue_link.pk).update(
            assignment_due_at=timezone.now() + timedelta(hours=2)
        )

        assert sweep_assignment_sla() == 0
        assert not _alerts_for(overdue_link).exists()

    def test_an_item_with_no_deadline_is_left_alone(self, overdue_link, plain_user, add_coordinator):
        # A null SLA means the area never promised a pickup time; there is
        # nothing to be late against.
        add_coordinator(overdue_link.organizational_unit, plain_user)
        IssueOrganizationalUnit.objects.filter(pk=overdue_link.pk).update(assignment_due_at=None)

        assert sweep_assignment_sla() == 0
        assert not _alerts_for(overdue_link).exists()

    def test_an_assigned_item_is_not_overdue(self, overdue_link, plain_user, add_coordinator):
        # Somebody is on it. The deadline was for picking it up, and it was.
        add_coordinator(overdue_link.organizational_unit, plain_user)
        IssueOrganizationalUnit.objects.filter(pk=overdue_link.pk).update(
            routing_state=RoutingState.ASSIGNED, primary_executor=plain_user
        )

        assert sweep_assignment_sla() == 0
        assert not _alerts_for(overdue_link).exists()

    def test_a_queued_item_counts_as_well_as_a_failed_one(self, overdue_link, plain_user, add_coordinator):
        add_coordinator(overdue_link.organizational_unit, plain_user)
        IssueOrganizationalUnit.objects.filter(pk=overdue_link.pk).update(
            routing_state=RoutingState.QUEUED, queue_reason=QueueReason.AWAITING_COORDINATOR
        )

        assert sweep_assignment_sla() == 1
        assert _alerts_for(overdue_link).count() == 1

    def test_one_area_with_no_recipient_does_not_stop_the_next(
        self,
        unit,
        second_unit,
        workspace_with_members,
        project,
        second_project,
        make_issue,
        link_project,
        overdue_link,
        plain_user,
        add_coordinator,
    ):
        # The first area has nobody; the second has a coordinator. A pass that
        # stopped on the first would leave the second's work unreported.
        link_project(second_unit, second_project)
        add_coordinator(second_unit, plain_user)
        other = IssueOrganizationalUnit.objects.create(
            issue=make_issue(second_project),
            organizational_unit=second_unit,
            project=second_project,
            workspace=workspace_with_members,
            routing_state=RoutingState.QUEUED,
            queue_reason=QueueReason.AWAITING_COORDINATOR,
            queued_at=timezone.now() - timedelta(hours=3),
            assignment_due_at=timezone.now() - timedelta(minutes=30),
        )

        assert sweep_assignment_sla() == 1

        assert _alerts_for(other).count() == 1
        assert not _alerts_for(overdue_link).exists()

    def test_the_sweep_does_not_change_the_item(self, overdue_link, plain_user, add_coordinator):
        # ``last_alerted_at`` is a record of what we told people, not a change
        # to the routing: an alert must never move an item in the queue.
        add_coordinator(overdue_link.organizational_unit, plain_user)
        before = IssueOrganizationalUnit.objects.filter(pk=overdue_link.pk).values(
            "routing_state", "queue_reason", "primary_executor", "assignment_due_at", "queued_at"
        )[0]

        sweep_assignment_sla()

        after = IssueOrganizationalUnit.objects.filter(pk=overdue_link.pk).values(
            "routing_state", "queue_reason", "primary_executor", "assignment_due_at", "queued_at"
        )[0]
        assert before == after
