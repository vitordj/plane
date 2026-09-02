# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the projection from the directory mirror onto the organizational layer.

These cover the promises the fork makes to an administrator who connects a
directory: that a sync never destroys a decision a human made, that people the
directory pushes who are not workspace members are parked rather than lost, and
that access really is withdrawn when somebody leaves a group upstream.
"""

import pytest

from plane.app.services.orca import (
    project_unit,
    project_workspace,
    reconcile_unit,
    resolve_identity,
    unresolved_identities,
)
from plane.db.models import (
    DirectoryIdentityState,
    DirectorySyncSource,
    OrganizationalUnitMembership,
    ProjectMember,
)

from .conftest import ROLE_MEMBER


@pytest.mark.unit
class TestIdentityResolution:
    def test_an_identity_matching_a_workspace_member_links(self, make_identity, plain_user, workspace_member_of):
        identity = make_identity("plain@plane.so")

        assert resolve_identity(identity) is True
        identity.refresh_from_db()
        assert identity.state == DirectoryIdentityState.LINKED
        assert identity.workspace_member_id == workspace_member_of(plain_user).id

    def test_matching_ignores_email_casing(self, make_identity, plain_user, workspace_member_of):
        """Entra does not normalize UPN casing, so neither may the join key."""
        identity = make_identity("PLAIN@Plane.So")

        assert resolve_identity(identity) is True
        identity.refresh_from_db()
        assert identity.workspace_member_id == workspace_member_of(plain_user).id

    def test_user_name_is_used_when_the_email_attribute_is_absent(self, make_identity, plain_user):
        """Tenants that map only userName still resolve, because the UPN is the mailbox."""
        identity = make_identity("plain@plane.so", email="")

        assert resolve_identity(identity) is True

    def test_someone_who_is_not_a_workspace_member_stays_unresolved(self, make_identity):
        identity = make_identity("nobody@plane.so")

        assert resolve_identity(identity) is False
        identity.refresh_from_db()
        assert identity.state == DirectoryIdentityState.UNRESOLVED
        assert identity.workspace_member_id is None

    def test_a_deactivated_workspace_member_does_not_resolve(self, make_identity, plain_user, workspace_member_of):
        """Somebody removed from the workspace must not regain access via the directory."""
        member = workspace_member_of(plain_user)
        member.is_active = False
        member.save()

        assert resolve_identity(make_identity("plain@plane.so")) is False


@pytest.mark.unit
class TestProjection:
    def test_a_group_member_becomes_a_unit_membership_and_project_access(
        self, bound_unit, project, link_project, make_identity, put_in_group, plain_user
    ):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        resolve_identity(identity)
        put_in_group(bound_unit, identity)

        result = project_unit(bound_unit)

        assert result.memberships_created == 1
        membership = OrganizationalUnitMembership.objects.get(organizational_unit=bound_unit)
        assert membership.sync_source == DirectorySyncSource.SCIM
        # The whole point of the layer: it lands as native project access.
        assert ProjectMember.objects.filter(
            project=project, member=plain_user, role=ROLE_MEMBER, is_active=True
        ).exists()

    def test_an_unresolved_identity_grants_nothing_and_is_reported(
        self, bound_unit, project, link_project, make_identity, put_in_group, workspace_with_members
    ):
        link_project(bound_unit, project)
        put_in_group(bound_unit, make_identity("nobody@plane.so"))

        result = project_unit(bound_unit)

        assert result.memberships_created == 0
        assert result.unresolved_user_names == ["nobody@plane.so"]
        assert OrganizationalUnitMembership.objects.filter(organizational_unit=bound_unit).count() == 0
        assert unresolved_identities(workspace_with_members.id).count() == 1

    def test_leaving_the_group_withdraws_the_membership_the_directory_created(
        self, bound_unit, project, link_project, make_identity, put_in_group, plain_user
    ):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        resolve_identity(identity)
        group_row = put_in_group(bound_unit, identity)
        project_unit(bound_unit)

        group_row.delete()
        result = project_unit(bound_unit)

        assert result.memberships_deactivated == 1
        assert OrganizationalUnitMembership.objects.get(organizational_unit=bound_unit).is_active is False
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_manual_membership_survives_the_person_being_absent_from_the_group(
        self, bound_unit, project, link_project, add_member, plain_user
    ):
        """The core promise: a sync only takes back what the sync gave."""
        link_project(bound_unit, project)
        add_member(bound_unit, plain_user)  # sync_source defaults to manual
        # The fixture writes the membership straight to the database, so the
        # access it implies has to be materialized before a sync can be asked
        # to leave it alone.
        reconcile_unit(bound_unit, force_sync=True)
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

        result = project_unit(bound_unit)

        assert result.memberships_deactivated == 0
        membership = OrganizationalUnitMembership.objects.get(organizational_unit=bound_unit)
        assert membership.is_active is True
        assert membership.sync_source == DirectorySyncSource.MANUAL
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_manual_membership_the_directory_also_asserts_stays_manual(
        self, bound_unit, add_member, make_identity, put_in_group, plain_user
    ):
        """
        Provenance must not be upgraded to ``scim`` just because the directory
        agrees — otherwise a later removal upstream would revoke access an admin
        granted deliberately.
        """
        add_member(bound_unit, plain_user)
        identity = make_identity("plain@plane.so")
        resolve_identity(identity)
        put_in_group(bound_unit, identity)

        project_unit(bound_unit)

        assert (
            OrganizationalUnitMembership.objects.get(organizational_unit=bound_unit).sync_source
            == DirectorySyncSource.MANUAL
        )

    def test_deactivating_someone_in_the_directory_withdraws_their_access(
        self, bound_unit, project, link_project, make_identity, put_in_group, plain_user
    ):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        resolve_identity(identity)
        put_in_group(bound_unit, identity)
        project_unit(bound_unit)

        identity.is_active = False
        identity.save()
        project_unit(bound_unit)

        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_projection_is_idempotent(self, bound_unit, project, link_project, make_identity, put_in_group):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        resolve_identity(identity)
        put_in_group(bound_unit, identity)

        project_unit(bound_unit)
        second = project_unit(bound_unit)

        assert second.memberships_created == 0
        assert second.memberships_deactivated == 0

    def test_withdrawal_can_be_switched_off_for_the_workspace(
        self, bound_unit, project, link_project, make_identity, put_in_group, directory_connection
    ):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        resolve_identity(identity)
        group_row = put_in_group(bound_unit, identity)
        project_unit(bound_unit)

        directory_connection.deprovision_removes_membership = False
        directory_connection.save()
        group_row.delete()
        result = project_unit(bound_unit)

        assert result.memberships_deactivated == 0
        assert OrganizationalUnitMembership.objects.get(organizational_unit=bound_unit).is_active is True


@pytest.mark.unit
class TestWorkspaceProjection:
    def test_someone_joining_the_workspace_later_gets_their_area(
        self,
        bound_unit,
        project,
        link_project,
        make_identity,
        put_in_group,
        workspace_with_members,
        outsider_user,
    ):
        """
        The whole reason the mirror is kept separately: the directory pushed
        this person before they had workspace access, and nothing in SCIM will
        tell us when that changes.
        """
        from plane.db.models import WorkspaceMember

        link_project(bound_unit, project)
        identity = make_identity(outsider_user.email)
        put_in_group(bound_unit, identity)
        project_unit(bound_unit)
        assert OrganizationalUnitMembership.objects.count() == 0

        WorkspaceMember.objects.create(workspace=workspace_with_members, member=outsider_user, role=ROLE_MEMBER)
        result = project_workspace(workspace_with_members.id)

        assert result.memberships_created == 1
        assert ProjectMember.objects.filter(project=project, member=outsider_user, is_active=True).exists()

    def test_unbound_units_are_left_alone_by_a_workspace_projection(
        self, unit, add_member, plain_user, workspace_with_members
    ):
        """A unit nobody bound to a group is not the directory's business."""
        add_member(unit, plain_user)

        project_workspace(workspace_with_members.id)

        assert OrganizationalUnitMembership.objects.get(organizational_unit=unit).is_active is True
