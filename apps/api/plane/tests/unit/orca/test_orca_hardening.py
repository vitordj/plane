# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Regression tests for the pre-production hardening pass.

Each class here pins one defect found reviewing the organizational layer before
promoting it. They are grouped by the failure they prevent rather than by the
module they touch, because that is how they will be read when one of them goes
red: the question will be "what broke", not "which file".
"""

import pytest
from django.core.management import CommandError, call_command

from plane.db.models import (
    IssueOrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    ProjectMember,
)
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole

from .conftest import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_MEMBER,
    issue_unit_url,
    member_url,
    members_url,
    unit_project_url,
    unit_projects_url,
    units_url,
)


@pytest.fixture
def project_with_admin(project, workspace_with_members, admin_user):
    """The issue routes are project-scoped, so the admin needs project membership."""
    ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )
    return project


@pytest.mark.unit
class TestResponsibleUnitCanBeSetClearedAndSetAgain:
    """
    Clearing a responsible unit is a soft delete: the row stays in the table
    with ``deleted_at`` set. While ``issue`` was a OneToOneField its unique
    index covered that dead row too, so the next POST tried to insert a second
    link for the same issue and hit a unique violation. The default manager
    hides soft-deleted rows, so ``get_or_create`` could not find the row to
    reuse either — the endpoint had no way out.

    Verified against the pre-fix model: all four cases below failed. The
    IntegrityError does not surface as a 500 — ``BaseAPIView.handle_exception``
    converts it into ``400 {"error": "The payload is not valid"}`` — so the
    symptom was a work item whose area could never be set again, rejected with
    a message pointing at the payload rather than at the dead row.
    """

    def test_setting_clearing_and_setting_again_succeeds(
        self, admin_client, workspace_with_members, project_with_admin, unit, second_unit, make_issue
    ):
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)

        first = admin_client.post(url, {"organizational_unit_id": str(unit.id)}, format="json")
        cleared = admin_client.delete(url)
        second = admin_client.post(url, {"organizational_unit_id": str(second_unit.id)}, format="json")

        assert first.status_code == 200
        assert cleared.status_code == 204
        # The regression was a 500 from an IntegrityError on this call.
        assert second.status_code == 200
        assert second.data["organizational_unit"]["slug"] == "legal"

    def test_exactly_one_live_link_survives_the_cycle(
        self, admin_client, workspace_with_members, project_with_admin, unit, second_unit, make_issue
    ):
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(unit.id)}, format="json")
        admin_client.delete(url)
        admin_client.post(url, {"organizational_unit_id": str(second_unit.id)}, format="json")

        live = IssueOrganizationalUnit.objects.filter(issue=issue)

        assert live.count() == 1
        assert live.first().organizational_unit_id == second_unit.id

    def test_the_cleared_link_is_kept_as_history(
        self, admin_client, workspace_with_members, project_with_admin, unit, second_unit, make_issue
    ):
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(unit.id)}, format="json")
        admin_client.delete(url)
        admin_client.post(url, {"organizational_unit_id": str(second_unit.id)}, format="json")

        # all_objects bypasses the soft-delete manager, so this sees history.
        every_row = IssueOrganizationalUnit.all_objects.filter(issue=issue)
        cleared = every_row.filter(deleted_at__isnull=False)

        assert every_row.count() == 2
        assert cleared.count() == 1
        assert cleared.first().organizational_unit_id == unit.id

    def test_the_cycle_can_repeat(
        self, admin_client, workspace_with_members, project_with_admin, unit, second_unit, make_issue
    ):
        # One round could pass on a lucky ordering; three prove the constraint
        # is genuinely partial rather than merely unhit.
        issue = make_issue(project_with_admin)
        url = issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id)

        statuses = []
        for target in (unit, second_unit, unit):
            statuses.append(
                admin_client.post(url, {"organizational_unit_id": str(target.id)}, format="json").status_code
            )
            statuses.append(admin_client.delete(url).status_code)

        assert statuses == [200, 204, 200, 204, 200, 204]
        assert IssueOrganizationalUnit.objects.filter(issue=issue).count() == 0
        assert IssueOrganizationalUnit.all_objects.filter(issue=issue).count() == 3


@pytest.mark.unit
class TestIdentityFieldsCannotBeRepointedByPatch:
    """
    A membership's ``workspace_member`` and a link's ``project`` are identity,
    not attributes. When they were writable, a PATCH could move a membership
    onto a different person; the view then reconciled the *new* person only,
    leaving the previous one holding the ProjectMember rows this membership had
    granted them — residual access with nothing left pointing at it.
    """

    def test_patching_workspace_member_does_not_move_the_membership(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        second_user,
        workspace_member_of,
    ):
        link_project(unit, project)
        membership = add_member(unit, plain_user)
        original = membership.workspace_member_id

        response = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, membership.id),
            {"workspace_member": str(workspace_member_of(second_user).id)},
            format="json",
        )
        membership.refresh_from_db()

        assert response.status_code == 200
        assert membership.workspace_member_id == original

    def test_the_previous_member_keeps_no_residual_project_access(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        second_user,
        workspace_member_of,
    ):
        link_project(unit, project)
        membership = add_member(unit, plain_user)
        admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, membership.id),
            {"workspace_member": str(workspace_member_of(second_user).id)},
            format="json",
        )

        # plain_user still holds the membership, so their access is legitimate;
        # second_user was never granted anything by this unit.
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()
        assert not ProjectMember.objects.filter(project=project, member=second_user, is_active=True).exists()

    def test_role_remains_patchable(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        # Locking identity must not lock the attributes the endpoint is for.
        link_project(unit, project)
        membership = add_member(unit, plain_user)

        response = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, membership.id),
            {"role": OrganizationalUnitMemberRole.LEAD},
            format="json",
        )
        membership.refresh_from_db()

        assert response.status_code == 200
        assert membership.role == OrganizationalUnitMemberRole.LEAD

    def test_patching_project_does_not_move_the_link(
        self, admin_client, workspace_with_members, unit, project, second_project, link_project
    ):
        unit_project = link_project(unit, project)

        response = admin_client.patch(
            unit_project_url(workspace_with_members.slug, unit.id, unit_project.id),
            {"project": str(second_project.id)},
            format="json",
        )
        unit_project.refresh_from_db()

        assert response.status_code == 200
        assert unit_project.project_id == project.id

    def test_the_old_project_does_not_keep_orphaned_access(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        second_project,
        link_project,
        add_member,
        plain_user,
    ):
        unit_project = link_project(unit, project)
        add_member(unit, plain_user)
        admin_client.patch(
            unit_project_url(workspace_with_members.slug, unit.id, unit_project.id),
            {"project": str(second_project.id)},
            format="json",
        )

        # The link never moved, so second_project was never granted access.
        assert not ProjectMember.objects.filter(project=second_project, member=plain_user, is_active=True).exists()

    def test_default_role_remains_patchable(self, admin_client, workspace_with_members, unit, project, link_project):
        unit_project = link_project(unit, project, role=ROLE_MEMBER)

        response = admin_client.patch(
            unit_project_url(workspace_with_members.slug, unit.id, unit_project.id),
            {"default_role": ROLE_GUEST},
            format="json",
        )
        unit_project.refresh_from_db()

        assert response.status_code == 200
        assert unit_project.default_role == ROLE_GUEST


@pytest.mark.unit
class TestMemberCreationValidatesRoleAndLead:
    """
    The create endpoint read ``role`` straight off ``request.data`` into
    ``get_or_create``. ``choices`` is only enforced during model validation, so
    an arbitrary string was persisted, and every case that would produce a
    second active lead reached the single-lead partial index as an
    IntegrityError. ``BaseViewSet`` turns that into a generic
    ``400 {"error": "The payload is not valid"}``, which names neither the
    field nor the conflict, and the error still aborts the surrounding
    ``transaction.atomic()`` block — so a bulk add could leave nothing applied
    with no indication of which member caused it.
    """

    def test_an_unknown_role_is_rejected(
        self, admin_client, workspace_with_members, unit, plain_user, workspace_member_of
    ):
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": [str(workspace_member_of(plain_user).id)], "role": "supervisor"},
            format="json",
        )

        assert response.status_code == 400
        assert not OrganizationalUnitMembership.objects.filter(organizational_unit=unit).exists()

    def test_two_leads_in_one_request_are_rejected(
        self, admin_client, workspace_with_members, unit, plain_user, second_user, workspace_member_of
    ):
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {
                "workspace_member_ids": [
                    str(workspace_member_of(plain_user).id),
                    str(workspace_member_of(second_user).id),
                ],
                "role": OrganizationalUnitMemberRole.LEAD,
            },
            format="json",
        )

        assert response.status_code == 400
        # Nothing partially applied: the check runs before the transaction.
        assert not OrganizationalUnitMembership.objects.filter(organizational_unit=unit).exists()

    def test_a_second_lead_is_rejected_when_one_is_already_active(
        self, admin_client, workspace_with_members, unit, plain_user, second_user, add_member, workspace_member_of
    ):
        add_member(unit, plain_user, role=OrganizationalUnitMemberRole.LEAD)

        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {
                "workspace_member_ids": [str(workspace_member_of(second_user).id)],
                "role": OrganizationalUnitMemberRole.LEAD,
            },
            format="json",
        )

        assert response.status_code == 400
        assert (
            OrganizationalUnitMembership.objects.filter(
                organizational_unit=unit, role=OrganizationalUnitMemberRole.LEAD, is_active=True
            ).count()
            == 1
        )

    def test_reviving_a_stored_lead_is_rejected_when_another_lead_is_active(
        self, admin_client, workspace_with_members, unit, plain_user, second_user, add_member, workspace_member_of
    ):
        # second_user's membership is inactive but still stored as `lead`.
        # Reactivating it — even via a request asking for `member` — would
        # resurrect a second active lead, because reactivation preserves the
        # stored role.
        dormant = add_member(unit, second_user, role=OrganizationalUnitMemberRole.LEAD)
        dormant.is_active = False
        dormant.save()
        add_member(unit, plain_user, role=OrganizationalUnitMemberRole.LEAD)

        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": [str(workspace_member_of(second_user).id)], "role": "member"},
            format="json",
        )
        dormant.refresh_from_db()

        assert response.status_code == 400
        assert dormant.is_active is False

    def test_a_single_lead_is_still_accepted(
        self, admin_client, workspace_with_members, unit, plain_user, workspace_member_of
    ):
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {
                "workspace_member_ids": [str(workspace_member_of(plain_user).id)],
                "role": OrganizationalUnitMemberRole.LEAD,
            },
            format="json",
        )

        assert response.status_code == 201
        assert (
            OrganizationalUnitMembership.objects.get(organizational_unit=unit, workspace_member__member=plain_user).role
            == OrganizationalUnitMemberRole.LEAD
        )

    def test_adding_several_plain_members_is_still_accepted(
        self, admin_client, workspace_with_members, unit, plain_user, second_user, workspace_member_of
    ):
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {
                "workspace_member_ids": [
                    str(workspace_member_of(plain_user).id),
                    str(workspace_member_of(second_user).id),
                ]
            },
            format="json",
        )

        assert response.status_code == 201
        assert OrganizationalUnitMembership.objects.filter(organizational_unit=unit, is_active=True).count() == 2

    def test_an_empty_member_list_is_rejected(self, admin_client, workspace_with_members, unit):
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id), {"workspace_member_ids": []}, format="json"
        )

        assert response.status_code == 400

    def test_a_malformed_member_id_is_a_validation_error_not_a_crash(self, admin_client, workspace_with_members, unit):
        # Previously this reached the ORM as a filter value and raised, which
        # DRF surfaces as a 500 rather than a 400.
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": ["not-a-uuid"]},
            format="json",
        )

        assert response.status_code == 400


@pytest.mark.unit
class TestInheritedRoleIsAnAuthorizationFloor:
    """
    The reconciler re-raises a role that has been manually demoted below what
    the unit grants, and leaves manual promotions above it alone. That
    asymmetry is intentional — the unit membership is the authorization of
    record — so it is pinned here rather than left to be read off the code.
    """

    def test_a_manual_demotion_below_the_inherited_role_is_restored(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        from plane.app.services.orca import reconcile_access

        link_project(unit, project, role=ROLE_MEMBER)
        add_member(unit, plain_user)
        reconcile_access(workspace_with_members.id)

        # An admin demotes by hand, then the reconciler runs again.
        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        project_member.role = ROLE_GUEST
        project_member.save()
        reconcile_access(workspace_with_members.id)
        project_member.refresh_from_db()

        assert project_member.role == ROLE_MEMBER

    def test_a_manual_promotion_above_the_inherited_role_is_preserved(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        from plane.app.services.orca import reconcile_access

        link_project(unit, project, role=ROLE_MEMBER)
        add_member(unit, plain_user)
        reconcile_access(workspace_with_members.id)

        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        project_member.role = ROLE_ADMIN
        project_member.save()
        reconcile_access(workspace_with_members.id)
        project_member.refresh_from_db()

        assert project_member.role == ROLE_ADMIN

    def test_removing_the_membership_is_how_access_is_actually_withdrawn(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        from plane.app.services.orca import reconcile_access

        link_project(unit, project, role=ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        reconcile_access(workspace_with_members.id)

        membership.is_active = False
        membership.save()
        reconcile_access(workspace_with_members.id)

        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()


@pytest.mark.unit
class TestFeatureFlagClosesTheLayer:
    """
    ``ORCA_ORG_UNITS_ENABLED`` was defined in settings and read nowhere: the
    routes registered unconditionally and no view consulted it. For a subsystem
    that writes ProjectMember rows, a switch that only documents an intention
    is worse than none — an operator would believe the layer was off.
    """

    def test_reads_are_closed_when_the_layer_is_disabled(self, settings, admin_client, workspace_with_members, unit):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.get(units_url(workspace_with_members.slug))

        # 404, not 403: a disabled feature reads as absent.
        assert response.status_code == 404

    def test_writes_are_closed_when_the_layer_is_disabled(
        self, settings, admin_client, workspace_with_members, unit, plain_user, workspace_member_of
    ):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": [str(workspace_member_of(plain_user).id)]},
            format="json",
        )

        assert response.status_code == 404
        assert not OrganizationalUnitMembership.objects.filter(organizational_unit=unit).exists()

    def test_project_links_are_closed_when_the_layer_is_disabled(
        self, settings, admin_client, workspace_with_members, unit, project
    ):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(project.id), "default_role": ROLE_MEMBER},
            format="json",
        )

        assert response.status_code == 404
        assert not OrganizationalUnitProject.objects.filter(organizational_unit=unit).exists()

    def test_the_issue_routes_are_closed_when_the_layer_is_disabled(
        self, settings, admin_client, workspace_with_members, project_with_admin, unit, make_issue
    ):
        issue = make_issue(project_with_admin)
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 404

    def test_the_reconcile_command_refuses_to_run_when_disabled(
        self, settings, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        # The command is the one entry point that is not an HTTP route, so the
        # switch has to close it too or bulk reconciliation stays available.
        link_project(unit, project)
        add_member(unit, plain_user)
        settings.ORCA_ORG_UNITS_ENABLED = False

        with pytest.raises(CommandError):
            call_command("reconcile_organizational_access", "--workspace", workspace_with_members.slug, "--apply")

        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_config_endpoint_stays_reachable_so_the_ui_can_hide_the_layer(
        self, settings, admin_client, workspace_with_members
    ):
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = admin_client.get(f"/api/orca/workspaces/{workspace_with_members.slug}/config/")

        assert response.status_code == 200
        assert response.data["organizational_units_enabled"] is False

    def test_the_config_endpoint_reports_the_layer_as_on_by_default(self, admin_client, workspace_with_members):
        response = admin_client.get(f"/api/orca/workspaces/{workspace_with_members.slug}/config/")

        assert response.status_code == 200
        assert response.data["organizational_units_enabled"] is True

    def test_the_layer_works_normally_while_enabled(self, settings, admin_client, workspace_with_members, unit):
        settings.ORCA_ORG_UNITS_ENABLED = True

        response = admin_client.get(units_url(workspace_with_members.slug))

        assert response.status_code == 200


@pytest.mark.unit
class TestWorkspaceLabelAndStateWritesAreAdminOnly:
    """
    All four write surfaces of the workspace label and state layers.

    ``WorkSpaceAdminPermission`` admits Admin *and* Member despite its name, so
    every one of these was writable by a plain member. None of them is a local
    edit: workspace labels and states replicate into every subscribed project,
    and deleting a state moves that project's work items onto the default state
    before dropping it. Reads keep the broader rule, so what is pinned here is
    the split — member reads yes, member writes no, admin writes yes — on each
    surface separately, because they are four different permission decisions.
    """

    LABEL_SETTINGS = "/api/orca/workspaces/{slug}/project-labels/settings/"
    LABELS = "/api/orca/workspaces/{slug}/project-labels/"
    STATE_SETTINGS = "/api/orca/workspaces/{slug}/project-states/settings/"
    STATES = "/api/orca/workspaces/{slug}/project-states/"

    # --- surface 1: the label settings PATCH ---------------------------------

    def test_a_member_cannot_toggle_the_label_layer(self, member_client, workspace_with_members):
        url = self.LABEL_SETTINGS.format(slug=workspace_with_members.slug)

        assert member_client.patch(url, {"is_enabled": True}, format="json").status_code == 403

    def test_an_admin_can_toggle_the_label_layer(self, admin_client, workspace_with_members):
        url = self.LABEL_SETTINGS.format(slug=workspace_with_members.slug)

        assert admin_client.patch(url, {"is_enabled": True}, format="json").status_code == 200

    def test_a_member_can_still_read_the_label_settings(self, member_client, workspace_with_members):
        url = self.LABEL_SETTINGS.format(slug=workspace_with_members.slug)

        assert member_client.get(url).status_code == 200

    # --- surface 2: the workspace label viewset ------------------------------

    def test_a_member_cannot_create_a_workspace_label(self, member_client, workspace_with_members):
        url = self.LABELS.format(slug=workspace_with_members.slug)

        assert member_client.post(url, {"name": "Nope"}, format="json").status_code == 403

    def test_a_member_cannot_edit_or_delete_a_workspace_label(
        self, admin_client, member_client, workspace_with_members
    ):
        url = self.LABELS.format(slug=workspace_with_members.slug)
        created = admin_client.post(url, {"name": "Compliance"}, format="json")
        detail = f"{url}{created.data['id']}/"

        assert member_client.patch(detail, {"name": "Renamed"}, format="json").status_code == 403
        assert member_client.delete(detail).status_code == 403

    def test_an_admin_can_create_a_workspace_label(self, admin_client, workspace_with_members):
        url = self.LABELS.format(slug=workspace_with_members.slug)

        assert admin_client.post(url, {"name": "Compliance"}, format="json").status_code == 201

    def test_a_member_can_still_list_workspace_labels(self, member_client, workspace_with_members):
        url = self.LABELS.format(slug=workspace_with_members.slug)

        assert member_client.get(url).status_code == 200

    # --- surface 3: the state settings PATCH ---------------------------------

    def test_a_member_cannot_toggle_the_state_layer(self, member_client, workspace_with_members):
        url = self.STATE_SETTINGS.format(slug=workspace_with_members.slug)

        assert member_client.patch(url, {"is_enabled": True}, format="json").status_code == 403

    def test_an_admin_can_toggle_the_state_layer(self, admin_client, workspace_with_members):
        url = self.STATE_SETTINGS.format(slug=workspace_with_members.slug)

        assert admin_client.patch(url, {"is_enabled": True}, format="json").status_code == 200

    def test_a_member_can_still_read_the_state_settings(self, member_client, workspace_with_members):
        url = self.STATE_SETTINGS.format(slug=workspace_with_members.slug)

        assert member_client.get(url).status_code == 200

    # --- surface 4: the project state viewset --------------------------------

    def test_a_member_cannot_create_a_workspace_state(self, member_client, workspace_with_members):
        url = self.STATES.format(slug=workspace_with_members.slug)

        response = member_client.post(url, {"name": "Blocked", "group": "started", "color": "#FF0000"}, format="json")

        assert response.status_code == 403

    def test_a_member_cannot_edit_or_delete_a_workspace_state(
        self, admin_client, member_client, workspace_with_members
    ):
        # The delete path is the destructive one: it reassigns work items in
        # every subscribed project before dropping the state.
        url = self.STATES.format(slug=workspace_with_members.slug)
        created = admin_client.post(url, {"name": "Blocked", "group": "started", "color": "#FF0000"}, format="json")
        detail = f"{url}{created.data['id']}/"

        assert member_client.patch(detail, {"name": "Renamed"}, format="json").status_code == 403
        assert member_client.delete(detail).status_code == 403

    def test_an_admin_can_create_a_workspace_state(self, admin_client, workspace_with_members):
        url = self.STATES.format(slug=workspace_with_members.slug)

        response = admin_client.post(url, {"name": "Blocked", "group": "started", "color": "#FF0000"}, format="json")

        assert response.status_code == 201

    def test_a_member_can_still_list_workspace_states(self, member_client, workspace_with_members):
        url = self.STATES.format(slug=workspace_with_members.slug)

        assert member_client.get(url).status_code == 200

    def test_a_guest_still_cannot_write(self, guest_client, workspace_with_members):
        # Guests were already refused; the change must not have widened them.
        assert (
            guest_client.post(
                self.LABELS.format(slug=workspace_with_members.slug), {"name": "Nope"}, format="json"
            ).status_code
            == 403
        )
        assert (
            guest_client.post(
                self.STATES.format(slug=workspace_with_members.slug),
                {"name": "Nope", "group": "started", "color": "#FF0000"},
                format="json",
            ).status_code
            == 403
        )


@pytest.mark.unit
class TestFeatureFlagClosesTheBackgroundPaths:
    """
    The kill switch has to reach everything that writes, not just what a person
    can click.

    ``ORCA_ORG_UNITS_ENABLED=0`` closed the HTTP routes and the reconcile
    command, but the hourly directory pass on the Celery beat and the queued
    reconciliation task consulted nothing. An operator who switched the layer
    off — the thing you do first when access looks wrong — would have found it
    still creating unit memberships and writing native ProjectMember rows every
    hour, through the one door nobody was watching.
    """

    def test_the_hourly_directory_pass_writes_nothing_when_disabled(
        self, settings, directory_connection, bound_unit, project, link_project, make_identity, put_in_group, plain_user
    ):
        from plane.bgtasks.organizational_directory_task import resolve_directory_identities

        link_project(bound_unit, project)
        put_in_group(bound_unit, make_identity("plain@plane.so"))
        settings.ORCA_ORG_UNITS_ENABLED = False

        resolve_directory_identities()

        assert not OrganizationalUnitMembership.objects.filter(organizational_unit=bound_unit).exists()
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_hourly_directory_pass_still_runs_while_enabled(
        self, settings, directory_connection, bound_unit, project, link_project, make_identity, put_in_group, plain_user
    ):
        """The control: without it the test above would pass on a broken task."""
        from plane.bgtasks.organizational_directory_task import resolve_directory_identities

        link_project(bound_unit, project)
        put_in_group(bound_unit, make_identity("plain@plane.so"))
        settings.ORCA_ORG_UNITS_ENABLED = True

        resolve_directory_identities()

        assert OrganizationalUnitMembership.objects.filter(organizational_unit=bound_unit).exists()
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_reconciliation_task_that_lands_after_the_switch_writes_nothing(
        self, settings, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """
        The switch is read when the task runs, not when it was queued: a task
        already on the queue when an operator turns the layer off must not land
        afterwards and grant the access they were trying to stop.
        """
        from plane.bgtasks.organizational_unit_task import reconcile_organizational_access

        link_project(unit, project)
        add_member(unit, plain_user)
        settings.ORCA_ORG_UNITS_ENABLED = False

        reconcile_organizational_access(workspace_with_members.id)

        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_reconciliation_task_still_runs_while_enabled(
        self, settings, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        from plane.bgtasks.organizational_unit_task import reconcile_organizational_access

        link_project(unit, project)
        add_member(unit, plain_user)
        settings.ORCA_ORG_UNITS_ENABLED = True

        reconcile_organizational_access(workspace_with_members.id)

        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_reconciler_itself_refuses_to_write_when_disabled(
        self, settings, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """
        Defence in depth: the task, the command and the API mixin each check the
        switch before calling ``reconcile_access``, but that function is the one
        place native ``ProjectMember`` rows get written. A future caller that
        forgets the guard must still be stopped there.
        """
        from plane.app.services.orca import reconcile_access

        link_project(unit, project)
        add_member(unit, plain_user)
        settings.ORCA_ORG_UNITS_ENABLED = False

        changes = reconcile_access(workspace_with_members.id)

        assert changes == []
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_reconciler_still_writes_while_enabled(
        self, settings, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """The control for the test above."""
        from plane.app.services.orca import reconcile_access

        link_project(unit, project)
        add_member(unit, plain_user)
        settings.ORCA_ORG_UNITS_ENABLED = True

        reconcile_access(workspace_with_members.id)

        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_directory_sync_command_refuses_to_run_when_disabled(
        self, settings, workspace_with_members, bound_unit, project, link_project, make_identity, put_in_group
    ):
        link_project(bound_unit, project)
        put_in_group(bound_unit, make_identity("plain@plane.so"))
        settings.ORCA_ORG_UNITS_ENABLED = False

        with pytest.raises(CommandError):
            call_command("sync_organizational_directory", "--workspace", workspace_with_members.slug)

        assert not OrganizationalUnitMembership.objects.filter(organizational_unit=bound_unit).exists()

    def test_the_directory_sync_command_refuses_even_in_report_only_mode(
        self, settings, workspace_with_members, bound_unit
    ):
        """
        Reporting reads the layer's own tables, which the switch says are not
        to be consulted. Refusing both modes keeps one answer to "is the layer
        on", rather than one per flag.
        """
        settings.ORCA_ORG_UNITS_ENABLED = False

        with pytest.raises(CommandError):
            call_command("sync_organizational_directory", "--workspace", workspace_with_members.slug, "--report-only")
