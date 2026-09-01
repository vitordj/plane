# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
HTTP contract tests for the SCIM 2.0 provisioning service.

The caller here is Microsoft, not a browser, and the contract is RFC 7644
rather than Plane's own conventions — so these tests pin the wire format
(envelopes, status codes, ``Location`` headers) alongside the behaviour, and
exercise the exact payload shapes Entra emits rather than idealized ones.
"""

import pytest

from plane.app.services.orca import project_unit, resolve_identity
from plane.db.models import (
    DirectorySyncSource,
    OrganizationalDirectoryGroupMembership,
    OrganizationalDirectoryIdentity,
    OrganizationalUnit,
    ProjectMember,
)

from .conftest import (
    scim_base,
    scim_group_url,
    scim_groups_url,
    scim_user_url,
    scim_users_url,
)

SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.mark.unit
class TestScimAuthentication:
    def test_a_request_without_a_token_is_rejected(self, client, workspace_with_members, directory_connection):
        response = client.get(scim_users_url(workspace_with_members.slug))

        assert response.status_code == 401
        assert response.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]

    def test_a_wrong_token_is_rejected(self, client, workspace_with_members, directory_connection):
        response = client.get(scim_users_url(workspace_with_members.slug), HTTP_AUTHORIZATION="Bearer not-the-token")

        assert response.status_code == 401

    def test_a_disabled_connection_rejects_a_valid_token(
        self, scim_client, workspace_with_members, directory_connection
    ):
        directory_connection.is_enabled = False
        directory_connection.save()

        response = scim_client.get(scim_users_url(workspace_with_members.slug))

        assert response.status_code == 401

    def test_a_workspace_without_a_connection_answers_401_not_404(
        self, scim_client, other_workspace, directory_connection
    ):
        """
        Answering 404 would let an unauthenticated caller enumerate which
        workspaces exist and which have provisioning configured.
        """
        response = scim_client.get(scim_users_url(other_workspace.slug))

        assert response.status_code == 401

    def test_a_successful_call_records_when_the_token_was_last_used(
        self, scim_client, workspace_with_members, directory_connection
    ):
        assert directory_connection.token_last_used_at is None

        scim_client.get(scim_users_url(workspace_with_members.slug))

        directory_connection.refresh_from_db()
        assert directory_connection.token_last_used_at is not None


@pytest.mark.unit
class TestScimDiscovery:
    def test_the_service_provider_config_declares_what_is_implemented(
        self, scim_client, workspace_with_members, directory_connection
    ):
        response = scim_client.get(f"{scim_base(workspace_with_members.slug)}/ServiceProviderConfig")

        assert response.status_code == 200
        body = response.json()
        assert body["patch"]["supported"] is True
        assert body["bulk"]["supported"] is False
        assert body["authenticationSchemes"][0]["type"] == "oauthbearertoken"

    def test_both_resource_types_are_advertised(self, scim_client, workspace_with_members, directory_connection):
        response = scim_client.get(f"{scim_base(workspace_with_members.slug)}/ResourceTypes")

        assert response.status_code == 200
        assert {resource["id"] for resource in response.json()["Resources"]} == {"User", "Group"}


@pytest.mark.unit
class TestScimUsers:
    def test_creating_a_user_mirrors_it_and_links_a_workspace_member(
        self, scim_client, workspace_with_members, directory_connection, plain_user
    ):
        response = scim_client.post(
            scim_users_url(workspace_with_members.slug),
            {
                "schemas": [SCHEMA_USER],
                "userName": "plain@plane.so",
                "externalId": "entra-user-1",
                "displayName": "Plain Person",
                "active": True,
                "emails": [{"value": "plain@plane.so", "primary": True, "type": "work"}],
            },
            format="json",
        )

        assert response.status_code == 201
        assert response["Location"].endswith(f"/Users/{response.json()['id']}")
        identity = OrganizationalDirectoryIdentity.objects.get(user_name="plain@plane.so")
        assert identity.state == "linked"
        assert identity.external_id == "entra-user-1"

    def test_creating_a_user_nobody_knows_still_succeeds_but_grants_nothing(
        self, scim_client, workspace_with_members, directory_connection
    ):
        """
        Failing here would quarantine the user in Entra. The correct answer is
        to accept the record and report it as unresolved.
        """
        response = scim_client.post(
            scim_users_url(workspace_with_members.slug),
            {"schemas": [SCHEMA_USER], "userName": "nobody@plane.so", "active": True},
            format="json",
        )

        assert response.status_code == 201
        assert OrganizationalDirectoryIdentity.objects.get(user_name="nobody@plane.so").state == "unresolved"

    def test_creating_the_same_user_twice_is_a_uniqueness_conflict(
        self, scim_client, workspace_with_members, directory_connection, make_identity
    ):
        make_identity("plain@plane.so")

        response = scim_client.post(
            scim_users_url(workspace_with_members.slug),
            {"schemas": [SCHEMA_USER], "userName": "plain@plane.so"},
            format="json",
        )

        assert response.status_code == 409
        assert response.json()["scimType"] == "uniqueness"

    def test_a_user_without_a_user_name_is_an_invalid_value(
        self, scim_client, workspace_with_members, directory_connection
    ):
        response = scim_client.post(
            scim_users_url(workspace_with_members.slug), {"schemas": [SCHEMA_USER]}, format="json"
        )

        assert response.status_code == 400
        assert response.json()["scimType"] == "invalidValue"

    def test_listing_supports_the_filter_entra_sends_before_every_create(
        self, scim_client, workspace_with_members, directory_connection, make_identity
    ):
        make_identity("plain@plane.so")
        make_identity("second@plane.so")

        response = scim_client.get(
            scim_users_url(workspace_with_members.slug), {"filter": 'userName eq "plain@plane.so"'}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["totalResults"] == 1
        assert body["Resources"][0]["userName"] == "plain@plane.so"

    def test_a_filter_on_an_unsupported_attribute_is_rejected_rather_than_ignored(
        self, scim_client, workspace_with_members, directory_connection
    ):
        """Silently ignoring a filter would return the wrong resources, not fewer."""
        response = scim_client.get(scim_users_url(workspace_with_members.slug), {"filter": 'department eq "Legal"'})

        assert response.status_code == 400
        assert response.json()["scimType"] == "invalidFilter"

    def test_pagination_is_one_based(self, scim_client, workspace_with_members, directory_connection, make_identity):
        make_identity("a@plane.so")
        make_identity("b@plane.so")

        response = scim_client.get(scim_users_url(workspace_with_members.slug), {"startIndex": 2, "count": 1})

        body = response.json()
        assert body["totalResults"] == 2
        assert body["startIndex"] == 2
        assert [resource["userName"] for resource in body["Resources"]] == ["b@plane.so"]

    def test_deprovisioning_by_patch_withdraws_the_access_the_directory_granted(
        self,
        scim_client,
        workspace_with_members,
        directory_connection,
        bound_unit,
        project,
        link_project,
        make_identity,
        put_in_group,
        plain_user,
    ):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        put_in_group(bound_unit, identity)
        resolve_identity(identity)
        project_unit(bound_unit)
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

        response = scim_client.patch(
            scim_user_url(workspace_with_members.slug, identity.id),
            {"schemas": [SCHEMA_PATCH_OP], "Operations": [{"op": "Replace", "path": "active", "value": False}]},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["active"] is False
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_pathless_patch_carrying_a_partial_resource_is_applied(
        self, scim_client, workspace_with_members, directory_connection, make_identity
    ):
        """Entra sends this shape too, and rejecting it would strand the user."""
        identity = make_identity("plain@plane.so")

        scim_client.patch(
            scim_user_url(workspace_with_members.slug, identity.id),
            {"schemas": [SCHEMA_PATCH_OP], "Operations": [{"op": "replace", "value": {"active": False}}]},
            format="json",
        )

        identity.refresh_from_db()
        assert identity.is_active is False

    def test_an_unknown_patch_path_is_ignored_rather_than_failing_the_request(
        self, scim_client, workspace_with_members, directory_connection, make_identity
    ):
        """
        A tenant mapping an attribute Plane has no column for must not stop the
        attributes that do matter from arriving.
        """
        identity = make_identity("plain@plane.so")

        response = scim_client.patch(
            scim_user_url(workspace_with_members.slug, identity.id),
            {
                "schemas": [SCHEMA_PATCH_OP],
                "Operations": [
                    {"op": "replace", "path": "department", "value": "Legal"},
                    {"op": "replace", "path": "displayName", "value": "Renamed"},
                ],
            },
            format="json",
        )

        assert response.status_code == 200
        identity.refresh_from_db()
        assert identity.display_name == "Renamed"

    def test_deleting_a_user_withdraws_access_and_drops_their_group_entries(
        self,
        scim_client,
        workspace_with_members,
        directory_connection,
        bound_unit,
        project,
        link_project,
        make_identity,
        put_in_group,
        plain_user,
    ):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        put_in_group(bound_unit, identity)
        resolve_identity(identity)
        project_unit(bound_unit)

        response = scim_client.delete(scim_user_url(workspace_with_members.slug, identity.id))

        assert response.status_code == 204
        assert not OrganizationalDirectoryGroupMembership.objects.filter(identity_id=identity.id).exists()
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_user_from_another_workspace_is_not_found(
        self, scim_client, workspace_with_members, directory_connection, other_workspace
    ):
        foreign = OrganizationalDirectoryIdentity.objects.create(
            workspace=other_workspace, user_name="outsider@plane.so", email="outsider@plane.so"
        )

        response = scim_client.get(scim_user_url(workspace_with_members.slug, foreign.id))

        assert response.status_code == 404


@pytest.mark.unit
class TestScimGroups:
    def test_creating_a_group_creates_the_area_and_binds_it(
        self, scim_client, workspace_with_members, directory_connection
    ):
        response = scim_client.post(
            scim_groups_url(workspace_with_members.slug),
            {"schemas": [SCHEMA_GROUP], "displayName": "Finance", "externalId": "entra-group-finance"},
            format="json",
        )

        assert response.status_code == 201
        unit = OrganizationalUnit.objects.get(name="Finance")
        assert unit.external_id == "entra-group-finance"
        assert unit.sync_source == DirectorySyncSource.SCIM

    def test_a_group_adopts_an_area_an_admin_created_with_the_same_name(
        self, scim_client, workspace_with_members, directory_connection, unit
    ):
        """
        The intended workflow: an admin sets up the area and its projects, then
        the directory supplies the people. Creating a duplicate would silently
        strand the projects the admin configured.
        """
        response = scim_client.post(
            scim_groups_url(workspace_with_members.slug),
            {"schemas": [SCHEMA_GROUP], "displayName": "Compliance", "externalId": "entra-group-compliance"},
            format="json",
        )

        assert response.status_code == 201
        assert OrganizationalUnit.objects.filter(name="Compliance").count() == 1
        unit.refresh_from_db()
        assert unit.external_id == "entra-group-compliance"
        # Adoption must not rewrite provenance: an admin still owns this area.
        assert unit.sync_source == DirectorySyncSource.MANUAL

    def test_automatic_creation_can_be_switched_off(self, scim_client, workspace_with_members, directory_connection):
        directory_connection.auto_create_units = False
        directory_connection.save()

        response = scim_client.post(
            scim_groups_url(workspace_with_members.slug),
            {"schemas": [SCHEMA_GROUP], "displayName": "Finance", "externalId": "entra-group-finance"},
            format="json",
        )

        assert response.status_code == 400
        assert not OrganizationalUnit.objects.filter(name="Finance").exists()

    def test_two_groups_cannot_claim_the_same_binding(
        self, scim_client, workspace_with_members, directory_connection, bound_unit
    ):
        response = scim_client.post(
            scim_groups_url(workspace_with_members.slug),
            {"schemas": [SCHEMA_GROUP], "displayName": "Anything", "externalId": bound_unit.external_id},
            format="json",
        )

        assert response.status_code == 409
        assert response.json()["scimType"] == "uniqueness"

    def test_adding_a_member_grants_project_access(
        self,
        scim_client,
        workspace_with_members,
        directory_connection,
        bound_unit,
        project,
        link_project,
        make_identity,
        plain_user,
    ):
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")

        response = scim_client.patch(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {
                "schemas": [SCHEMA_PATCH_OP],
                "Operations": [{"op": "add", "path": "members", "value": [{"value": str(identity.id)}]}],
            },
            format="json",
        )

        assert response.status_code == 200
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_removing_a_member_through_a_filtered_path_works(
        self,
        scim_client,
        workspace_with_members,
        directory_connection,
        bound_unit,
        project,
        link_project,
        make_identity,
        put_in_group,
        plain_user,
    ):
        """
        Entra removes one person with ``members[value eq "..."]`` rather than
        putting the id in ``value``; missing this shape would silently leave
        access in place after somebody leaves the group.
        """
        link_project(bound_unit, project)
        identity = make_identity("plain@plane.so")
        put_in_group(bound_unit, identity)
        resolve_identity(identity)
        project_unit(bound_unit)

        response = scim_client.patch(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {
                "schemas": [SCHEMA_PATCH_OP],
                "Operations": [{"op": "remove", "path": f'members[value eq "{identity.id}"]'}],
            },
            format="json",
        )

        assert response.status_code == 200
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_replacing_the_member_list_removes_everyone_absent_from_it(
        self, scim_client, workspace_with_members, directory_connection, bound_unit, make_identity, put_in_group
    ):
        staying = make_identity("plain@plane.so")
        leaving = make_identity("second@plane.so")
        put_in_group(bound_unit, staying)
        put_in_group(bound_unit, leaving)

        scim_client.put(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {
                "schemas": [SCHEMA_GROUP],
                "displayName": bound_unit.name,
                "members": [{"value": str(staying.id)}],
            },
            format="json",
        )

        remaining = OrganizationalDirectoryGroupMembership.objects.filter(organizational_unit=bound_unit)
        assert [row.identity_id for row in remaining] == [staying.id]

    def test_renaming_a_group_renames_the_area(
        self, scim_client, workspace_with_members, directory_connection, bound_unit
    ):
        scim_client.patch(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {
                "schemas": [SCHEMA_PATCH_OP],
                "Operations": [{"op": "replace", "path": "displayName", "value": "Compliance & Risk"}],
            },
            format="json",
        )

        bound_unit.refresh_from_db()
        assert bound_unit.name == "Compliance & Risk"
        assert bound_unit.slug == "compliance-risk"

    def test_deleting_a_group_unbinds_the_area_and_keeps_manual_members(
        self,
        scim_client,
        workspace_with_members,
        directory_connection,
        bound_unit,
        project,
        link_project,
        add_member,
        make_identity,
        put_in_group,
        plain_user,
        second_user,
    ):
        """
        The area carries project links and hand-picked members an admin owns, so
        removing the group upstream withdraws only what the directory granted.
        """
        link_project(bound_unit, project)
        add_member(bound_unit, second_user)
        identity = make_identity("plain@plane.so")
        put_in_group(bound_unit, identity)
        resolve_identity(identity)
        project_unit(bound_unit)

        response = scim_client.delete(scim_group_url(workspace_with_members.slug, bound_unit.id))

        assert response.status_code == 204
        bound_unit.refresh_from_db()
        assert bound_unit.external_id == ""
        assert OrganizationalUnit.objects.filter(pk=bound_unit.pk).exists()
        assert ProjectMember.objects.filter(project=project, member=second_user, is_active=True).exists()
        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_group_from_another_workspace_is_not_found(
        self, scim_client, workspace_with_members, directory_connection, foreign_unit
    ):
        response = scim_client.get(scim_group_url(workspace_with_members.slug, foreign_unit.id))

        assert response.status_code == 404
