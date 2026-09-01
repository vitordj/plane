# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
HTTP contract tests for the directory connection administration API.

These endpoints mint the credential that lets a machine grant project access
through areas, so the tests focus on who may call them and on the credential's
handling — issued once, stored only as a digest, and never readable again.
"""

import pytest

from plane.db.models import OrganizationalDirectoryConnection, hash_directory_token

from .conftest import (
    directory_resync_url,
    directory_token_url,
    directory_unresolved_url,
    directory_url,
)


@pytest.mark.unit
class TestDirectoryConnectionPermissions:
    @pytest.mark.parametrize("client_fixture", ["member_client", "guest_client"])
    def test_only_admins_may_read_the_connection(self, request, client_fixture, workspace_with_members):
        client = request.getfixturevalue(client_fixture)

        response = client.get(directory_url(workspace_with_members.slug))

        assert response.status_code == 403

    def test_only_admins_may_issue_a_token(self, member_client, workspace_with_members):
        response = member_client.post(directory_token_url(workspace_with_members.slug))

        assert response.status_code == 403
        assert not OrganizationalDirectoryConnection.objects.filter(workspace=workspace_with_members).exists()

    def test_someone_outside_the_workspace_cannot_read_it(self, outsider_client, workspace_with_members):
        response = outsider_client.get(directory_url(workspace_with_members.slug))

        assert response.status_code in (401, 403, 404)


@pytest.mark.unit
class TestDirectoryConnection:
    def test_reading_creates_a_disabled_connection_to_render(self, admin_client, workspace_with_members):
        """The settings screen needs something to show before anything is set up."""
        response = admin_client.get(directory_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert response.data["is_enabled"] is False
        assert response.data["has_token"] is False
        assert response.data["scim_base_url"].endswith(f"/api/orca/scim/v2/workspaces/{workspace_with_members.slug}")

    def test_the_token_is_never_serialized_back(self, admin_client, workspace_with_members, directory_connection):
        response = admin_client.get(directory_url(workspace_with_members.slug))

        assert "token" not in response.data
        assert "token_hash" not in response.data
        assert response.data["has_token"] is True

    def test_issuing_returns_the_token_once_and_stores_only_its_digest(self, admin_client, workspace_with_members):
        response = admin_client.post(directory_token_url(workspace_with_members.slug))

        assert response.status_code == 201
        token = response.data["token"]
        connection = OrganizationalDirectoryConnection.objects.get(workspace=workspace_with_members)
        assert connection.token_hash == hash_directory_token(token)
        assert token not in connection.token_hash
        assert connection.token_prefix == token[:8]

    def test_rotating_invalidates_the_previous_token(
        self, admin_client, scim_client, workspace_with_members, directory_connection
    ):
        admin_client.post(directory_token_url(workspace_with_members.slug))

        # scim_client still carries the token issued by the fixture.
        response = scim_client.get(f"/api/orca/scim/v2/workspaces/{workspace_with_members.slug}/Users")

        assert response.status_code == 401

    def test_enabling_without_a_token_is_refused(self, admin_client, workspace_with_members):
        """
        Otherwise the screen would report provisioning as on while every SCIM
        call failed authentication — the worst state to debug from Entra.
        """
        response = admin_client.patch(directory_url(workspace_with_members.slug), {"is_enabled": True}, format="json")

        assert response.status_code == 400

    def test_revoking_also_switches_provisioning_off(self, admin_client, workspace_with_members, directory_connection):
        response = admin_client.delete(directory_token_url(workspace_with_members.slug))

        assert response.status_code == 204
        directory_connection.refresh_from_db()
        assert directory_connection.token_hash == ""
        assert directory_connection.is_enabled is False

    def test_server_owned_fields_cannot_be_set_from_the_request(
        self, admin_client, workspace_with_members, directory_connection
    ):
        forged = hash_directory_token("forged-token")

        admin_client.patch(
            directory_url(workspace_with_members.slug),
            {"tenant_id": "new-tenant", "token_hash": forged, "token_prefix": "forged"},
            format="json",
        )

        directory_connection.refresh_from_db()
        assert directory_connection.tenant_id == "new-tenant"
        assert directory_connection.token_hash != forged


@pytest.mark.unit
class TestDirectoryResyncAndReport:
    def test_resync_materializes_memberships_from_the_stored_mirror(
        self,
        admin_client,
        workspace_with_members,
        directory_connection,
        bound_unit,
        project,
        link_project,
        make_identity,
        put_in_group,
        plain_user,
    ):
        from plane.db.models import ProjectMember

        link_project(bound_unit, project)
        put_in_group(bound_unit, make_identity("plain@plane.so"))

        response = admin_client.post(directory_resync_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert response.data["memberships_created"] == 1
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_report_lists_only_identities_that_granted_nothing(
        self, admin_client, workspace_with_members, directory_connection, make_identity
    ):
        from plane.app.services.orca import resolve_identity

        resolve_identity(make_identity("plain@plane.so"))
        make_identity("nobody@plane.so")

        response = admin_client.get(directory_unresolved_url(workspace_with_members.slug))

        assert response.status_code == 200
        assert [row["user_name"] for row in response.data] == ["nobody@plane.so"]
