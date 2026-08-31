# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
HTTP contract tests for every method the organizational-unit API exposes.

The reconciler is tested directly elsewhere. These tests exercise the layer
above it — permissions, workspace isolation, payload validation, and the write
ordering inside ``destroy`` — because that is where an authorization feature
fails in ways unit tests of the service layer cannot see.
"""

import pytest

from plane.db.models import (
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    ProjectMember,
)

from .conftest import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_MEMBER,
    effective_access_url,
    member_url,
    members_url,
    unit_project_url,
    unit_projects_url,
    unit_url,
    units_url,
    workload_url,
)


@pytest.mark.unit
class TestOrganizationalUnitCrud:
    def test_retrieve_returns_the_unit_with_counts(
        self, member_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project)
        add_member(unit, plain_user)

        response = member_client.get(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 200
        assert response.data["slug"] == "compliance"
        assert response.data["member_count"] == 1
        assert response.data["project_count"] == 1

    def test_retrieve_of_a_unit_from_another_workspace_is_not_found(
        self, admin_client, workspace_with_members, foreign_unit
    ):
        """A unit id from another tenant must not resolve through this slug."""
        response = admin_client.get(unit_url(workspace_with_members.slug, foreign_unit.id))

        assert response.status_code == 404

    def test_outsider_cannot_read_units_of_a_workspace_they_do_not_belong_to(
        self, outsider_client, workspace_with_members, unit
    ):
        response = outsider_client.get(units_url(workspace_with_members.slug))

        assert response.status_code == 403

    def test_admin_updates_name_and_description(self, admin_client, workspace_with_members, unit):
        response = admin_client.patch(
            unit_url(workspace_with_members.slug, unit.id),
            {"name": "Compliance & Risk", "description": "Second line of defence"},
            format="json",
        )

        assert response.status_code == 200
        unit.refresh_from_db()
        assert unit.name == "Compliance & Risk"
        assert unit.description == "Second line of defence"

    def test_non_admin_cannot_update_a_unit(self, member_client, workspace_with_members, unit):
        response = member_client.patch(
            unit_url(workspace_with_members.slug, unit.id), {"name": "Hijacked"}, format="json"
        )

        assert response.status_code == 403
        unit.refresh_from_db()
        assert unit.name == "Compliance"

    def test_updating_an_unknown_unit_is_not_found(self, admin_client, workspace_with_members, foreign_unit):
        response = admin_client.patch(
            unit_url(workspace_with_members.slug, foreign_unit.id), {"name": "Nope"}, format="json"
        )

        assert response.status_code == 404

    def test_updating_to_a_taken_slug_is_rejected(self, admin_client, workspace_with_members, unit, second_unit):
        """Slug collisions must conflict cleanly, exactly as create does."""
        response = admin_client.patch(
            unit_url(workspace_with_members.slug, second_unit.id), {"slug": "compliance"}, format="json"
        )

        assert response.status_code == 409
        second_unit.refresh_from_db()
        assert second_unit.slug == "legal"

    def test_deactivating_a_unit_withdraws_the_access_it_sourced(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project)
        add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)
        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is True

        response = admin_client.patch(
            unit_url(workspace_with_members.slug, unit.id), {"is_active": False}, format="json"
        )

        assert response.status_code == 200
        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is False

    def test_deleting_a_unit_withdraws_inherited_access(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project)
        add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

        response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 204
        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is False

    def test_deleting_a_unit_preserves_manual_access(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        grant_manual_access,
        plain_user,
    ):
        """Access an admin granted by hand outlives the unit that overlapped it."""
        grant_manual_access(project, plain_user, ROLE_MEMBER)
        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 204
        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.is_active is True
        assert project_member.role == ROLE_MEMBER

    def test_deleting_an_unknown_unit_is_not_found(self, admin_client, workspace_with_members, foreign_unit):
        response = admin_client.delete(unit_url(workspace_with_members.slug, foreign_unit.id))

        assert response.status_code == 404

    def test_non_admin_cannot_delete_a_unit(self, member_client, workspace_with_members, unit):
        response = member_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 403
        assert OrganizationalUnit.objects.filter(pk=unit.id).exists()

    def test_delete_rolls_back_entirely_when_reconciliation_fails(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        monkeypatch,
    ):
        """
        A failed withdrawal must not leave the unit deleted and the access
        orphaned; the whole request has to roll back.
        """
        link_project(unit, project)
        add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        def explode(*args, **kwargs):
            raise RuntimeError("reconciler down")

        monkeypatch.setattr("plane.app.views.organizational_unit.reconcile_unit", explode)

        response = admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 500
        unit.refresh_from_db()
        assert unit.is_active is True
        assert OrganizationalUnit.objects.filter(pk=unit.id).exists()
        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is True


@pytest.mark.unit
class TestOrganizationalUnitMembers:
    def test_listing_members_returns_the_roster(
        self, member_client, workspace_with_members, unit, add_member, plain_user
    ):
        add_member(unit, plain_user, role="lead")

        response = member_client.get(members_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["role"] == "lead"
        assert response.data[0]["email"] == "plain@plane.so"

    def test_non_admin_cannot_add_members(
        self, member_client, workspace_with_members, unit, workspace_member_of, second_user
    ):
        response = member_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": [str(workspace_member_of(second_user).id)]},
            format="json",
        )

        assert response.status_code == 403
        assert OrganizationalUnitMembership.objects.count() == 0

    def test_adding_without_members_is_rejected(self, admin_client, workspace_with_members, unit):
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id), {"workspace_member_ids": []}, format="json"
        )

        assert response.status_code == 400

    def test_adding_a_malformed_member_id_is_a_validation_error(self, admin_client, workspace_with_members, unit):
        """A bad uuid in the payload must not surface as a server error."""
        response = admin_client.post(
            members_url(workspace_with_members.slug, unit.id),
            {"workspace_member_ids": ["not-a-uuid"]},
            format="json",
        )

        assert response.status_code == 400

    def test_promoting_a_member_to_lead(self, admin_client, workspace_with_members, unit, add_member, plain_user):
        membership = add_member(unit, plain_user)

        response = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, membership.id), {"role": "lead"}, format="json"
        )

        assert response.status_code == 200
        membership.refresh_from_db()
        assert membership.role == "lead"

    def test_a_second_lead_is_rejected(
        self, admin_client, workspace_with_members, unit, add_member, plain_user, second_user
    ):
        """One active lead per unit is a database constraint; the API must
        surface it as a validation error rather than a 500."""
        add_member(unit, plain_user, role="lead")
        second = add_member(unit, second_user)

        response = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, second.id), {"role": "lead"}, format="json"
        )

        assert response.status_code == 400
        second.refresh_from_db()
        assert second.role == "member"

    def test_a_lead_inherits_the_same_role_as_a_member(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """Leadership is organizational, not a privilege escalation."""
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)

        admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, membership.id), {"role": "lead"}, format="json"
        )

        assert ProjectMember.objects.get(project=project, member=plain_user).role == ROLE_MEMBER

    def test_a_membership_cannot_be_repointed_to_another_workspace(
        self, admin_client, workspace_with_members, other_workspace, unit, add_member, plain_user, outsider_user
    ):
        from plane.db.models import WorkspaceMember

        membership = add_member(unit, plain_user)
        outsider_member = WorkspaceMember.objects.get(workspace=other_workspace, member=outsider_user)

        response = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, membership.id),
            {"workspace_member": str(outsider_member.id)},
            format="json",
        )

        assert response.status_code == 400
        membership.refresh_from_db()
        assert membership.workspace_member.member_id == plain_user.id

    def test_updating_an_unknown_membership_is_not_found(self, admin_client, workspace_with_members, unit):
        import uuid

        response = admin_client.patch(
            member_url(workspace_with_members.slug, unit.id, uuid.uuid4()), {"role": "lead"}, format="json"
        )

        assert response.status_code == 404

    def test_removing_a_member_withdraws_layer_created_access(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project)
        membership = add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

        response = admin_client.delete(member_url(workspace_with_members.slug, unit.id, membership.id))

        assert response.status_code == 204
        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is False

    def test_removing_a_member_preserves_manual_access(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        grant_manual_access,
        plain_user,
    ):
        grant_manual_access(project, plain_user, ROLE_MEMBER)
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        response = admin_client.delete(member_url(workspace_with_members.slug, unit.id, membership.id))

        assert response.status_code == 204
        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.is_active is True

    def test_removing_a_member_restores_the_role_they_had_before(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        grant_manual_access,
        plain_user,
    ):
        """A unit that elevated someone must hand the old role back, not keep it."""
        grant_manual_access(project, plain_user, ROLE_GUEST)
        link_project(unit, project, ROLE_ADMIN)
        membership = add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)
        assert ProjectMember.objects.get(project=project, member=plain_user).role == ROLE_ADMIN

        admin_client.delete(member_url(workspace_with_members.slug, unit.id, membership.id))

        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.is_active is True
        assert project_member.role == ROLE_GUEST

    def test_a_manual_promotion_survives_removal_from_the_unit(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
    ):
        """Drift makes the access manual; the layer must relinquish its claim."""
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        project_member.role = ROLE_ADMIN
        project_member.save()

        admin_client.delete(member_url(workspace_with_members.slug, unit.id, membership.id))

        project_member.refresh_from_db()
        assert project_member.is_active is True
        assert project_member.role == ROLE_ADMIN


@pytest.mark.unit
class TestOrganizationalUnitProjects:
    def test_listing_linked_projects(self, member_client, workspace_with_members, unit, project, link_project):
        link_project(unit, project, ROLE_MEMBER)

        response = member_client.get(unit_projects_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["project_name"] == "Onboarding"
        assert response.data[0]["default_role"] == ROLE_MEMBER

    def test_linking_a_project_materializes_memberships(
        self, admin_client, workspace_with_members, unit, project, add_member, plain_user
    ):
        add_member(unit, plain_user)

        response = admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(project.id), "default_role": ROLE_MEMBER},
            format="json",
        )

        assert response.status_code == 201
        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.role == ROLE_MEMBER
        assert project_member.is_active is True

    def test_non_admin_cannot_link_a_project(self, member_client, workspace_with_members, unit, project):
        response = member_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(project.id)},
            format="json",
        )

        assert response.status_code == 403
        assert OrganizationalUnitProject.objects.count() == 0

    def test_linking_a_project_from_another_workspace_is_rejected(
        self, admin_client, workspace_with_members, unit, foreign_project
    ):
        response = admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(foreign_project.id)},
            format="json",
        )

        assert response.status_code == 400
        assert OrganizationalUnitProject.objects.count() == 0

    def test_linking_without_a_project_is_rejected(self, admin_client, workspace_with_members, unit):
        response = admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id), {"default_role": ROLE_MEMBER}, format="json"
        )

        assert response.status_code == 400

    def test_linking_with_an_unknown_role_is_rejected(self, admin_client, workspace_with_members, unit, project):
        response = admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(project.id), "default_role": 99},
            format="json",
        )

        assert response.status_code == 400

    def test_linking_with_a_non_numeric_role_is_a_validation_error(
        self, admin_client, workspace_with_members, unit, project
    ):
        """``default_role`` is cast to int; a junk value must not raise."""
        response = admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(project.id), "default_role": "admin"},
            format="json",
        )

        assert response.status_code == 400

    def test_relinking_the_same_project_updates_the_role(
        self, admin_client, workspace_with_members, unit, project, add_member, plain_user
    ):
        add_member(unit, plain_user)
        admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(project.id), "default_role": ROLE_MEMBER},
            format="json",
        )

        response = admin_client.post(
            unit_projects_url(workspace_with_members.slug, unit.id),
            {"project_id": str(project.id), "default_role": ROLE_ADMIN},
            format="json",
        )

        assert response.status_code == 200
        assert ProjectMember.objects.get(project=project, member=plain_user).role == ROLE_ADMIN

    def test_raising_the_inherited_role_promotes_members(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        unit_project = link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        response = admin_client.patch(
            unit_project_url(workspace_with_members.slug, unit.id, unit_project.id),
            {"default_role": ROLE_ADMIN},
            format="json",
        )

        assert response.status_code == 200
        assert ProjectMember.objects.get(project=project, member=plain_user).role == ROLE_ADMIN

    def test_lowering_the_inherited_role_leaves_manual_promotions_alone(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user, second_user
    ):
        """Only access the layer still controls may be lowered."""
        unit_project = link_project(unit, project, ROLE_ADMIN)
        add_member(unit, plain_user)
        add_member(unit, second_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        drifted = ProjectMember.objects.get(project=project, member=second_user)
        drifted.role = ROLE_MEMBER
        drifted.save()

        admin_client.patch(
            unit_project_url(workspace_with_members.slug, unit.id, unit_project.id),
            {"default_role": ROLE_GUEST},
            format="json",
        )

        assert ProjectMember.objects.get(project=project, member=plain_user).role == ROLE_GUEST
        assert ProjectMember.objects.get(project=project, member=second_user).role == ROLE_MEMBER

    def test_a_link_cannot_be_repointed_to_another_workspace_project(
        self, admin_client, workspace_with_members, unit, project, link_project, foreign_project
    ):
        unit_project = link_project(unit, project, ROLE_MEMBER)

        response = admin_client.patch(
            unit_project_url(workspace_with_members.slug, unit.id, unit_project.id),
            {"project": str(foreign_project.id)},
            format="json",
        )

        assert response.status_code == 400
        unit_project.refresh_from_db()
        assert unit_project.project_id == project.id

    def test_unlinking_withdraws_the_access_it_sourced(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        unit_project = link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        response = admin_client.delete(unit_project_url(workspace_with_members.slug, unit.id, unit_project.id))

        assert response.status_code == 204
        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is False

    def test_unlinking_keeps_access_another_unit_still_justifies(
        self,
        admin_client,
        workspace_with_members,
        unit,
        second_unit,
        project,
        link_project,
        add_member,
        plain_user,
    ):
        unit_project = link_project(unit, project, ROLE_MEMBER)
        link_project(second_unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        add_member(second_unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)
        reconcile_unit(second_unit, force_sync=True)

        response = admin_client.delete(unit_project_url(workspace_with_members.slug, unit.id, unit_project.id))

        assert response.status_code == 204
        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.is_active is True
        assert project_member.role == ROLE_MEMBER

    def test_unlinking_an_unknown_link_is_not_found(self, admin_client, workspace_with_members, unit):
        import uuid

        response = admin_client.delete(unit_project_url(workspace_with_members.slug, unit.id, uuid.uuid4()))

        assert response.status_code == 404


@pytest.mark.unit
class TestEffectiveAccessAndWorkload:
    def test_effective_access_never_writes(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project)
        add_member(unit, plain_user)

        admin_client.get(effective_access_url(workspace_with_members.slug, unit.id))

        assert not ProjectMember.objects.filter(project=project, member=plain_user).exists()

    def test_effective_access_reports_current_and_desired_roles(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        grant_manual_access,
        plain_user,
    ):
        grant_manual_access(project, plain_user, ROLE_GUEST)
        link_project(unit, project, ROLE_ADMIN)
        add_member(unit, plain_user)

        response = admin_client.get(effective_access_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 200
        change = response.data["changes"][0]
        assert change["current_role"] == ROLE_GUEST
        assert change["desired_role"] == ROLE_ADMIN
        assert change["sources"]

    def test_effective_access_resolves_the_strongest_role_across_units(
        self,
        admin_client,
        workspace_with_members,
        unit,
        second_unit,
        project,
        link_project,
        add_member,
        plain_user,
    ):
        link_project(unit, project, ROLE_GUEST)
        link_project(second_unit, project, ROLE_ADMIN)
        add_member(unit, plain_user)
        add_member(second_unit, plain_user)

        response = admin_client.get(effective_access_url(workspace_with_members.slug, unit.id))

        change = response.data["changes"][0]
        assert change["desired_role"] == ROLE_ADMIN
        assert len(change["sources"]) == 2

    def test_effective_access_is_empty_without_members_or_projects(self, admin_client, workspace_with_members, unit):
        response = admin_client.get(effective_access_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 200
        assert response.data["changes"] == []

    def test_effective_access_of_an_unknown_unit_is_not_found(self, admin_client, workspace_with_members, foreign_unit):
        response = admin_client.get(effective_access_url(workspace_with_members.slug, foreign_unit.id))

        assert response.status_code == 404

    def test_workload_reports_open_work_per_member(
        self,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        second_user,
        make_issue,
    ):
        from plane.db.models import IssueAssignee

        link_project(unit, project)
        add_member(unit, plain_user)
        add_member(unit, second_user)
        issue = make_issue(project)
        IssueAssignee.objects.create(
            issue=issue, assignee=plain_user, project=project, workspace=workspace_with_members
        )

        response = admin_client.get(workload_url(workspace_with_members.slug, unit.id))

        assert response.status_code == 200
        load = {row["display_name"]: row["open_issues"] for row in response.data}
        assert load[plain_user.display_name] == 1
        assert load[second_user.display_name] == 0

    def test_workload_of_an_unknown_unit_is_not_found(self, admin_client, workspace_with_members, foreign_unit):
        response = admin_client.get(workload_url(workspace_with_members.slug, foreign_unit.id))

        assert response.status_code == 404

    def test_workload_never_writes(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project)
        add_member(unit, plain_user)

        admin_client.get(workload_url(workspace_with_members.slug, unit.id))

        assert not ProjectMember.objects.filter(project=project, member=plain_user).exists()


@pytest.mark.unit
class TestDeletionPreservesAudit:
    """
    ``DELETE`` must not destroy the record of who had access and why.

    Plane's own ``SoftDeleteModel`` stamps ``deleted_at`` instead of removing
    the row, so the organizational layer inherits archival semantics for free.
    These tests pin that, because an authorization layer that forgets its own
    history cannot answer an incident question later.
    """

    def test_deleting_a_unit_archives_it_rather_than_erasing_it(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        from plane.db.models import OrganizationalUnit as Unit

        link_project(unit, project)
        add_member(unit, plain_user)
        unit_id = unit.id

        admin_client.delete(unit_url(workspace_with_members.slug, unit_id))

        assert not Unit.objects.filter(pk=unit_id).exists()
        archived = Unit.all_objects.get(pk=unit_id)
        assert archived.deleted_at is not None
        assert archived.name == "Compliance"

    def test_the_provenance_ledger_survives_a_deleted_unit(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        from plane.db.models import OrganizationalUnitGrant

        link_project(unit, project)
        add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)
        assert OrganizationalUnitGrant.objects.filter(organizational_unit=unit).exists()

        admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        # The grant rows remain readable through ``all_objects``, so the answer
        # to "why did this person once have access?" is still recoverable.
        assert OrganizationalUnitGrant.all_objects.filter(organizational_unit_id=unit.id).exists()

    def test_the_aggregate_access_state_outlives_the_unit(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """The per-(person, project) state is not owned by any unit, so it must
        still be there to explain the access that remains."""
        from plane.db.models import OrganizationalProjectAccessState

        link_project(unit, project)
        membership = add_member(unit, plain_user)
        from plane.app.services.orca import reconcile_unit

        reconcile_unit(unit, force_sync=True)

        admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        state = OrganizationalProjectAccessState.objects.get(
            workspace_member_id=membership.workspace_member_id, project=project
        )
        assert state.last_reconciled_at is not None

    def test_the_slug_is_free_again_after_a_unit_is_deleted(self, admin_client, workspace_with_members, unit):
        """Archival must not permanently reserve a name."""
        admin_client.delete(unit_url(workspace_with_members.slug, unit.id))

        response = admin_client.post(units_url(workspace_with_members.slug), {"name": "Compliance"}, format="json")

        assert response.status_code == 201


@pytest.mark.unit
class TestUnitPayloadShape:
    """
    Every response that returns a unit must carry the same fields.

    A create that omits the annotated counts hands the UI a unit whose
    ``member_count`` is undefined, which renders as a blank where a number
    belongs — caught by driving the real screen, not by any service-level test.
    """

    def test_a_created_unit_carries_its_counts(self, admin_client, workspace_with_members):
        response = admin_client.post(units_url(workspace_with_members.slug), {"name": "Engineering"}, format="json")

        assert response.status_code == 201
        assert response.data["member_count"] == 0
        assert response.data["project_count"] == 0

    def test_an_updated_unit_carries_its_counts(
        self, admin_client, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project)
        add_member(unit, plain_user)

        response = admin_client.patch(
            unit_url(workspace_with_members.slug, unit.id), {"name": "Compliance & Risk"}, format="json"
        )

        assert response.status_code == 200
        assert response.data["member_count"] == 1
        assert response.data["project_count"] == 1
