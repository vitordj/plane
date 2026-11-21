# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Regression tests for R2 Frente F4 (member departure and unit deletion lifecycle).

Pins R2.09 and R2.10 (including error code 4944 and invariant I6).
"""

import pytest
from rest_framework import status

from plane.app.services.orca import set_responsibility
from plane.db.models import (
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    OrganizationalUnit,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMembership,
    State,
    StateGroup,
    WorkspaceMember,
)

from .conftest import (
    ROLE_MEMBER,
    coordinators_url,
    member_url,
    members_url,
    unit_url,
)


@pytest.mark.unit
@pytest.mark.django_db
class TestR209WorkspaceDepartureCleansOrgTies:
    """R2.09: removing a workspace member deactivates their lead membership and coordinator role."""

    def test_removing_workspace_member_deactivates_lead_and_coordinator_and_frees_lead_slot(
        self,
        admin_client,
        workspace_with_members,
        unit,
        add_member,
        add_coordinator,
        plain_user,
        second_user,
    ):
        lead_membership = add_member(unit, plain_user, role="lead")
        second_membership = add_member(unit, second_user, role="member")
        coord = add_coordinator(unit, plain_user)
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)

        # Remove plain_user from the workspace via upstream endpoint.
        response = admin_client.delete(f"/api/workspaces/{workspace_with_members.slug}/members/{wm.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data

        lead_membership.refresh_from_db()
        coord.refresh_from_db()
        assert lead_membership.is_active is False
        assert coord.is_active is False

        # Roster and coordinator list no longer show the departed member.
        roster = admin_client.get(members_url(workspace_with_members.slug, unit.id))
        assert roster.status_code == status.HTTP_200_OK
        assert [str(row["member_id"]) for row in roster.data] == [str(second_user.id)]

        coords = admin_client.get(coordinators_url(workspace_with_members.slug, unit.id))
        assert coords.status_code == status.HTTP_200_OK
        assert coords.data == []

        # Promoting second_user to lead now succeeds (no 400 "already has an active lead").
        promote = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, second_membership.id),
            {"role": "lead"},
            format="json",
        )
        assert promote.status_code == status.HTTP_200_OK, promote.data
        assert promote.data["role"] == "lead"

        # Re-activating the workspace member (re-invite) does NOT restore old coordinator authority.
        wm.refresh_from_db()
        wm.is_active = True
        wm.save()
        coord.refresh_from_db()
        assert coord.is_active is False
        assert (
            OrganizationalUnitCoordinator.objects.filter(
                organizational_unit=unit, workspace_member=wm, is_active=True
            ).count()
            == 0
        )
        assert (
            OrganizationalUnitMembership.objects.filter(
                organizational_unit=unit, workspace_member=wm, is_active=True
            ).count()
            == 0
        )


@pytest.mark.unit
@pytest.mark.django_db
class TestR210UnitDeleteGuardAndAuditTrail:
    """R2.10: deleting an area with open work returns 409; closed work logs I6 events on delete."""

    def test_deleting_unit_with_open_work_item_returns_409_and_keeps_unit(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        make_issue,
    ):
        link_project(unit, project, ROLE_MEMBER)
        issue = make_issue(project, name="Open item")
        set_responsibility(issue, unit)

        response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == 4944
        assert response.data["error_message"] == "ORG_UNIT_HAS_ACTIVE_WORK_ITEMS"
        assert OrganizationalUnit.objects.filter(pk=unit.id).exists()
        assert IssueOrganizationalUnit.objects.filter(issue=issue, organizational_unit=unit).exists()

    def test_deleting_unit_with_only_completed_work_succeeds_and_records_i6_event(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        make_issue,
    ):
        link_project(unit, project, ROLE_MEMBER)
        done_state = State.objects.create(
            project=project, workspace=workspace_with_members, name="Done", group=StateGroup.COMPLETED.value
        )
        issue = make_issue(project, name="Closed item")
        set_responsibility(issue, unit)
        issue.state = done_state
        issue.save()

        response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not OrganizationalUnit.objects.filter(pk=unit.id).exists()
        assert not IssueOrganizationalUnit.objects.filter(issue=issue).exists()
        assert IssueResponsibilityEvent.objects.filter(issue=issue, from_unit_id=unit.id, to_unit__isnull=True).exists()
