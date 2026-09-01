# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the ``reconcile_organizational_access`` management command.

The command is the operator's escape hatch for repairing a workspace whose
inherited access has drifted, so its dry-run has to be genuinely read-only —
an operator who cannot trust the preview will not run the repair.
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from plane.db.models import ProjectMember

from .conftest import ROLE_ADMIN, ROLE_GUEST, ROLE_MEMBER


def run(*args, **options):
    out = StringIO()
    call_command("reconcile_organizational_access", *args, stdout=out, **options)
    return out.getvalue()


@pytest.mark.unit
class TestReconcileCommand:
    def test_an_unknown_workspace_is_an_error(self, db):
        with pytest.raises(CommandError):
            run("--workspace", "does-not-exist")

    def test_the_dry_run_writes_nothing(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)

        output = run("--workspace", workspace_with_members.slug)

        assert "dry-run" in output
        assert "create" in output
        assert not ProjectMember.objects.filter(project=project, member=plain_user).exists()

    def test_apply_materializes_the_previewed_changes(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)

        output = run("--workspace", workspace_with_members.slug, "--apply")

        assert "Applied" in output
        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.role == ROLE_MEMBER
        assert project_member.is_active is True

    def test_a_second_apply_reports_nothing_left_to_do(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """Idempotency is what makes the command safe to schedule."""
        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        run("--workspace", workspace_with_members.slug, "--apply")

        output = run("--workspace", workspace_with_members.slug, "--apply")

        assert "Applied 0 change(s)" in output
        assert ProjectMember.objects.filter(project=project, member=plain_user).count() == 1

    def test_the_command_repairs_access_that_drifted_out_of_band(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """A ProjectMember deactivated directly in the database comes back."""
        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        run("--workspace", workspace_with_members.slug, "--apply")

        ProjectMember.objects.filter(project=project, member=plain_user).update(is_active=False)

        run("--workspace", workspace_with_members.slug, "--apply")

        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is True

    def test_the_command_respects_the_workspace_role_ceiling(
        self, workspace_with_members, unit, project, link_project, add_member, guest_user
    ):
        """A workspace guest is never elevated, however strong the unit link."""
        link_project(unit, project, ROLE_ADMIN)
        add_member(unit, guest_user)

        run("--workspace", workspace_with_members.slug, "--apply")

        assert ProjectMember.objects.get(project=project, member=guest_user).role == ROLE_GUEST

    def test_the_command_is_scoped_to_one_workspace(
        self,
        workspace_with_members,
        other_workspace,
        foreign_unit,
        foreign_project,
        outsider_user,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
    ):
        from plane.db.models import OrganizationalUnitMembership, OrganizationalUnitProject, WorkspaceMember

        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        OrganizationalUnitProject.objects.create(
            organizational_unit=foreign_unit,
            project=foreign_project,
            workspace=other_workspace,
            default_role=ROLE_MEMBER,
        )
        OrganizationalUnitMembership.objects.create(
            organizational_unit=foreign_unit,
            workspace_member=WorkspaceMember.objects.get(workspace=other_workspace, member=outsider_user),
            workspace=other_workspace,
        )

        run("--workspace", workspace_with_members.slug, "--apply")

        assert ProjectMember.objects.filter(project=project, member=plain_user).exists()
        assert not ProjectMember.objects.filter(project=foreign_project).exists()
