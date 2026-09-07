# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who an area's stuck-work alert reaches, and what the row it writes says.

The recipient rule is the part worth pinning: coordinators, then the lead as a
*fallback* rather than an addition. Getting that wrong is invisible in
production — appointing a coordinator would quietly stop alerting the lead, or
keep alerting them forever — so both branches are asserted, along with the
third case where the area has nobody and the alert has to end in a log instead
of an exception.
"""

import pytest

from plane.app.services.orca import alerts
from plane.db.models import (
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitMemberRole,
    QueueReason,
    RoutingState,
)


def _link(unit, issue, workspace, *, state=RoutingState.ALLOCATION_FAILED):
    return IssueOrganizationalUnit.objects.create(
        issue=issue,
        organizational_unit=unit,
        project=issue.project,
        workspace=workspace,
        routing_state=state,
        queue_reason=QueueReason.NO_ELIGIBLE_MEMBER,
    )


@pytest.mark.unit
@pytest.mark.django_db
class TestRecipientsFor:
    def test_active_coordinators_are_the_recipients(
        self, unit, workspace_with_members, admin_user, plain_user, add_coordinator, add_member
    ):
        add_coordinator(unit, plain_user)
        # A lead exists too, and must not be reached: the coordinator rule wins
        # outright rather than adding to it.
        add_member(unit, admin_user, role=OrganizationalUnitMemberRole.LEAD)

        assert alerts.recipients_for(unit) == [plain_user.id]

    def test_two_coordinators_both_hear(self, unit, plain_user, second_user, add_coordinator):
        add_coordinator(unit, plain_user)
        add_coordinator(unit, second_user)

        assert set(alerts.recipients_for(unit)) == {plain_user.id, second_user.id}

    def test_an_inactive_coordinator_falls_through_to_the_lead(
        self, unit, admin_user, plain_user, add_coordinator, add_member
    ):
        coordinator = add_coordinator(unit, plain_user)
        coordinator.is_active = False
        coordinator.save(update_fields=["is_active"])
        add_member(unit, admin_user, role=OrganizationalUnitMemberRole.LEAD)

        # Deactivating coordination has to behave like never having had one,
        # otherwise revoking a coordinator silences the area.
        assert alerts.recipients_for(unit) == [admin_user.id]

    def test_the_lead_is_the_fallback_when_there_is_no_coordinator(self, unit, admin_user, plain_user, add_member):
        add_member(unit, plain_user)  # an ordinary member is not a recipient
        add_member(unit, admin_user, role=OrganizationalUnitMemberRole.LEAD)

        assert alerts.recipients_for(unit) == [admin_user.id]

    def test_an_area_with_neither_reaches_nobody(self, unit, plain_user, add_member):
        add_member(unit, plain_user)

        assert alerts.recipients_for(unit) == []


@pytest.mark.unit
@pytest.mark.django_db
class TestNotify:
    def test_one_notification_per_recipient_with_the_orca_sender(
        self,
        unit,
        workspace_with_members,
        project,
        make_issue,
        plain_user,
        second_user,
        add_coordinator,
    ):
        add_coordinator(unit, plain_user)
        add_coordinator(unit, second_user)
        issue = make_issue(project)
        link = _link(unit, issue, workspace_with_members)

        written = alerts.notify(link, alerts.KIND_ALLOCATION_FAILED)

        assert written == 2
        rows = Notification.objects.filter(entity_identifier=issue.id)
        assert rows.count() == 2
        assert set(rows.values_list("receiver_id", flat=True)) == {plain_user.id, second_user.id}
        row = rows.first()
        assert row.sender == "in_app:orca:allocation_failed"
        # Nobody did this; a deadline passed or an allocator failed.
        assert row.triggered_by_id is None
        assert row.entity_name == "issue"
        assert row.workspace_id == workspace_with_members.id
        assert row.project_id == project.id

    def test_the_payload_carries_the_area_and_the_routing_state(
        self, unit, workspace_with_members, project, make_issue, plain_user, add_coordinator
    ):
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        link = _link(unit, issue, workspace_with_members)

        alerts.notify(link, alerts.KIND_ALLOCATION_FAILED)

        data = Notification.objects.get(entity_identifier=issue.id).data
        assert data["kind"] == alerts.KIND_ALLOCATION_FAILED
        assert data["routing_state"] == RoutingState.ALLOCATION_FAILED
        assert data["queue_reason"] == QueueReason.NO_ELIGIBLE_MEMBER
        assert data["organizational_unit"]["id"] == str(unit.id)
        assert data["organizational_unit"]["name"] == unit.name
        assert data["issue"]["id"] == str(issue.id)
        assert data["issue"]["sequence_id"] == issue.sequence_id
        assert data["issue"]["identifier"] == project.identifier

    def test_the_overdue_kind_has_its_own_sender_and_title(
        self, unit, workspace_with_members, project, make_issue, plain_user, add_coordinator
    ):
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        link = _link(unit, issue, workspace_with_members, state=RoutingState.QUEUED)

        alerts.notify(link, alerts.KIND_ASSIGNMENT_OVERDUE)

        row = Notification.objects.get(entity_identifier=issue.id)
        assert row.sender == "in_app:orca:assignment_overdue"
        # A distinct title, not the allocation one reused: the two situations
        # need different actions from whoever reads the inbox.
        assert row.title == alerts._TITLES[alerts.KIND_ASSIGNMENT_OVERDUE]
        assert row.title != alerts._TITLES[alerts.KIND_ALLOCATION_FAILED]
        assert row.data["kind"] == alerts.KIND_ASSIGNMENT_OVERDUE

    def test_nothing_is_written_when_the_area_has_nobody(self, unit, workspace_with_members, project, make_issue):
        issue = make_issue(project)
        link = _link(unit, issue, workspace_with_members)

        assert alerts.notify(link, alerts.KIND_ALLOCATION_FAILED) == 0
        assert not Notification.objects.filter(entity_identifier=issue.id).exists()

    def test_notifying_does_not_touch_last_alerted_at(
        self, unit, workspace_with_members, project, make_issue, plain_user, add_coordinator
    ):
        # The de-duplication window belongs to the sweep. If ``notify`` stamped
        # the row, the immediate hook would suppress the sweep's first alert.
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        link = _link(unit, issue, workspace_with_members)

        alerts.notify(link, alerts.KIND_ALLOCATION_FAILED)

        link.refresh_from_db()
        assert link.last_alerted_at is None
