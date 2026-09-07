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

from plane.app.services.orca import (
    plan_access,
    reconcile_access,
    reconcile_coordinator,
    reconcile_membership,
    reconcile_unit,
)
from plane.db.models import (
    GrantSource,
    OrganizationalProjectAccessState,
    OrganizationalUnit,
    OrganizationalUnitCoordinator,
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


def add_coordinator(unit, workspace_member):
    return OrganizationalUnitCoordinator.objects.create(
        organizational_unit=unit,
        workspace_member=workspace_member,
        workspace=unit.workspace,
    )


@pytest.mark.unit
class TestCoordinatorAccess:
    """
    Coordinating an area is a second way to reach its projects.

    The point of every test here is the same asymmetry: coordination grants on
    its own account, so it must also be withdrawn on its own account — without
    disturbing what a membership, or a human, already justified.
    """

    def test_a_coordinator_reaches_every_project_the_area_covers(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """Coordination alone is enough; belonging to the area is not required (RFC §5.2)."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        pld = make_project("PLD", "PLD")
        link_project(compliance, onboarding)
        link_project(compliance, pld)

        rita = make_member("rita")
        reconcile_coordinator(add_coordinator(compliance, rita), force_sync=True)

        assert project_member(onboarding, rita).role == ROLE_MEMBER
        assert project_member(pld, rita).role == ROLE_MEMBER
        assert not OrganizationalUnitMembership.objects.filter(
            organizational_unit=compliance, workspace_member=rita
        ).exists()

    def test_a_coordinator_grant_names_the_coordination_not_a_membership(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """
        Provenance is what makes the withdrawal below surgical, so it is pinned
        here on its own: the grant carries the coordination and no membership.
        """
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)

        rita = make_member("rita")
        coordinator = add_coordinator(compliance, rita)
        reconcile_coordinator(coordinator, force_sync=True)

        grant = OrganizationalUnitGrant.objects.get(workspace_member=rita, project=onboarding, is_active=True)
        assert grant.grant_source == GrantSource.COORDINATOR
        assert grant.coordinator_id == coordinator.id
        assert grant.membership_id is None
        # Member, not the link's default_role: coordinating is permission to
        # hand work out, never to administer the projects it lives in.
        assert grant.granted_role == ROLE_MEMBER

    def test_a_coordinator_does_not_inherit_a_role_above_member(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """An area that grants Admin to its members still only grants Member to its coordinator."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_ADMIN)

        rita = make_member("rita")
        reconcile_coordinator(add_coordinator(compliance, rita), force_sync=True)

        assert project_member(onboarding, rita).role == ROLE_MEMBER

    def test_a_manual_admin_is_not_demoted_by_becoming_a_coordinator(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """The inherited role is a floor. A role a human chose above it stands."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)

        rita = make_member("rita")
        ProjectMember.objects.create(
            project=onboarding,
            member_id=rita.member_id,
            workspace=org_workspace,
            role=ROLE_ADMIN,
            is_active=True,
        )

        reconcile_coordinator(add_coordinator(compliance, rita), force_sync=True)

        assert project_member(onboarding, rita).role == ROLE_ADMIN

    def test_removing_the_coordinator_restores_the_baseline(self, org_workspace, make_member, make_project, make_unit):
        """
        What coordination added is what coordination takes back: a person who
        was a Guest by hand is a Guest again, not deactivated.
        """
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)

        rita = make_member("rita")
        ProjectMember.objects.create(
            project=onboarding,
            member_id=rita.member_id,
            workspace=org_workspace,
            role=ROLE_GUEST,
            is_active=True,
        )

        coordinator = add_coordinator(compliance, rita)
        reconcile_coordinator(coordinator, force_sync=True)
        assert project_member(onboarding, rita).role == ROLE_MEMBER

        coordinator.is_active = False
        coordinator.save()
        reconcile_coordinator(coordinator, force_sync=True)

        member = project_member(onboarding, rita)
        assert member.is_active is True
        assert member.role == ROLE_GUEST
        assert not OrganizationalUnitGrant.objects.filter(
            coordinator=coordinator, project=onboarding, is_active=True
        ).exists()

    def test_a_coordinator_who_is_also_a_member_keeps_access_after_stepping_down(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """
        The two ties are independent sources. Ending one leaves the other
        standing — which is the whole reason a grant records which is which.
        """
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)

        rita = make_member("rita")
        add_member(compliance, rita)
        coordinator = add_coordinator(compliance, rita)
        reconcile_unit(compliance, force_sync=True)

        assert (
            OrganizationalUnitGrant.objects.filter(workspace_member=rita, project=onboarding, is_active=True).count()
            == 2
        )

        coordinator.is_active = False
        coordinator.save()
        reconcile_unit(compliance, force_sync=True)

        member = project_member(onboarding, rita)
        assert member.is_active is True
        assert member.role == ROLE_MEMBER
        remaining = OrganizationalUnitGrant.objects.filter(workspace_member=rita, project=onboarding, is_active=True)
        assert remaining.count() == 1
        assert remaining.first().grant_source == GrantSource.MEMBERSHIP

    def test_a_membership_grant_is_untouched_by_the_new_column(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """Nothing changes for an area with no coordinator: the old shape is the default."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding)

        ana = make_member("ana")
        membership = add_member(compliance, ana)
        reconcile_membership(membership, force_sync=True)

        grant = OrganizationalUnitGrant.objects.get(workspace_member=ana, project=onboarding, is_active=True)
        assert grant.grant_source == GrantSource.MEMBERSHIP
        assert grant.membership_id == membership.id
        assert grant.coordinator_id is None


@pytest.mark.unit
class TestAWorkspaceDemotionIsNotAManualChoice:
    """
    R1.A2 — access that outlived the area that granted it.

    The layer only lowers or withdraws while the current ``ProjectMember.role``
    still equals the role it last wrote. That drift check assumes a difference
    can only have come from a person, and the core breaks the assumption: a
    demotion to workspace Guest rewrites every one of that person's
    ``ProjectMember`` rows to 5 on its own.

    The layer used to see the rewrite, agree with it -- the capped target is 5
    too, so there is nothing to write -- and leave ``last_applied_role`` naming
    the old role. On the way back up, the drift check compared against that
    stale claim, saw a difference, and recorded the core's write as a human's
    choice. Leaving the area then restored that "choice" instead of withdrawing,
    and somebody who only ever reached the project through the area kept it.
    """

    def test_the_claim_follows_the_workspace_cap_down(self, org_workspace, make_member, make_project, make_unit):
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_MEMBER)
        bruno = make_member("bruno")
        reconcile_membership(add_member(compliance, bruno), force_sync=True)

        bruno.role = ROLE_GUEST
        bruno.save()
        ProjectMember.objects.filter(member=bruno.member, project=onboarding).update(role=ROLE_GUEST)
        reconcile_access(org_workspace.id)

        state = OrganizationalProjectAccessState.objects.get(workspace_member=bruno, project=onboarding)
        # The whole fix: the claim names the role the person actually holds.
        assert state.last_applied_role == ROLE_GUEST
        assert state.baseline_role is None
        assert state.created_by_org_layer is True

    def test_leaving_the_area_after_a_round_trip_through_guest_withdraws_access(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """The four steps of the finding, in order."""
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_MEMBER)
        bruno = make_member("bruno")
        membership = add_member(compliance, bruno)
        reconcile_membership(membership, force_sync=True)
        assert project_member(onboarding, bruno).role == ROLE_MEMBER

        # 2. Demoted to workspace Guest; the core rewrites the ProjectMember.
        bruno.role = ROLE_GUEST
        bruno.save()
        ProjectMember.objects.filter(member=bruno.member, project=onboarding).update(role=ROLE_GUEST)
        reconcile_access(org_workspace.id)

        # 3. Restored to workspace Member.
        bruno.role = ROLE_MEMBER
        bruno.save()
        reconcile_access(org_workspace.id)
        assert project_member(onboarding, bruno).role == ROLE_MEMBER
        state = OrganizationalProjectAccessState.objects.get(workspace_member=bruno, project=onboarding)
        # Nobody chose anything by hand, so there is no baseline to fall back to.
        assert state.baseline_role is None

        # 4. Leaves the area. The only reason he was in the project is gone.
        membership.is_active = False
        membership.save()
        reconcile_access(org_workspace.id)

        assert project_member(onboarding, bruno).is_active is False

    def test_a_real_manual_promotion_still_survives_the_same_round_trip(
        self, org_workspace, make_member, make_project, make_unit
    ):
        """
        The fix must not cost the guarantee it sits next to: access a person
        granted by hand outlives the area, round trip or no round trip.
        """
        compliance = make_unit("Compliance", "compliance")
        onboarding = make_project("Onboarding", "ONB")
        link_project(compliance, onboarding, role=ROLE_GUEST)
        ana = make_member("ana")
        ProjectMember.objects.create(
            project=onboarding, member=ana.member, workspace=org_workspace, role=ROLE_ADMIN, is_active=True
        )
        membership = add_member(compliance, ana)
        reconcile_membership(membership, force_sync=True)

        membership.is_active = False
        membership.save()
        reconcile_access(org_workspace.id)

        member_row = project_member(onboarding, ana)
        assert member_row.is_active is True
        assert member_row.role == ROLE_ADMIN
