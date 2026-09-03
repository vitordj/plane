# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers for the Orca organizational layer (see FORK.md)."""

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    AssignmentDecision,
    IssueOrganizationalUnit,
    MembershipAllocationSettings,
    OrganizationalDirectoryConnection,
    OrganizationalDirectoryIdentity,
    OrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    WorkspaceMemberAvailability,
)

from plane.app.services.orca.coverage import covered_project_ids
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole

from .base import BaseSerializer


class OrganizationalUnitSerializer(BaseSerializer):
    """Read/write serializer for organizational units."""

    member_count = serializers.IntegerField(read_only=True)
    project_count = serializers.IntegerField(read_only=True)
    project_ids = serializers.SerializerMethodField()

    def get_project_ids(self, obj):
        """
        The projects this area may own work in.

        @description The interface needs it to offer only the areas that cover
        the work item's project — the same rule the API enforces, so the
        dropdown cannot offer something the save will reject. Reads the
        prefetched links when the view provided them (``covered_links``) and
        falls back to a query for single-object responses.
        @param obj: The organizational unit.
        @returns: List of project ids as strings.
        """
        links = getattr(obj, "covered_links", None)
        if links is not None:
            return [str(link.project_id) for link in links]
        return [str(project_id) for project_id in covered_project_ids(obj)]

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
            "project_ids",
            "sync_source",
            "external_id",
            "directory_synced_at",
            "created_at",
            "updated_at",
        ]
        # The directory binding is written by the SCIM endpoints, never by the
        # settings UI: letting an admin retype an external id by hand would let
        # them silently steal another group's binding.
        read_only_fields = [
            "workspace",
            "sync_source",
            "external_id",
            "directory_synced_at",
            "created_at",
            "updated_at",
        ]


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
            "sync_source",
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
        # sync_source records where the row came from (manual or directory) and
        # is what lets a directory sync take back only what it gave, so it is
        # never editable through the API either.
        read_only_fields = ["organizational_unit", "workspace_member", "sync_source", "created_at"]


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


class AssignmentPolicySerializer(BaseSerializer):
    """How an area assigns work, for the settings screen and the policy read."""

    class Meta:
        model = OrganizationalUnitAssignmentPolicy
        fields = [
            "id",
            "organizational_unit",
            "unit_project",
            "default_mode",
            "allowed_modes",
            "assignment_sla_seconds",
            "max_open_items_per_member",
            "is_active",
            "version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["workspace", "version"]


class AssignmentDecisionSerializer(BaseSerializer):
    """
    One decision, as the timeline shows it.

    @description Includes the candidates snapshot: the question a coordinator
    asks about an automatic choice is "why them and not me?", and the answer
    is the ranking that was in front of the service at the time.
    """

    class Meta:
        model = AssignmentDecision
        fields = [
            "id",
            "issue",
            "organizational_unit",
            "trigger",
            "requested_mode",
            "effective_mode",
            "policy_source",
            "policy_version",
            "algorithm_version",
            "outcome",
            "candidates_snapshot",
            "chosen_assignee",
            "previous_primary_executor",
            "decided_by",
            "supersedes",
            "reason",
            "created_at",
        ]
        read_only_fields = fields


class IssueRoutingSerializer(BaseSerializer):
    """Where a work item stands in its area's queue."""

    class Meta:
        model = IssueOrganizationalUnit
        fields = [
            "id",
            "organizational_unit",
            "routing_state",
            "queue_reason",
            "queued_at",
            "assignment_due_at",
            "primary_executor",
            "current_assignment_decision",
        ]
        read_only_fields = fields


class WorkspaceMemberAvailabilitySerializer(BaseSerializer):
    """One stretch of time somebody is away."""

    member_id = serializers.UUIDField(source="workspace_member.member_id", read_only=True)
    display_name = serializers.CharField(source="workspace_member.member.display_name", read_only=True)

    class Meta:
        model = WorkspaceMemberAvailability
        fields = [
            "id",
            "workspace_member",
            "member_id",
            "display_name",
            "unavailable_from",
            "unavailable_until",
            "reason",
            "source",
            "external_id",
            "created_at",
        ]
        # Who the interval is about is the row's identity, and `source` says
        # which writer owns it — a manual edit that relabelled an imported row
        # as manual would make the next import unable to take back what it
        # gave. Both are set by the view, never by the payload.
        read_only_fields = ["workspace_member", "source", "created_at"]


class MembershipAllocationSettingsSerializer(BaseSerializer):
    """How much work one person takes from one area."""

    class Meta:
        model = MembershipAllocationSettings
        fields = [
            "id",
            "membership",
            "accepts_new_work",
            "max_open_items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["membership", "created_at", "updated_at"]
