# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Administration API for the workspace's directory connection.

These are the endpoints the Areas settings screen talks to. They configure the
connection Microsoft Entra ID provisions through, mint and revoke its bearer
token, run a repair pass, and serve the report of directory identities that
could not be turned into workspace access.

Everything here is workspace-Admin only: the SCIM token grants a machine the
power to add people to units, which cascades into project access, so issuing
one is an authorization decision of the same weight as editing a unit.

Every endpoint also sits behind the organizational layer's kill switch: a
directory connection exists only to feed units, so ``ORCA_ORG_UNITS_ENABLED=0``
closes it along with the rest of the layer.
"""

# Django imports
from django.db import transaction

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions.base import ROLE, allow_permission
from plane.app.serializers import (
    OrganizationalDirectoryConnectionSerializer,
    OrganizationalDirectoryIdentitySerializer,
)
from plane.app.services.orca import project_workspace, unresolved_identities
from plane.db.models import OrganizationalDirectoryConnection, Workspace

from .base import BaseAPIView
from .organizational_unit import OrganizationalUnitFeatureMixin

# Fields an admin may set. The token, its metadata and the sync counters are
# server-owned, so they are not accepted from the request body.
EDITABLE_CONNECTION_FIELDS = (
    "is_enabled",
    "tenant_id",
    "auto_create_units",
    "deprovision_removes_membership",
)


def get_or_create_connection(workspace_id):
    """
    Fetch the workspace's connection, creating a disabled one on first read.

    @description The settings screen needs something to render before an admin
    has configured anything. A connection created here is disabled and holds no
    token, so it grants nothing until somebody deliberately turns it on.

    @param workspace_id: The workspace.
    @returns: The ``OrganizationalDirectoryConnection``.
    """
    connection, _ = OrganizationalDirectoryConnection.objects.get_or_create(workspace_id=workspace_id)
    return connection


class OrganizationalDirectoryConnectionEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """Read and configure the workspace's directory connection."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        """Return the connection, with the SCIM base URL to paste into Entra."""
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        connection = get_or_create_connection(workspace.id)
        data = OrganizationalDirectoryConnectionSerializer(connection).data
        # Derived from the request rather than stored, so it stays correct
        # behind a proxy or after a hostname change.
        scheme = "https" if request.is_secure() else "http"
        data["scim_base_url"] = f"{scheme}://{request.get_host()}/api/orca/scim/v2/workspaces/{slug}"
        return Response(data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug):
        """
        Update the connection's settings.

        @description Enabling the connection without a token is rejected: it
        would look configured on the settings screen while every SCIM call
        still failed authentication, which is the most confusing possible
        state to debug against a live tenant.
        """
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        connection = get_or_create_connection(workspace.id)
        payload = {key: value for key, value in request.data.items() if key in EDITABLE_CONNECTION_FIELDS}

        if payload.get("is_enabled") and not connection.token_hash:
            return Response(
                {"error": "Issue a SCIM token before enabling directory provisioning"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = OrganizationalDirectoryConnectionSerializer(connection, data=payload, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OrganizationalDirectoryTokenEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """Mint and revoke the SCIM bearer token."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        """
        Issue a new token, invalidating the previous one.

        @description The token is returned in the clear exactly once — only its
        digest is stored — so the response is the single opportunity to copy it
        into the Entra provisioning form. Rotation takes effect immediately,
        which means provisioning fails until Entra is updated.
        """
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        connection = get_or_create_connection(workspace.id)
        with transaction.atomic():
            token = connection.issue_token()
            connection.save()

        data = OrganizationalDirectoryConnectionSerializer(connection).data
        data["token"] = token
        return Response(data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug):
        """
        Revoke the token and switch provisioning off.

        @description Disabling alongside the revocation is deliberate: a
        connection with no credential can never authenticate again, so leaving
        it marked enabled would misreport the workspace's state.
        """
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        connection = get_or_create_connection(workspace.id)
        connection.token_hash = ""
        connection.token_prefix = ""
        connection.token_issued_at = None
        connection.token_last_used_at = None
        connection.is_enabled = False
        connection.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationalDirectoryResyncEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """Re-run the projection for the whole workspace."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        """
        Repair the workspace from the mirror it already holds.

        @description No call is made to the directory: this replays what SCIM
        already pushed. It is the button to press after adding somebody to the
        workspace who the directory had pushed earlier — their identity
        resolves and their memberships appear, without waiting for Entra's next
        provisioning cycle.
        """
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        result = project_workspace(workspace.id)
        connection = get_or_create_connection(workspace.id)
        connection.last_sync_summary = result.as_dict()
        connection.save(update_fields=["last_sync_summary", "updated_at"])
        return Response(result.as_dict(), status=status.HTTP_200_OK)


class OrganizationalDirectoryUnresolvedEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """Report the directory identities that granted nothing."""

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        """
        List identities the directory pushed that are not workspace members.

        @description This is the answer to "why did Entra provision 40 people
        and only 31 got access". A unit never invites anyone, so these are the
        rows an admin has to act on — by inviting the person to the workspace,
        or by removing them from the group upstream.
        """
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND)

        identities = unresolved_identities(workspace.id).select_related("workspace_member", "workspace_member__member")
        serializer = OrganizationalDirectoryIdentitySerializer(identities, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
