# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Internal API for the Orca organizational layer, served under ``/api/orca/``.

Per FORK.md these endpoints live in the fork's own namespace and never touch
upstream routes. Mutations are restricted to workspace Admins: adding someone
to a unit is an authorization operation that can grant access to many projects
at once, so v1 keeps it centralized. Unit leads have read access only.
"""

# Django imports
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.utils.text import slugify

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions.base import ROLE, allow_permission
from plane.app.serializers import (
    AssignmentDecisionSerializer,
    AssignmentPolicySerializer,
    IssueRoutingSerializer,
    OrganizationalUnitMembershipCreateSerializer,
    OrganizationalUnitMembershipSerializer,
    OrganizationalUnitProjectSerializer,
    OrganizationalUnitSerializer,
)
from plane.app.services.orca import (
    MODE_APPEND,
    MODE_FILL_EMPTY,
    allocate,
    availability_enabled,
    resolve_policy,
    set_responsibility,
    plan_access,
    reconcile_membership,
    reconcile_unit,
    reconcile_unit_project,
    unit_covers_project,
    workload_snapshot,
)
from plane.api.views.orca.base import orca_public_api_enabled
from plane.app.services.orca.errors import OrcaDomainError
from plane.db.models import (
    AssignmentMode,
    Issue,
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    Project,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView, BaseViewSet

# The modes a caller may name today. MODE_FILL_EMPTY and MODE_APPEND are the
# v1 vocabulary and are still accepted; see the assign endpoint's docstring.
LIVE_ASSIGNMENT_MODES = (
    AssignmentMode.MANUAL,
    AssignmentMode.SELF_CLAIM,
    AssignmentMode.LEAST_LOADED,
)

# Native project roles a unit may hand out, mirroring ``ROLE_CHOICES``.
VALID_PROJECT_ROLES = {ROLE.GUEST.value, ROLE.MEMBER.value, ROLE.ADMIN.value}


class OrganizationalUnitFeatureMixin:
    """
    Kill switch for the organizational layer.

    ``ORCA_ORG_UNITS_ENABLED=0`` has to actually stop the feature rather than
    merely describe an intent. This layer writes native ``ProjectMember`` rows,
    so an operator turning it off is withdrawing a permission-granting
    subsystem: leaving the API reachable while only the UI hides would keep
    every mutation one curl away.

    Enforced per request rather than by registering routes conditionally, so
    the switch does not depend on import order and the API and the UI agree the
    moment the setting changes — which is also what makes it testable.

    Answers 404, not 403: a disabled feature should read as absent rather than
    as something the caller merely lacks rights for.
    """

    def initial(self, request, *args, **kwargs):
        if not organizational_units_enabled():
            raise Http404("The organizational layer is disabled on this instance")
        return super().initial(request, *args, **kwargs)


def organizational_units_enabled() -> bool:
    """Whether the organizational layer is switched on for this instance."""
    return bool(getattr(settings, "ORCA_ORG_UNITS_ENABLED", True))


class OrcaConfigEndpoint(BaseAPIView):
    """
    Which Orca features this instance has switched on.

    @description Deliberately outside the kill switch: the UI has to be able to
    ask whether the organizational layer exists in order to hide it, which it
    could not do through an endpoint that the same switch makes invisible.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        return Response(
            {
                "organizational_units_enabled": organizational_units_enabled(),
                # So the interface can show integration instructions only where
                # they would actually work.
                "public_api_enabled": orca_public_api_enabled(),
                # With this off the availability forms are pointless: nothing
                # reads what they would save.
                "availability_enabled": availability_enabled(),
            },
            status=status.HTTP_200_OK,
        )


