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

from plane.app.services.orca import plan_access, reconcile_access, reconcile_membership
from plane.db.models import (
    OrganizationalProjectAccessState,
    OrganizationalUnitCoordinator,
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


# --- coordinators ------------------------------------------------------------


def coordinate(unit, workspace_member):
    return OrganizationalUnitCoordinator.objects.create(
        organizational_unit=unit,
        workspace_member=workspace_member,
        workspace=unit.workspace,
    )


@pytest.mark.unit
class TestCoordinatorAccess:
    """
    A coordinator runs an area's queue, which they cannot do without access to
    the projects it covers. What matters here is the withdrawal: taking the
    coordination away must take back only what coordination gave.
    """

    def test_a_coordinator_gains_access_to_the_covered_projects(
        self, org_workspace, make_member, make_project, make_unit
    ):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        maria = make_member("maria")
        coordinate(compliance, maria)

        reconcile_access(org_workspace.id)

        assert project_member(onboarding, maria).role == ROLE_MEMBER

    def test_a_coordinator_is_not_made_a_member_of_the_area(self, org_workspace, make_member, make_project, make_unit):
        """
        Otherwise making somebody responsible for a queue would start pushing
        work at them, which is the opposite of what coordination means.
        """
        compliance = make_unit("Compliance", "compliance")
        link_project(compliance, make_project("Onboarding", "ONB"))
        maria = make_member("maria")
        coordinate(compliance, maria)

        reconcile_access(org_workspace.id)

        assert not OrganizationalUnitMembership.objects.filter(
            organizational_unit=compliance, workspace_member=maria
        ).exists()

    def test_a_manual_admin_is_not_demoted_by_coordinating(self, org_workspace, make_member, make_project, make_unit):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        maria = make_member("maria")
        ProjectMember.objects.create(
            project=onboarding,
            member_id=maria.member_id,
            workspace=org_workspace,
            role=ROLE_ADMIN,
            is_active=True,
        )
        coordinate(compliance, maria)

        reconcile_access(org_workspace.id)

        assert project_member(onboarding, maria).role == ROLE_ADMIN

    def test_withdrawing_coordination_takes_the_access_back(self, org_workspace, make_member, make_project, make_unit):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        maria = make_member("maria")
        coordinator = coordinate(compliance, maria)
        reconcile_access(org_workspace.id)

        coordinator.is_active = False
        coordinator.save(update_fields=["is_active"])
        reconcile_access(org_workspace.id)

        assert project_member(onboarding, maria).is_active is False

    def test_a_coordinator_who_is_also_a_member_keeps_access_after_stepping_down(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """The whole reason the ledger records why access exists."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        maria = make_member("maria")
        add_member(compliance, maria)
        coordinator = coordinate(compliance, maria)
        reconcile_access(org_workspace.id)

        coordinator.is_active = False
        coordinator.save(update_fields=["is_active"])
        reconcile_access(org_workspace.id)

        assert project_member(onboarding, maria).is_active is True

    def test_the_grant_records_which_reason_it_was(self, org_workspace, make_member, make_project, make_unit):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)
        maria = make_member("maria")
        coordinate(compliance, maria)

        reconcile_access(org_workspace.id)

        grant = OrganizationalUnitGrant.objects.get(workspace_member=maria, project=onboarding, is_active=True)
        assert grant.grant_source == "coordinator"
        assert grant.membership_id is None

    def test_reconciling_twice_changes_nothing(self, org_workspace, make_member, make_project, make_unit):
        compliance = make_unit("Compliance", "compliance")
        link_project(compliance, make_project("Onboarding", "ONB"))
        coordinate(compliance, make_member("maria"))
        reconcile_access(org_workspace.id)

        changes = reconcile_access(org_workspace.id)

        assert [change for change in changes if change.action != "none"] == []
