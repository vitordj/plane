# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the Orca organizational access reconciler.

These cover the access rules that make the layer safe to run repeatedly:
inherited access is materialized as native ``ProjectMember`` rows, manual
access is never destroyed, and the strongest role wins when several units
grant access to the same project.
"""

import pytest
from rest_framework.test import APIClient

from plane.app.services.orca import plan_access, reconcile_access, reconcile_membership
from plane.db.models import (
    OrganizationalProjectAccessState,
    OrganizationalUnit,
    OrganizationalUnitGrant,
    OrganizationalUnitMemberRole,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    Project,
    ProjectMember,
    User,
    Workspace,
    WorkspaceMember,
)

ROLE_ADMIN = 20
ROLE_MEMBER = 15
ROLE_GUEST = 5


@pytest.fixture
def owner(db):
    user = User.objects.create(email="owner@plane.so", username="owner", first_name="Owner")
    user.set_password("owner@123")
    user.save()
    return user


@pytest.fixture
def org_workspace(db, owner):
    workspace = Workspace.objects.create(name="Orca", slug="orca", owner=owner)
    WorkspaceMember.objects.create(workspace=workspace, member=owner, role=ROLE_ADMIN)
    return workspace


@pytest.fixture
def make_member(db, org_workspace):
    def _make(name, role=ROLE_MEMBER):
        user = User.objects.create(email=f"{name}@plane.so", username=name, first_name=name.title())
        user.set_password("member@123")
        user.save()
        return WorkspaceMember.objects.create(workspace=org_workspace, member=user, role=role)

    return _make


@pytest.fixture
def make_project(db, org_workspace, owner):
    def _make(name, identifier):
        return Project.objects.create(
            name=name,
            identifier=identifier,
            workspace=org_workspace,
            created_by=owner,
        )

    return _make


@pytest.fixture
def make_unit(db, org_workspace):
    def _make(name, slug):
        return OrganizationalUnit.objects.create(workspace=org_workspace, name=name, slug=slug)

    return _make


def add_member(unit, workspace_member, role=OrganizationalUnitMemberRole.MEMBER):
    return OrganizationalUnitMembership.objects.create(
        organizational_unit=unit,
        workspace_member=workspace_member,
        workspace=unit.workspace,
        role=role,
    )


def link_project(unit, project, role=ROLE_MEMBER):
    return OrganizationalUnitProject.objects.create(
        organizational_unit=unit,
        project=project,
        workspace=unit.workspace,
        default_role=role,
    )


def project_member(project, workspace_member):
    return ProjectMember.objects.filter(project=project, member_id=workspace_member.member_id).first()


@pytest.mark.unit
class TestInheritedAccess:
    def test_joining_a_unit_materializes_project_members(self, org_workspace, make_member, make_project, make_unit):
        """A person joining a unit becomes a native member of every linked project."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        pld = make_project("PLD", "PLD")
        link_project(compliance, onboarding)
        link_project(compliance, pld, role=ROLE_GUEST)

        ana = make_member("ana")
        membership = add_member(compliance, ana)
        reconcile_membership(membership, force_sync=True)

        assert project_member(onboarding, ana).role == ROLE_MEMBER
        assert project_member(onboarding, ana).is_active is True
        assert project_member(pld, ana).role == ROLE_GUEST
        assert OrganizationalUnitGrant.objects.filter(workspace_member=ana, is_active=True).count() == 2

    def test_lead_inherits_the_same_role_as_members(self, org_workspace, make_member, make_project, make_unit):
        """Leading a unit governs the unit, not the projects: no implicit project Admin."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_MEMBER)

        maria = make_member("maria")
        membership = add_member(compliance, maria, role=OrganizationalUnitMemberRole.LEAD)
        reconcile_membership(membership, force_sync=True)

        assert project_member(onboarding, maria).role == ROLE_MEMBER

    def test_strongest_role_wins_across_units(self, org_workspace, make_member, make_project, make_unit):
        """Two units granting different roles on one project resolve to the highest."""
        comercial = make_unit("Comercial", "comercial")
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(comercial, onboarding, role=ROLE_GUEST)
        link_project(compliance, onboarding, role=ROLE_MEMBER)

        ana = make_member("ana")
        add_member(comercial, ana)
        add_member(compliance, ana)
        reconcile_access(org_workspace.id)

        assert project_member(onboarding, ana).role == ROLE_MEMBER

    def test_leaving_one_of_two_units_lowers_but_keeps_access(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """Losing the stronger unit drops to the role the remaining unit grants."""
        comercial = make_unit("Comercial", "comercial")
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(comercial, onboarding, role=ROLE_GUEST)
        link_project(compliance, onboarding, role=ROLE_MEMBER)

        ana = make_member("ana")
        add_member(comercial, ana)
        compliance_membership = add_member(compliance, ana)
        reconcile_access(org_workspace.id)
        assert project_member(onboarding, ana).role == ROLE_MEMBER

        compliance_membership.is_active = False
        compliance_membership.save()
        reconcile_access(org_workspace.id)

        member = project_member(onboarding, ana)
        assert member.is_active is True
        assert member.role == ROLE_GUEST

    def test_leaving_the_only_unit_removes_layer_created_access(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """Access this layer created is withdrawn when its last source disappears."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)

        lucas = make_member("lucas")
        membership = add_member(compliance, lucas)
        reconcile_membership(membership, force_sync=True)
        assert project_member(onboarding, lucas).is_active is True

        membership.is_active = False
        membership.save()
        reconcile_access(org_workspace.id)

        assert project_member(onboarding, lucas).is_active is False
        assert OrganizationalUnitGrant.objects.get(membership=membership).is_active is False


