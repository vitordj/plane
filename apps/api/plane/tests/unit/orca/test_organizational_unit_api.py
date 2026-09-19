# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Contract tests for the Orca organizational-unit API under ``/api/orca/``.

They pin the two things most likely to regress: the routes resolve where
FORK.md says they should, and mutations stay workspace-admin only while reads
stay open to any workspace member.
"""

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    OrganizationalUnit,
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


@pytest.fixture
def admin_user(db):
    user = User.objects.create(email="admin@plane.so", username="admin", first_name="Admin")
    user.set_password("admin@123")
    user.save()
    return user


@pytest.fixture
def plain_user(db):
    user = User.objects.create(email="plain@plane.so", username="plain", first_name="Plain")
    user.set_password("plain@123")
    user.save()
    return user


@pytest.fixture
def workspace_with_members(db, admin_user, plain_user):
    workspace = Workspace.objects.create(name="Orca", slug="orca-api", owner=admin_user)
    WorkspaceMember.objects.create(workspace=workspace, member=admin_user, role=ROLE_ADMIN)
    WorkspaceMember.objects.create(workspace=workspace, member=plain_user, role=ROLE_MEMBER)
    return workspace


@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def member_client(plain_user):
    client = APIClient()
    client.force_authenticate(user=plain_user)
    return client


def units_url(slug):
    return f"/api/orca/workspaces/{slug}/organizational-units/"


@pytest.mark.unit
class TestOrganizationalUnitAPI:
    def test_admin_creates_a_unit(self, admin_client, workspace_with_members):
        response = admin_client.post(
            units_url(workspace_with_members.slug),
            {"name": "Compliance", "description": "Regulatory area"},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["slug"] == "compliance"
        assert OrganizationalUnit.objects.filter(workspace=workspace_with_members, slug="compliance").exists()

    def test_non_admin_cannot_create_a_unit(self, member_client, workspace_with_members):
        response = member_client.post(units_url(workspace_with_members.slug), {"name": "Comercial"}, format="json")

        assert response.status_code == 403
        assert OrganizationalUnit.objects.count() == 0

    def test_any_workspace_member_can_list_units(self, member_client, workspace_with_members):
        OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Compliance", slug="compliance")

        response = member_client.get(units_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]["name"] == "Compliance"

    def test_duplicate_slug_is_rejected(self, admin_client, workspace_with_members):
        OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Compliance", slug="compliance")

        response = admin_client.post(units_url(workspace_with_members.slug), {"name": "Compliance"}, format="json")

        assert response.status_code == 409

    def test_adding_a_member_materializes_project_access(
        self, admin_client, workspace_with_members, admin_user, plain_user
    ):
        """The end-to-end path: link a project, add a person, get a ProjectMember."""
        unit = OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Compliance", slug="compliance")
        project = Project.objects.create(
            name="Onboarding", identifier="ONB", workspace=workspace_with_members, created_by=admin_user
        )
        OrganizationalUnitProject.objects.create(
            organizational_unit=unit, project=project, workspace=workspace_with_members, default_role=ROLE_MEMBER
        )
        workspace_member = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)

        response = admin_client.post(
            f"{units_url(workspace_with_members.slug)}{unit.id}/members/",
            {"workspace_member_ids": [str(workspace_member.id)]},
            format="json",
        )

        assert response.status_code == 201
        assert OrganizationalUnitMembership.objects.filter(organizational_unit=unit).count() == 1
        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.role == ROLE_MEMBER
        assert project_member.is_active is True

    def test_member_from_another_workspace_is_rejected(self, admin_client, workspace_with_members, admin_user):
        unit = OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Compliance", slug="compliance")
        other_workspace = Workspace.objects.create(name="Other", slug="other-api", owner=admin_user)
        outsider = WorkspaceMember.objects.create(workspace=other_workspace, member=admin_user, role=ROLE_MEMBER)

        response = admin_client.post(
            f"{units_url(workspace_with_members.slug)}{unit.id}/members/",
            {"workspace_member_ids": [str(outsider.id)]},
            format="json",
        )

        assert response.status_code == 400
        assert OrganizationalUnitMembership.objects.count() == 0

    def test_effective_access_is_read_only(self, admin_client, workspace_with_members, admin_user, plain_user):
        unit = OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Compliance", slug="compliance")
        project = Project.objects.create(
            name="Onboarding", identifier="ONB", workspace=workspace_with_members, created_by=admin_user
        )
        OrganizationalUnitProject.objects.create(
            organizational_unit=unit, project=project, workspace=workspace_with_members, default_role=ROLE_MEMBER
        )
        workspace_member = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        OrganizationalUnitMembership.objects.create(
            organizational_unit=unit, workspace_member=workspace_member, workspace=workspace_with_members
        )

        response = admin_client.get(f"{units_url(workspace_with_members.slug)}{unit.id}/effective-access/")

        assert response.status_code == 200
        assert response.data["changes"][0]["action"] == "create"
        assert not ProjectMember.objects.filter(project=project, member=plain_user).exists()

    def test_me_endpoint_lists_own_units(self, member_client, workspace_with_members, plain_user):
        unit = OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Compliance", slug="compliance")
        workspace_member = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        OrganizationalUnitMembership.objects.create(
            organizational_unit=unit, workspace_member=workspace_member, workspace=workspace_with_members
        )

        response = member_client.get(f"{units_url(workspace_with_members.slug)}me/")

        assert response.status_code == 200
        assert response.data[0]["organizational_unit"]["slug"] == "compliance"
        assert response.data[0]["role"] == "member"
