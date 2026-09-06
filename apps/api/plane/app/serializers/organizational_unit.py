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
    OrganizationalUnitCoordinator,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    WorkspaceMemberAvailability,
)
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole

from .base import BaseSerializer


class OrganizationalUnitSerializer(BaseSerializer):
    """Read/write serializer for organizational units."""

    member_count = serializers.IntegerField(read_only=True)
    project_count = serializers.IntegerField(read_only=True)
    project_ids = serializers.SerializerMethodField()

    # Filled by the list endpoint's Prefetch; absent when a single unit is
    # serialized on its own.
    COVERED_PROJECTS_ATTR = "covered_projects"

    def get_project_ids(self, obj) -> list:
        """
        @description The projects this area actually covers — the ones a work
        item may name it responsible for. Archived projects are left out: they
        grant nothing, so an area linked only to archived projects covers none
        of them, and the interface must not offer it there (defect D1).

        Uses the list endpoint's prefetch when it is there, so listing areas
        stays one query rather than one per area, and falls back to a filtered
        query for the single-unit responses, which serialize one object anyway.
        @param obj: The organizational unit being serialized.
        @returns: Project ids as strings.
        """
        links = getattr(obj, self.COVERED_PROJECTS_ATTR, None)
        if links is None:
            links = obj.unit_projects.filter(project__archived_at__isnull=True)
        return [str(link.project_id) for link in links]

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
    """How an area hands work out, as the interface needs to read it."""

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
        read_only_fields = ["id", "organizational_unit", "version", "created_at", "updated_at"]


class AssignmentDecisionSerializer(BaseSerializer):
    """
    One allocation, as the interface shows it.

    @description ``candidates_snapshot`` is deliberately **not** exposed: it
    carries the load of every person the ranking considered, which is a
    performance-shaped view of a team that a work item panel has no business
    publishing. The audit path can read the row directly.
    """

    class Meta:
        model = AssignmentDecision
        fields = [
            "id",
            "trigger",
            "requested_mode",
            "effective_mode",
            "policy_source",
            "policy_version",
            "algorithm_version",
            "outcome",
            "chosen_assignee",
            "previous_primary_executor",
            "decided_by",
            "supersedes",
            "reason",
            "created_at",
        ]
        read_only_fields = fields


class IssueRoutingSerializer(BaseSerializer):
    """Where a work item stands between "an area owns this" and "a person is on it"."""

    organizational_unit = OrganizationalUnitSerializer(read_only=True)
    current_assignment_decision = AssignmentDecisionSerializer(read_only=True)

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
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class OrganizationalUnitCoordinatorSerializer(BaseSerializer):
    """
    Who runs an area's queue, with the same light member details the
    membership serializer carries so one list can render both.

    @description ``workspace_member`` is read-only for the reason the
    membership gives: re-pointing it would leave the previous person holding
    the ``ProjectMember`` rows this coordination granted them. Ending a
    coordination is DELETE, which withdraws first.
    """

    member_id = serializers.UUIDField(source="workspace_member.member_id", read_only=True)
    display_name = serializers.CharField(source="workspace_member.member.display_name", read_only=True)
    email = serializers.CharField(source="workspace_member.member.email", read_only=True)
    avatar_url = serializers.CharField(source="workspace_member.member.avatar_url", read_only=True)
    workspace_role = serializers.IntegerField(source="workspace_member.role", read_only=True)

    class Meta:
        model = OrganizationalUnitCoordinator
        fields = [
            "id",
            "organizational_unit",
            "workspace_member",
            "is_active",
            "member_id",
            "display_name",
            "email",
            "avatar_url",
            "workspace_role",
            "created_at",
        ]
        read_only_fields = ["organizational_unit", "workspace_member", "created_at"]