class OrganizationalUnitViewSet(OrganizationalUnitFeatureMixin, BaseViewSet):
    """CRUD for organizational units inside a workspace."""

    serializer_class = OrganizationalUnitSerializer
    model = OrganizationalUnit

    search_fields = ["name", "slug"]

    def get_queryset(self):
        return (
            OrganizationalUnit.objects.filter(workspace__slug=self.kwargs.get("slug"))
            .annotate(member_count=Count("memberships", filter=Q(memberships__is_active=True), distinct=True))
            .annotate(project_count=Count("unit_projects", distinct=True))
            .select_related("workspace")
            # Feeds the serializer's project_ids in one query instead of one
            # per unit; archived projects are dropped here, as coverage does.
            .prefetch_related(
                Prefetch(
                    "unit_projects",
                    queryset=OrganizationalUnitProject.objects.filter(project__archived_at__isnull=True),
                    to_attr="covered_links",
                )
            )
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug):
        units = self.get_queryset().order_by("name")
        serializer = OrganizationalUnitSerializer(units, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def retrieve(self, request, slug, pk):
        unit = self.get_queryset().filter(pk=pk).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")
        return Response(OrganizationalUnitSerializer(unit).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        name = request.data.get("name")
        if not name:
            return orca_error("ORG_UNIT_NAME_REQUIRED")

        unit_slug = request.data.get("slug") or slugify(name)
        if OrganizationalUnit.objects.filter(workspace=workspace, slug=unit_slug).exists():
            return orca_error("ORG_UNIT_SLUG_TAKEN", status.HTTP_409_CONFLICT)

        serializer = OrganizationalUnitSerializer(data={**request.data, "slug": unit_slug})
        if serializer.is_valid():
            unit = serializer.save(workspace=workspace)
            # Re-read through the annotated queryset so the created resource
            # carries member_count and project_count like every other read
            # does. Without this the client gets a unit whose counts are
            # undefined and renders them as blanks.
            created = self.get_queryset().filter(pk=unit.pk).first()
            return Response(OrganizationalUnitSerializer(created).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, pk):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=pk).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        # Checked up front rather than left to the unique constraint: an
        # IntegrityError would abort the surrounding transaction, so the clean
        # 409 has to come before the write is attempted.
        new_slug = request.data.get("slug")
        if (
            new_slug
            and new_slug != unit.slug
            and OrganizationalUnit.objects.filter(workspace_id=unit.workspace_id, slug=new_slug)
            .exclude(pk=unit.pk)
            .exists()
        ):
            return orca_error("ORG_UNIT_SLUG_TAKEN", status.HTTP_409_CONFLICT)

        serializer = OrganizationalUnitSerializer(unit, data=request.data, partial=True)
        if serializer.is_valid():
            with transaction.atomic():
                serializer.save()
                # Deactivating a unit withdraws the access it sourced.
                reconcile_unit(unit)
            updated = self.get_queryset().filter(pk=unit.pk).first()
            return Response(OrganizationalUnitSerializer(updated).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, pk):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=pk).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        # Withdraw inherited access before the unit disappears, so the ledger
        # can still tell which project access it was responsible for. All three
        # steps share one transaction: a half-applied delete would leave the
        # unit gone and the inherited ProjectMember rows orphaned.
        with transaction.atomic():
            unit.is_active = False
            unit.save()
            reconcile_unit(unit, force_sync=True)
            unit.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationalUnitMemberViewSet(OrganizationalUnitFeatureMixin, BaseViewSet):
    """Manage who belongs to an organizational unit."""

    serializer_class = OrganizationalUnitMembershipSerializer
    model = OrganizationalUnitMembership

    def get_unit(self, slug, unit_id):
        return OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug, unit_id):
        # Only active memberships: a directory sync withdraws access by
        # deactivating the row rather than deleting it, so that the provenance
        # survives for audit and re-adding somebody is a flag flip. Listing
        # those rows would show people who have left the area as if they were
        # still in it — and would disagree with ``member_count``, which has
        # always counted active memberships only.
        memberships = OrganizationalUnitMembership.objects.filter(
            organizational_unit_id=unit_id,
            organizational_unit__workspace__slug=slug,
            is_active=True,
        ).select_related("workspace_member", "workspace_member__member")
        serializer = OrganizationalUnitMembershipSerializer(memberships, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug, unit_id):
        unit = self.get_unit(slug, unit_id)
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        # Validated before anything is written: `role` never reaches the model
        # through a serializer on this path, and a second active lead would
        # otherwise abort the transaction with an IntegrityError from the
        # single-lead partial index instead of returning a 400.
        payload = OrganizationalUnitMembershipCreateSerializer(data=request.data, context={"organizational_unit": unit})
        if not payload.is_valid():
            return Response(payload.errors, status=status.HTTP_400_BAD_REQUEST)

        member_ids = payload.validated_data["workspace_member_ids"]
        role = payload.validated_data["role"]

        # v1 grants access to existing workspace members only; units never invite.
        workspace_members = list(
            WorkspaceMember.objects.filter(id__in=member_ids, workspace_id=unit.workspace_id, is_active=True)
        )
        if len(workspace_members) != len(member_ids):
            return orca_error("ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE")

        created = []
        with transaction.atomic():
            for workspace_member in workspace_members:
                membership, was_created = OrganizationalUnitMembership.objects.get_or_create(
                    organizational_unit=unit,
                    workspace_member=workspace_member,
                    defaults={"workspace_id": unit.workspace_id, "role": role},
                )
                if not was_created:
                    changed = False
                    if not membership.is_active:
                        # Reactivation restores the role the membership was
                        # stored with rather than overwriting it; the validator
                        # has already rejected the case where reviving a stored
                        # lead would produce a second active one.
                        membership.is_active = True
                        changed = True
                    if role == OrganizationalUnitMemberRole.LEAD and membership.role != role:
                        # An explicit lead request is unambiguous, and the
                        # validator has ruled out a conflicting active lead.
                        membership.role = role
                        changed = True
                    if changed:
                        membership.save()
                created.append(membership)

            reconcile_unit(unit)
        serializer = OrganizationalUnitMembershipSerializer(created, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, unit_id, pk):
        membership = OrganizationalUnitMembership.objects.filter(
            pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
        ).first()
        if membership is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        # A unit has at most one active lead, enforced by a partial unique
        # index. Rejecting the second lead here keeps it a validation error
        # instead of an IntegrityError that would poison the transaction.
        if (
            request.data.get("role") == OrganizationalUnitMemberRole.LEAD
            and membership.role != OrganizationalUnitMemberRole.LEAD
            and OrganizationalUnitMembership.objects.filter(
                organizational_unit_id=unit_id,
                role=OrganizationalUnitMemberRole.LEAD,
                is_active=True,
            )
            .exclude(pk=membership.pk)
            .exists()
        ):
            return orca_error("ORG_UNIT_LEAD_ALREADY_SET")

        serializer = OrganizationalUnitMembershipSerializer(membership, data=request.data, partial=True)
        if serializer.is_valid():
            with transaction.atomic():
                serializer.save()
                reconcile_membership(membership)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, unit_id, pk):
        membership = OrganizationalUnitMembership.objects.filter(
            pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
        ).first()
        if membership is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        # Deactivate then reconcile synchronously: the reconciler must observe
        # the membership row while deciding what access to withdraw. One
        # transaction, so a failure never strands the withdrawn access.
        with transaction.atomic():
            membership.is_active = False
            membership.save()
            reconcile_membership(membership, force_sync=True)
            membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationalUnitProjectViewSet(OrganizationalUnitFeatureMixin, BaseViewSet):
    """Manage which projects a unit grants access to, and at which role."""

    serializer_class = OrganizationalUnitProjectSerializer
    model = OrganizationalUnitProject

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug, unit_id):
        unit_projects = OrganizationalUnitProject.objects.filter(
            organizational_unit_id=unit_id,
            organizational_unit__workspace__slug=slug,
        ).select_related("project")
        serializer = OrganizationalUnitProjectSerializer(unit_projects, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug, unit_id):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        project_id = request.data.get("project_id")
        if not project_id:
            return orca_error("ORG_UNIT_PROJECT_REQUIRED")

        # The role arrives as raw JSON, so a non-numeric value has to be a
        # validation error rather than an uncaught cast.
        try:
            default_role = int(request.data.get("default_role", ROLE.MEMBER.value))
        except (TypeError, ValueError):
            return orca_error("ORG_UNIT_INVALID_ROLE")
        if default_role not in VALID_PROJECT_ROLES:
            return orca_error("ORG_UNIT_INVALID_ROLE")

        project = Project.objects.filter(pk=project_id, workspace_id=unit.workspace_id).first()
        if project is None:
            return orca_error("ORG_UNIT_PROJECT_NOT_IN_WORKSPACE")

        with transaction.atomic():
            unit_project, created = OrganizationalUnitProject.objects.get_or_create(
                organizational_unit=unit,
                project=project,
                defaults={"workspace_id": unit.workspace_id, "default_role": default_role},
            )
            if not created and unit_project.default_role != default_role:
                unit_project.default_role = default_role
                unit_project.save()

            reconcile_unit_project(unit_project)
        serializer = OrganizationalUnitProjectSerializer(unit_project)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def partial_update(self, request, slug, unit_id, pk):
        unit_project = OrganizationalUnitProject.objects.filter(
            pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
        ).first()
        if unit_project is None:
            return orca_not_found("ORG_UNIT_LINK_NOT_FOUND")

        serializer = OrganizationalUnitProjectSerializer(unit_project, data=request.data, partial=True)
        if serializer.is_valid():
            with transaction.atomic():
                serializer.save()
                reconcile_unit_project(unit_project)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, unit_id, pk):
        unit_project = OrganizationalUnitProject.objects.filter(
            pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
        ).first()
        if unit_project is None:
            return orca_not_found("ORG_UNIT_LINK_NOT_FOUND")

        project_id = unit_project.project_id
        workspace_id = unit_project.workspace_id
        member_ids = list(
            OrganizationalUnitMembership.objects.filter(organizational_unit_id=unit_id).values_list(
                "workspace_member_id", flat=True
            )
        )

        # Unlinking removes the source, so the reconcile has to run after the
        # link is gone — but inside the same transaction, so a failure cannot
        # leave the link deleted and the access untouched.
        from plane.app.services.orca import reconcile_access

        with transaction.atomic():
            unit_project.delete()
            reconcile_access(workspace_id, member_ids or None, [project_id])
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationalUnitEffectiveAccessEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Strictly read-only preview of the access a unit currently sources.

    @description Runs the same resolver the reconciler uses, without writing,
    so admins can see current state, desired state and provenance before
    changing anything.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        member_ids = list(
            OrganizationalUnitMembership.objects.filter(organizational_unit_id=unit.id).values_list(
                "workspace_member_id", flat=True
            )
        )
        project_ids = list(
            OrganizationalUnitProject.objects.filter(organizational_unit_id=unit.id).values_list(
                "project_id", flat=True
            )
        )
        if not member_ids or not project_ids:
            return Response({"changes": []}, status=status.HTTP_200_OK)

        changes = plan_access(unit.workspace_id, member_ids, project_ids)
        return Response({"changes": [change.as_dict() for change in changes]}, status=status.HTTP_200_OK)


class UserOrganizationalUnitsEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    The requesting user's own units, their role in each, and linked projects.

    @description Cheap read endpoint that lets the UI show "my areas" without
    admin permissions. The v1 UI only uses workspace settings, but shipping the
    endpoint now means the view can be added later without reshaping the API.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        memberships = (
            OrganizationalUnitMembership.objects.filter(
                organizational_unit__workspace__slug=slug,
                workspace_member__member=request.user,
                is_active=True,
                organizational_unit__is_active=True,
            )
            .select_related("organizational_unit")
            .prefetch_related("organizational_unit__unit_projects")
        )

        payload = [
            {
                "organizational_unit": OrganizationalUnitSerializer(membership.organizational_unit).data,
                "role": membership.role,
                "projects": OrganizationalUnitProjectSerializer(
                    membership.organizational_unit.unit_projects.all(), many=True
                ).data,
            }
            for membership in memberships
        ]
        return Response(payload, status=status.HTTP_200_OK)


class IssueOrganizationalUnitEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Set, read, or clear the organizational unit responsible for a work item.

    @description The responsible unit is a sidecar link, not a column on
    ``Issue``. Assignment stays a separate, explicit action so marking a unit
    responsible never silently changes who is assigned.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        link = (
            IssueOrganizationalUnit.objects.filter(issue_id=issue_id, project_id=project_id, workspace__slug=slug)
            .select_related("organizational_unit", "primary_executor", "current_assignment_decision")
            .first()
        )
        if link is None:
            return Response({"organizational_unit": None, "routing": None}, status=status.HTTP_200_OK)
        return Response(
            {
                "organizational_unit": OrganizationalUnitSerializer(link.organizational_unit).data,
                # Where it stands in the area's queue, so the interface can say
                # more than "this area owns it".
                "routing": IssueRoutingSerializer(link).data,
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        unit = OrganizationalUnit.objects.filter(
            pk=request.data.get("organizational_unit_id"), workspace_id=issue.workspace_id
        ).first()
        if unit is None:
            return orca_error("ORG_UNIT_NOT_IN_WORKSPACE")

        # An area only owns work in the projects it covers: its members inherit
        # project access from that link, so work routed anywhere else has
        # nobody eligible to take it — or lands on someone who cannot see it.
        if not unit_covers_project(unit, issue.project_id):
            return orca_error("ORG_UNIT_NOT_COVERING_PROJECT")

        # Everything that touches responsibility or execution goes through the
        # service: it is what writes the queue state and the decision record.
        try:
            result = set_responsibility(
                issue,
                unit,
                actor=request.user,
                source="ui",
                requested_mode=request.data.get("mode"),
                trigger="internal_api",
                reason=request.data.get("reason", ""),
            )
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        return Response(
            {
                "organizational_unit": OrganizationalUnitSerializer(unit).data,
                "routing": IssueRoutingSerializer(result.link).data,
                "decision": AssignmentDecisionSerializer(result.decision).data if result.decision else None,
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def delete(self, request, slug, project_id, issue_id):
        link = IssueOrganizationalUnit.objects.filter(
            issue_id=issue_id, project_id=project_id, workspace__slug=slug
        ).first()
        if link is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        # Record where responsibility went before dropping the link, otherwise
        # the history says the work item was never in an area at all. The
        # native assignees stay: it just goes back to being an ordinary work
        # item, with the same people on it.
        IssueResponsibilityEvent.objects.create(
            issue_id=issue_id,
            workspace_id=link.workspace_id,
            from_unit=link.organizational_unit,
            to_unit=None,
            actor=request.user,
            source="ui",
            reason=request.data.get("reason", "") if isinstance(request.data, dict) else "",
        )
        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class IssueOrganizationalUnitAssignEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Allocate a work item to a member of its responsible unit.

    @description Delegates to the assignment service, so an allocation made
    here moves the queue state and writes a decision like any other.

    ``mode`` accepts the assignment modes (``manual``, ``self_claim``,
    ``least_loaded``) and, DEPRECATED since the queue landed, the v1 vocabulary
    ``fill_empty`` and ``append``. Both map to automatic allocation: the
    service never replaces an existing assignee, so "append" is what it always
    does. They will be removed in Phase 2, when the interface speaks the
    service's vocabulary.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        unit_id = request.data.get("organizational_unit_id")
        if unit_id:
            unit = OrganizationalUnit.objects.filter(pk=unit_id, workspace_id=issue.workspace_id).first()
        else:
            link = IssueOrganizationalUnit.objects.filter(issue=issue).select_related("organizational_unit").first()
            unit = link.organizational_unit if link else None

        if unit is None:
            return orca_error("ORG_WORK_ITEM_HAS_NO_UNIT")

        if not unit_covers_project(unit, issue.project_id):
            return orca_error("ORG_UNIT_NOT_COVERING_PROJECT")

        mode = request.data.get("mode", MODE_FILL_EMPTY)
        if mode in (MODE_FILL_EMPTY, MODE_APPEND):
            requested_mode = AssignmentMode.LEAST_LOADED
            # v1 semantics kept intact for the deprecated vocabulary: in
            # fill_empty a work item that already has somebody on it is left
            # alone. Callers on the new vocabulary get the service's own
            # behaviour, where allocating never removes an existing assignee.
            if mode == MODE_FILL_EMPTY and IssueAssignee.objects.filter(issue=issue).exists():
                return Response({"assigned": None, "reason": "already_assigned"}, status=status.HTTP_200_OK)
        elif mode in LIVE_ASSIGNMENT_MODES:
            requested_mode = mode
        else:
            return orca_error("ORG_INVALID_ASSIGNMENT_MODE")

        explicit_executor = request.data.get("primary_executor")

        try:
            result = allocate(
                issue,
                unit,
                requested_mode=None if explicit_executor else requested_mode,
                explicit_executor=explicit_executor,
                actor=request.user,
                trigger="internal_api",
                reason=request.data.get("reason", ""),
            )
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        # An allocation that ran and found nobody says so with the queue reason
        # ("no_eligible_member"), which is what the callers already read.
        reason = result.link.queue_reason if result.outcome == "allocation_failed" else result.outcome

        return Response(
            {
                "assigned": str(result.executor_id) if result.executor_id else None,
                "reason": reason,
                "routing": IssueRoutingSerializer(result.link).data,
                "decision": AssignmentDecisionSerializer(result.decision).data if result.decision else None,
            },
            status=status.HTTP_200_OK,
        )


class OrganizationalUnitWorkloadEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """Open-work count per unit member, across the unit's own projects."""

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")
        return Response(workload_snapshot(unit), status=status.HTTP_200_OK)


class OrganizationalUnitPolicyEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    The assignment policy an area applies, resolved.

    @description Read-only, and deliberately the *effective* policy rather
    than the stored rows: the interface needs to say "work here is claimed by
    whoever is free", and answering that from two possible rows is how the
    interface and the API end up disagreeing.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id, pk=None):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        project_id = pk
        if project_id is not None and not unit_covers_project(unit, project_id):
            return orca_error("ORG_UNIT_NOT_COVERING_PROJECT")

        try:
            resolution = resolve_policy(unit, project_id)
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        return Response(
            {
                "effective_mode": resolution.effective_mode,
                "policy_source": resolution.policy_source,
                "policy_version": resolution.policy_version,
                "assignment_sla_seconds": resolution.assignment_sla_seconds,
                "policy": AssignmentPolicySerializer(resolution.policy).data if resolution.policy else None,
            },
            status=status.HTTP_200_OK,
        )
