# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Review gaps on the area lifecycle (batch C).

Pins two follow-ups of R2.09 / R2.10:

* a lead (or coordinator) whose workspace membership was deactivated by a path
  that fires no signals (upstream's ``bulk_update``) no longer blocks the lead
  slot, and does not get the role back if the member is reactivated;
* the unit delete guard ignores work items of soft-deleted projects and runs
  its check and delete under a row lock.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status

from plane.app.services.orca import set_responsibility
from plane.db.models import (
    OrganizationalUnit,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMembership,
    Project,
    WorkspaceMember,
)

from .conftest import ROLE_MEMBER, member_url, members_url, unit_url


def _deactivate_like_upstream(workspace_member):
    """Deactivate the way ``user/base.py`` does: ``bulk_update``, so no signal fires."""
    workspace_member.is_active = False
    WorkspaceMember.objects.bulk_update([workspace_member], ["is_active"])


@pytest.mark.unit
@pytest.mark.django_db
class TestDepartedLeadDoesNotBlockLeadSlot:
    def _departed_lead(self, unit, second_user, add_member, add_coordinator, workspace_member_of):
        lead = add_member(unit, second_user, role="lead")
        coordinator = add_coordinator(unit, second_user)
        _deactivate_like_upstream(workspace_member_of(second_user))
        return lead, coordinator

    def test_naming_a_new_lead_retires_stale_rows_and_succeeds(
        self,
        admin_client,
        workspace_with_members,
        unit,
        second_user,
        plain_user,
        add_member,
        add_coordinator,
        workspace_member_of,
    ):
        lead, coordinator = self._departed_lead(unit, second_user, add_member, add_coordinator, workspace_member_of)

        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": [str(workspace_member_of(plain_user).id)], "role": "lead"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        lead.refresh_from_db()
        coordinator.refresh_from_db()
        assert lead.is_active is False
        assert coordinator.is_active is False
        new_lead = OrganizationalUnitMembership.objects.get(
            organizational_unit=unit, workspace_member=workspace_member_of(plain_user)
        )
        assert new_lead.role == "lead" and new_lead.is_active is True

    def test_promoting_an_existing_member_to_lead_succeeds(
        self,
        admin_client,
        workspace_with_members,
        unit,
        second_user,
        plain_user,
        add_member,
        add_coordinator,
        workspace_member_of,
    ):
        lead, _ = self._departed_lead(unit, second_user, add_member, add_coordinator, workspace_member_of)
        candidate = add_member(unit, plain_user, role="member")

        response = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, candidate.id), {"role": "lead"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        lead.refresh_from_db()
        candidate.refresh_from_db()
        assert lead.is_active is False
        assert candidate.role == "lead"

    def test_reactivated_member_does_not_get_lead_or_coordinator_back(
        self,
        admin_client,
        workspace_with_members,
        unit,
        second_user,
        plain_user,
        add_member,
        add_coordinator,
        workspace_member_of,
    ):
        lead, coordinator = self._departed_lead(unit, second_user, add_member, add_coordinator, workspace_member_of)
        admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": [str(workspace_member_of(plain_user).id)], "role": "lead"},
            format="json",
        )

        # The person is re-invited to the workspace.
        WorkspaceMember.objects.filter(pk=workspace_member_of(second_user).pk).update(is_active=True)

        lead.refresh_from_db()
        coordinator.refresh_from_db()
        assert lead.is_active is False
        assert coordinator.is_active is False
        assert (
            OrganizationalUnitMembership.objects.filter(
                organizational_unit=unit, role="lead", is_active=True, workspace_member__is_active=True
            ).count()
            == 1
        )
        assert not OrganizationalUnitCoordinator.objects.filter(
            organizational_unit=unit, workspace_member=workspace_member_of(second_user), is_active=True
        ).exists()


@pytest.mark.unit
@pytest.mark.django_db
class TestUnitDeleteGuardEdges:
    def test_work_items_of_a_soft_deleted_project_do_not_block_deletion(
        self, admin_client, workspace_with_members, unit, project, link_project, make_issue
    ):
        link_project(unit, project, ROLE_MEMBER)
        issue = make_issue(project, name="Orphaned by project delete")
        set_responsibility(issue, unit)
        # Soft delete without running the async cascade, which is the window
        # the guard has to tolerate.
        Project.objects.filter(pk=project.pk).update(deleted_at=timezone.now())

        response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
        assert not OrganizationalUnit.objects.filter(pk=unit.id).exists()

    def test_open_work_item_of_a_live_project_still_blocks_deletion(
        self, admin_client, workspace_with_members, unit, project, link_project, make_issue
    ):
        link_project(unit, project, ROLE_MEMBER)
        set_responsibility(make_issue(project, name="Live item"), unit)

        response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_check_and_delete_run_under_a_row_lock_on_the_unit(self, admin_client, workspace_with_members, unit):
        with CaptureQueriesContext(connection) as queries:
            response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        locking = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"] and "organizational_units" in q["sql"]]
        assert locking, "the unit row must be locked before the open-work check"