class QueueItemSerializer(BaseSerializer):
    """
    One row of a coordinator's inbox.

    @description Deliberately flatter and wider than ``IssueRoutingSerializer``:
    a queue row has to be readable without a second request per item, so it
    carries the work item's identifier, title, native state and due date
    alongside the routing fields. It does not carry the area — every row on the
    page belongs to the same one.

    ``assignment_overdue`` and ``age_seconds`` come from the queryset's
    annotation and the page's single ``now`` (see ``services/orca/queue.py``):
    computing them per row would let two items on one page be judged against
    different instants and sort against each other by microseconds.
    """

    issue_id = serializers.UUIDField(read_only=True)
    sequence_id = serializers.IntegerField(source="issue.sequence_id", read_only=True)
    name = serializers.CharField(source="issue.name", read_only=True)
    project_identifier = serializers.CharField(source="issue.project.identifier", read_only=True)
    target_date = serializers.DateField(source="issue.target_date", read_only=True)
    state_group = serializers.CharField(source="issue.state.group", read_only=True, default=None)
    state_name = serializers.CharField(source="issue.state.name", read_only=True, default=None)
    assignment_overdue = serializers.BooleanField(read_only=True, default=False)
    age_seconds = serializers.SerializerMethodField()
    primary_executor_detail = serializers.SerializerMethodField()
    process = serializers.SerializerMethodField()

    def get_process(self, obj) -> dict:
        """
        @description Which step of which process instance this row is, when it
        is one (item 4.6). The instance's progress is not counted here — that
        would be a query per row — but read from the context, where the view
        computed it once for the whole page.
        @param obj: The ``IssueOrganizationalUnit`` row.
        @returns dict or ``None``.
        """
        step = self.context.get("process_steps", {}).get(obj.issue_id)
        if step is None:
            return None
        progress = self.context.get("process_progress", {}).get(step.process_instance_id, {})
        return {
            "instance_id": str(step.process_instance_id),
            "source": step.process_instance.external_source,
            "external_instance_id": step.process_instance.external_instance_id,
            "template_name": step.process_instance.template_name,
            "step_key": step.step_key,
            "completion_mode": step.completion_mode,
            "done": progress.get("done", 0),
            "total": progress.get("total", 0),
        }

    def get_age_seconds(self, obj) -> int:
        """
        @description How long this item has been waiting, in seconds, or
        ``None`` for one that is not waiting on anybody.
        @param obj: The ``IssueOrganizationalUnit`` row.
        @returns int or None.
        """
        if obj.queued_at is None:
            return None
        now = self.context.get("now")
        if now is None:
            return None
        return int((now - obj.queued_at).total_seconds())

    def get_primary_executor_detail(self, obj) -> dict:
        """
        @description The person on the item, as much as a queue row needs to
        draw an avatar and a name.
        @param obj: The ``IssueOrganizationalUnit`` row.
        @returns dict or None.
        """
        executor = obj.primary_executor
        if executor is None:
            return None
        return {
            "id": str(executor.id),
            "display_name": executor.display_name,
            "avatar_url": executor.avatar_url,
        }

    class Meta:
        model = IssueOrganizationalUnit
        fields = [
            "id",
            "issue_id",
            "sequence_id",
            "name",
            "project",
            "project_identifier",
            "target_date",
            "state_group",
            "state_name",
            "routing_state",
            "queue_reason",
            "queued_at",
            "assignment_due_at",
            "assignment_overdue",
            "age_seconds",
            "last_alerted_at",
            "primary_executor",
            "primary_executor_detail",
            "process",
            "current_assignment_decision",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WorkspaceMemberAvailabilitySerializer(BaseSerializer):
    """
    One window in which somebody is not taking work.

    @description ``reason`` is one of three coarse values on purpose — the
    layer needs to know that a person is away, not why in any detail a
    colleague could read off a queue screen. Nothing here carries a note field
    for the same reason.
    """

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
        # The window's identity is the person and the interval. Editing one in
        # place would rewrite history a decision may already have been made
        # under; the way to change an absence is to delete it and record the
        # one that is true.
        read_only_fields = ["workspace_member", "source", "external_id", "created_at"]


class MembershipAllocationSettingsSerializer(BaseSerializer):
    """What one person will accept from one area."""

    class Meta:
        model = MembershipAllocationSettings
        fields = ["id", "membership", "accepts_new_work", "max_open_items", "created_at", "updated_at"]
        read_only_fields = ["id", "membership", "created_at", "updated_at"]