@pytest.mark.unit
class TestManualAccessWins:
    def test_manual_access_survives_leaving_the_unit(self, org_workspace, make_member, make_project, make_unit):
        """Pre-existing manual access is restored, not deleted, when a unit is removed."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_MEMBER)

        lucas = make_member("lucas")
        ProjectMember.objects.create(
            project=onboarding,
            member_id=lucas.member_id,
            workspace=org_workspace,
            role=ROLE_GUEST,
        )

        membership = add_member(compliance, lucas)
        reconcile_membership(membership, force_sync=True)
        assert project_member(onboarding, lucas).role == ROLE_MEMBER

        membership.is_active = False
        membership.save()
        reconcile_access(org_workspace.id)

        member = project_member(onboarding, lucas)
        assert member.is_active is True
        assert member.role == ROLE_GUEST

    def test_manual_promotion_is_never_reverted(self, org_workspace, make_member, make_project, make_unit):
        """A hand-made promotion outranks the layer's claim and is left alone."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_MEMBER)

        maria = make_member("maria")
        membership = add_member(compliance, maria)
        reconcile_membership(membership, force_sync=True)

        promoted = project_member(onboarding, maria)
        promoted.role = ROLE_ADMIN
        promoted.save()

        membership.is_active = False
        membership.save()
        reconcile_access(org_workspace.id)

        member = project_member(onboarding, maria)
        assert member.is_active is True
        assert member.role == ROLE_ADMIN

        state = OrganizationalProjectAccessState.objects.get(workspace_member=maria, project=onboarding)
        assert state.last_applied_role is None

    def test_workspace_guest_is_never_elevated(self, org_workspace, make_member, make_project, make_unit):
        """Inherited roles are capped by the workspace role, mirroring the native API."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_MEMBER)

        visitor = make_member("visitor", role=ROLE_GUEST)
        membership = add_member(compliance, visitor)
        reconcile_membership(membership, force_sync=True)

        assert project_member(onboarding, visitor).role == ROLE_GUEST


@pytest.mark.unit
class TestReconcilerBehavior:
    def test_reconciliation_is_idempotent(self, org_workspace, make_member, make_project, make_unit):
        """Running twice changes nothing the second time."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        ana = make_member("ana")
        add_member(compliance, ana)

        reconcile_access(org_workspace.id)
        second_run = reconcile_access(org_workspace.id)

        assert [change.action for change in second_run] == ["none"]
        assert ProjectMember.objects.filter(project=onboarding, member_id=ana.member_id).count() == 1

    def test_inactive_unit_grants_nothing(self, org_workspace, make_member, make_project, make_unit):
        """Deactivating a unit withdraws the access it sourced."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        ana = make_member("ana")
        add_member(compliance, ana)
        reconcile_access(org_workspace.id)

        compliance.is_active = False
        compliance.save()
        reconcile_access(org_workspace.id)

        assert project_member(onboarding, ana).is_active is False

    def test_plan_access_writes_nothing(self, org_workspace, make_member, make_project, make_unit):
        """The preview used by effective-access is strictly read-only."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        ana = make_member("ana")
        add_member(compliance, ana)

        changes = plan_access(org_workspace.id)

        assert [change.action for change in changes] == ["create"]
        assert ProjectMember.objects.filter(project=onboarding, member_id=ana.member_id).count() == 0
        assert OrganizationalUnitGrant.objects.count() == 0

    def test_cross_workspace_membership_is_rejected(self, org_workspace, owner, make_unit):
        """A unit may only hold members of its own workspace."""
        from django.core.exceptions import ValidationError

        other_workspace = Workspace.objects.create(name="Other", slug="other", owner=owner)
        outsider = WorkspaceMember.objects.create(workspace=other_workspace, member=owner, role=ROLE_MEMBER)
        compliance = make_unit("Compliance", "compliance")

        with pytest.raises(ValidationError):
            add_member(compliance, outsider)


