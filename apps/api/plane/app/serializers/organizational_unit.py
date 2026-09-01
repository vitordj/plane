# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers for the Orca organizational layer (see FORK.md)."""

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    OrganizationalDirectoryConnection,
    OrganizationalDirectoryIdentity,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
)

from .base import BaseSerializer


class OrganizationalUnitSerializer(BaseSerializer):
    """Read/write serializer for organizational units."""

    member_count = serializers.IntegerField(read_only=True)
    project_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrganizationalUnit
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "logo_props",
            "is_active",
            "workspace",
            "member_count",
            "project_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["workspace", "created_at", "updated_at"]


class OrganizationalUnitMembershipSerializer(BaseSerializer):
    """Membership of a workspace member in a unit, with light member details."""

    member_id = serializers.UUIDField(source="workspace_member.member_id", read_only=True)
    display_name = serializers.CharField(source="workspace_member.member.display_name", read_only=True)
    email = serializers.CharField(source="workspace_member.member.email", read_only=True)
    avatar_url = serializers.CharField(source="workspace_member.member.avatar_url", read_only=True)
    workspace_role = serializers.IntegerField(source="workspace_member.role", read_only=True)

    class Meta:
        model = OrganizationalUnitMembership
        fields = [
            "id",
            "organizational_unit",
            "workspace_member",
            "role",
            "is_active",
            "member_id",
            "display_name",
            "email",
            "avatar_url",
            "workspace_role",
            "created_at",
        ]
        read_only_fields = ["organizational_unit", "created_at"]


class OrganizationalUnitProjectSerializer(BaseSerializer):
    """Link between a unit and a project, carrying the inherited project role."""

    project_name = serializers.CharField(source="project.name", read_only=True)
    project_identifier = serializers.CharField(source="project.identifier", read_only=True)

    class Meta:
        model = OrganizationalUnitProject
        fields = [
            "id",
            "organizational_unit",
            "project",
            "default_role",
            "project_name",
            "project_identifier",
            "created_at",
        ]
        read_only_fields = ["organizational_unit", "created_at"]


class OrganizationalDirectoryConnectionSerializer(BaseSerializer):
    """
    Read-only-ish view of a workspace's directory connection.

    The bearer token is never serialized — only whether one exists and the
    short prefix, so the settings screen can show which credential is
    installed without ever being able to reveal it.
    """

    has_token = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationalDirectoryConnection
        fields = [
            "id",
            "provider",
            "is_enabled",
            "tenant_id",
            "auto_create_units",
            "deprovision_removes_membership",
            "token_prefix",
            "token_issued_at",
            "token_last_used_at",
            "last_sync_at",
            "last_sync_summary",
            "has_token",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "provider",
            "token_prefix",
            "token_issued_at",
            "token_last_used_at",
            "last_sync_at",
            "last_sync_summary",
            "created_at",
            "updated_at",
        ]

    def get_has_token(self, obj) -> bool:
        """@returns: Whether a SCIM bearer token is currently installed."""
        return bool(obj.token_hash)


class OrganizationalDirectoryIdentitySerializer(BaseSerializer):
    """A mirrored directory identity, as the unresolved report shows it."""

    workspace_member_display_name = serializers.CharField(
        source="workspace_member.member.display_name", read_only=True, default=None
    )

    class Meta:
        model = OrganizationalDirectoryIdentity
        fields = [
            "id",
            "external_id",
            "user_name",
            "email",
            "display_name",
            "is_active",
            "state",
            "workspace_member",
            "workspace_member_display_name",
            "last_seen_at",
            "created_at",
        ]
        read_only_fields = fields
