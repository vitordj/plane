# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers for the Orca organizational layer (see FORK.md)."""

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
)
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole

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
        # workspace_member is the membership's identity, not an editable
        # attribute. A PATCH that re-points it at another person would
        # reconcile only the new person, leaving the previous one holding the
        # ProjectMember rows this membership had granted them. Swapping people
        # goes through DELETE + POST, which withdraws before it grants.
        read_only_fields = ["organizational_unit", "workspace_member", "created_at"]


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
        # Same reasoning as the membership above: re-pointing `project` would
        # reconcile the new project only and strand the inherited access on the
        # old one. Only default_role is editable in place.
        read_only_fields = ["organizational_unit", "project", "created_at"]


class OrganizationalUnitMembershipCreateSerializer(serializers.Serializer):
    """
    Input serializer for adding people to a unit.

    The write path is ``get_or_create`` plus a reactivation, not a
    ``ModelSerializer.save()``, so nothing on that path validates the payload:
    ``choices`` is only checked during model validation, and assigning a field
    and saving skips it. Without this serializer an arbitrary ``role`` string
    is persisted, and every second active lead surfaces as an
    ``IntegrityError`` from the single-lead partial index. ``BaseViewSet``
    reports that as a generic ``400 {"error": "The payload is not valid"}``
    which names neither the field nor the conflict, and it still aborts the
    surrounding ``transaction.atomic()`` block, so a bulk add applies nothing.

    The lead rules are checked against the unit before the transaction opens,
    counting both the leads the request sets directly and the ones it would
    resurrect by reactivating a membership stored as ``lead``.
    """

    workspace_member_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        error_messages={"empty": "At least one workspace member is required"},
    )
    role = serializers.ChoiceField(
        choices=OrganizationalUnitMemberRole.choices,
        default=OrganizationalUnitMemberRole.MEMBER,
    )

    def validate(self, attrs):
        unit = self.context["organizational_unit"]
        # Deduplicate but keep order, so the count check in the view compares
        # like with like and a repeated id cannot inflate the lead count.
        member_ids = list(dict.fromkeys(attrs["workspace_member_ids"]))
        attrs["workspace_member_ids"] = member_ids
        role = attrs["role"]

        # Leads this request would leave active: the ones it sets outright,
        # plus the ones it revives by reactivating a membership whose stored
        # role is already ``lead``.
        lead_ids = set(member_ids) if role == OrganizationalUnitMemberRole.LEAD else set()
        lead_ids |= set(
            OrganizationalUnitMembership.objects.filter(
                organizational_unit=unit,
                workspace_member_id__in=member_ids,
                role=OrganizationalUnitMemberRole.LEAD,
                is_active=False,
            ).values_list("workspace_member_id", flat=True)
        )

        if len(lead_ids) > 1:
            raise serializers.ValidationError(
                {"role": "An organizational unit can have only one lead; add leads one at a time."}
            )

        # A lead already in this request is not a conflict with itself.
        if lead_ids and (
            OrganizationalUnitMembership.objects.filter(
                organizational_unit=unit,
                role=OrganizationalUnitMemberRole.LEAD,
                is_active=True,
            )
            .exclude(workspace_member_id__in=member_ids)
            .exists()
        ):
            raise serializers.ValidationError({"role": "This organizational unit already has an active lead"})

        return attrs