@pytest.mark.unit
class TestManualAccessSurvivesAnElevation:
    """
    The layer raising somebody's role must not erase the evidence that a human
    put them where they were.

    ``_apply_change`` recorded the role it was about to overwrite only when it
    had never written to that pair before. Once the layer owned the row, a
    hand-made promotion sitting on top of it was overwritten silently, and the
    withdrawal that came later read ``current role == last applied role``,
    concluded nothing manual was there, and took the access away outright.
    """

    def test_a_promotion_is_restored_after_the_unit_raises_the_role_and_goes_away(
        self, org_workspace, make_member, make_project, make_unit
    ):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link = link_project(compliance, onboarding, role=ROLE_GUEST)

        lucas = make_member("lucas")
        membership = add_member(compliance, lucas)
        reconcile_membership(membership, force_sync=True)
        assert project_member(onboarding, lucas).role == ROLE_GUEST

        # An admin promotes Lucas by hand, above what Compliance grants.
        promoted = project_member(onboarding, lucas)
        promoted.role = ROLE_MEMBER
        promoted.save()

        # Compliance then starts granting Admin, so the layer writes over the
        # promotion. That write is where the manual role has to be remembered.
        link.default_role = ROLE_ADMIN
        link.save()
        reconcile_access(org_workspace.id)
        assert project_member(onboarding, lucas).role == ROLE_ADMIN

        state = OrganizationalProjectAccessState.objects.get(workspace_member=lucas, project=onboarding)
        assert state.baseline_role == ROLE_MEMBER
        assert state.created_by_org_layer is False

        membership.is_active = False
        membership.save()
        reconcile_access(org_workspace.id)

        member = project_member(onboarding, lucas)
        assert member.is_active is True
        assert member.role == ROLE_MEMBER

    def test_an_elevation_over_the_layers_own_role_records_no_baseline(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """
        The mirror image, and the reason the check is on drift rather than on
        elevation: when the role being overwritten is the one the layer itself
        last wrote, nobody chose it, so there is nothing to fall back to and
        the access goes away with the unit.
        """
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link = link_project(compliance, onboarding, role=ROLE_GUEST)

        maria = make_member("maria")
        membership = add_member(compliance, maria)
        reconcile_membership(membership, force_sync=True)

        link.default_role = ROLE_ADMIN
        link.save()
        reconcile_access(org_workspace.id)
        assert project_member(onboarding, maria).role == ROLE_ADMIN

        state = OrganizationalProjectAccessState.objects.get(workspace_member=maria, project=onboarding)
        assert state.baseline_role is None

        membership.is_active = False
        membership.save()
        reconcile_access(org_workspace.id)

        assert project_member(onboarding, maria).is_active is False


@pytest.fixture
def archive_request(owner, org_workspace):
    """Archive or unarchive a project through the API, as an admin of it."""
    client = APIClient()
    client.force_authenticate(user=owner)

    def _request(project, unarchive=False):
        # The route is project-scoped, so the caller has to hold access to the
        # project. Manual access, and none of the assertions below are about it.
        ProjectMember.objects.get_or_create(
            project=project,
            member=owner,
            defaults={"workspace": org_workspace, "role": ROLE_ADMIN, "is_active": True},
        )
        url = f"/api/workspaces/{org_workspace.slug}/projects/{project.id}/archive/"
        return client.delete(url) if unarchive else client.post(url)

    return _request


@pytest.mark.unit
class TestArchivingAProject:
    """
    Archiving is the one way a project stops being a source of inherited
    access without anybody touching the unit. The resolver already skips
    archived projects, so the access has no source the moment the flag is
    set — but nothing recomputed it, and the inherited ``ProjectMember`` row
    stayed active until somebody reconciled that project by hand.
    """

    def test_archiving_withdraws_the_inherited_access(
        self, org_workspace, make_member, make_project, make_unit, archive_request
    ):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        lucas = make_member("lucas")
        reconcile_membership(add_member(compliance, lucas), force_sync=True)
        assert project_member(onboarding, lucas).is_active is True

        response = archive_request(onboarding)

        assert response.status_code == 200
        assert project_member(onboarding, lucas).is_active is False

    def test_unarchiving_grants_it_again(self, org_workspace, make_member, make_project, make_unit, archive_request):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        lucas = make_member("lucas")
        reconcile_membership(add_member(compliance, lucas), force_sync=True)
        archive_request(onboarding)

        response = archive_request(onboarding, unarchive=True)

        assert response.status_code == 204
        assert project_member(onboarding, lucas).is_active is True

    def test_access_somebody_granted_by_hand_survives_archiving(
        self, org_workspace, make_member, make_project, make_unit, archive_request
    ):
        """The layer withdraws what it granted, and only that."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        lucas = make_member("lucas")
        ProjectMember.objects.create(
            project=onboarding,
            member_id=lucas.member_id,
            workspace=org_workspace,
            role=ROLE_GUEST,
            is_active=True,
        )
        reconcile_membership(add_member(compliance, lucas), force_sync=True)

        archive_request(onboarding)

        member = project_member(onboarding, lucas)
        assert member.is_active is True
        assert member.role == ROLE_GUEST

    def test_the_kill_switch_stops_the_reconciliation(
        self, settings, org_workspace, make_member, make_project, make_unit, archive_request
    ):
        """Archiving still works; the layer simply does not act."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        lucas = make_member("lucas")
        reconcile_membership(add_member(compliance, lucas), force_sync=True)
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = archive_request(onboarding)

        assert response.status_code == 200
        assert project_member(onboarding, lucas).is_active is True
