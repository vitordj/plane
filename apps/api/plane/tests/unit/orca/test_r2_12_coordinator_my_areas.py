# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Regression tests for R2.12 — ``UserOrganizationalUnitsEndpoint`` (``/me/``).

A coordinator who is not a member of the area they coordinate must still see
that area under "My areas" so they have a screen to open its queue and act on
its work items.
"""

import pytest

from plane.db.models import WorkspaceMember

from .conftest import ROLE_GUEST, ROLE_MEMBER


def my_units_url(slug):
    return f"/api/orca/workspaces/{slug}/organizational-units/me/"


@pytest.mark.unit
class TestCoordinatorMyAreas:
    def test_coordinator_outside_unit_receives_unit_in_my_areas(
        self, member_client, workspace_with_members, unit, project, link_project, add_coordinator, plain_user
    ):
        link_project(unit, project)
        add_coordinator(unit, plain_user)

        response = member_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert len(response.data) == 1
        entry = response.data[0]
        assert str(entry["organizational_unit"]["id"]) == str(unit.id)
        assert entry["role"] == "coordinator"
        assert entry["is_coordinator"] is True
        assert len(entry["projects"]) == 1

    def test_member_and_coordinator_is_deduplicated(
        self, member_client, workspace_with_members, unit, add_member, add_coordinator, plain_user
    ):
        add_member(unit, plain_user, role="lead")
        add_coordinator(unit, plain_user)

        response = member_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert len(response.data) == 1
        entry = response.data[0]
        assert str(entry["organizational_unit"]["id"]) == str(unit.id)
        assert entry["role"] == "lead"
        assert entry["is_coordinator"] is True

    def test_plain_member_has_is_coordinator_false(
        self, member_client, workspace_with_members, unit, add_member, plain_user
    ):
        add_member(unit, plain_user, role="member")

        response = member_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["role"] == "member"
        assert response.data[0]["is_coordinator"] is False

    def test_demoted_guest_coordinator_is_excluded(
        self, guest_client, workspace_with_members, unit, second_unit, add_coordinator, guest_user, workspace_member_of
    ):
        # Two coordinated areas; the guest is demoted afterwards. The endpoint
        # must drop both, and a later promotion must bring them back, so the
        # role filter (not an empty table) is what is under test.
        add_coordinator(unit, guest_user)
        add_coordinator(second_unit, guest_user)
        WorkspaceMember.objects.filter(workspace=workspace_with_members, member=guest_user).update(role=ROLE_MEMBER)
        before = guest_client.get(my_units_url(workspace_with_members.slug))
        assert {str(e["organizational_unit"]["id"]) for e in before.data} == {str(unit.id), str(second_unit.id)}

        WorkspaceMember.objects.filter(workspace=workspace_with_members, member=guest_user).update(role=ROLE_GUEST)
        response = guest_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert response.data == []

    def test_demoted_guest_coordinator_keeps_areas_where_they_are_a_member(
        self, guest_client, workspace_with_members, unit, second_unit, add_coordinator, add_member, guest_user
    ):
        add_coordinator(unit, guest_user)
        add_member(second_unit, guest_user, role="member")
        WorkspaceMember.objects.filter(workspace=workspace_with_members, member=guest_user).update(role=ROLE_GUEST)

        response = guest_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert [str(e["organizational_unit"]["id"]) for e in response.data] == [str(second_unit.id)]
        assert response.data[0]["is_coordinator"] is False

    def test_inactive_unit_is_excluded_for_coordinator(
        self, member_client, workspace_with_members, unit, second_unit, add_coordinator, plain_user
    ):
        add_coordinator(unit, plain_user)
        add_coordinator(second_unit, plain_user)
        unit.is_active = False
        unit.save(update_fields=["is_active"])

        response = member_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert [str(e["organizational_unit"]["id"]) for e in response.data] == [str(second_unit.id)]

    def test_inactive_coordinator_row_is_excluded(
        self, member_client, workspace_with_members, unit, second_unit, add_coordinator, plain_user
    ):
        stale = add_coordinator(unit, plain_user)
        add_coordinator(second_unit, plain_user)
        stale.is_active = False
        stale.save(update_fields=["is_active"])

        response = member_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert [str(e["organizational_unit"]["id"]) for e in response.data] == [str(second_unit.id)]

    def test_soft_deleted_coordinator_row_is_excluded(
        self, member_client, workspace_with_members, unit, second_unit, add_coordinator, plain_user
    ):
        gone = add_coordinator(unit, plain_user)
        add_coordinator(second_unit, plain_user)
        gone.delete()

        response = member_client.get(my_units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert [str(e["organizational_unit"]["id"]) for e in response.data] == [str(second_unit.id)]

    def test_coordinator_with_inactive_workspace_membership_is_excluded(
        self, member_client, workspace_with_members, unit, second_unit, add_coordinator, plain_user, workspace_member_of
    ):
        # Rows stay active (no signal ran), but the person is no longer in the
        # workspace; the valid area comes from a different, still-active user.
        add_coordinator(unit, plain_user)
        add_coordinator(second_unit, plain_user)
        WorkspaceMember.objects.filter(pk=workspace_member_of(plain_user).pk).update(is_active=False)

        response = member_client.get(my_units_url(workspace_with_members.slug))

        # Workspace access is gone, so the permission gate answers instead.
        assert response.status_code in (401, 403)
