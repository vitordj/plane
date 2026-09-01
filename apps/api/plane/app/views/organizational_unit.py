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
from django.db.models import Count, Q
from django.http import Http404
from django.utils.text import slugify

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions.base import ROLE, allow_permission
from plane.app.serializers import (
    OrganizationalUnitMembershipCreateSerializer,
    OrganizationalUnitMembershipSerializer,
    OrganizationalUnitProjectSerializer,
    OrganizationalUnitSerializer,
)
from plane.app.services.orca import (
    MODE_APPEND,
    MODE_FILL_EMPTY,
    assign_from_unit,
    plan_access,
    reconcile_membership,
    reconcile_unit,
    reconcile_unit_project,
    workload_snapshot,
)
from plane.db.models import (
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    Project,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole

from .base import BaseAPIView, BaseViewSet

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
            {"organizational_units_enabled": organizational_units_enabled()},
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
            return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(OrganizationalUnitSerializer(unit).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        name = request.data.get("name")
        if not name:
            return Response({"error": "Name is required"}, status=status.HTTP_400_BAD_REQUEST)

        unit_slug = request.data.get("slug") or slugify(name)
        if OrganizationalUnit.objects.filter(workspace=workspace, slug=unit_slug).exists():
            return Response(
                {"error": "An organizational unit with this slug already exists"},
                status=status.HTTP_409_CONFLICT,
            )

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
            return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)

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
            return Response(
                {"error": "An organizational unit with this slug already exists"},
                status=status.HTTP_409_CONFLICT,
            )

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
            return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)

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
        memberships = OrganizationalUnitMembership.objects.filter(
            organizational_unit_id=unit_id,
            organizational_unit__workspace__slug=slug,
        ).select_related("workspace_member", "workspace_member__member")
        serializer = OrganizationalUnitMembershipSerializer(memberships, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug, unit_id):
        unit = self.get_unit(slug, unit_id)
        if unit is None:
            return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)

        # Validated before anything is written: `role` never reaches the model
        # through a serializer on this path, and a second active lead would
        # otherwise abort the transaction with an IntegrityError from the
        # single-lead partial index instead of returning a 400.
        payload = OrganizationalUnitMembershipCreateSerializer(
            data=request.data, context={"organizational_unit": unit}
        )
        if not payload.is_valid():
            return Response(payload.errors, status=status.HTTP_400_BAD_REQUEST)

        member_ids = payload.validated_data["workspace_member_ids"]
        role = payload.validated_data["role"]

        # v1 grants access to existing workspace members only; units never invite.
        workspace_members = list(
            WorkspaceMember.objects.filter(id__in=member_ids, workspace_id=unit.workspace_id, is_active=True)
        )
        if len(workspace_members) != len(member_ids):
            return Response(
                {"error": "All members must be active members of this workspace"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            return Response({"error": "Membership not found"}, status=status.HTTP_404_NOT_FOUND)

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
            return Response(
                {"error": "This organizational unit already has an active lead"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            return Response({"error": "Membership not found"}, status=status.HTTP_404_NOT_FOUND)

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
            return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)

        project_id = request.data.get("project_id")
        if not project_id:
            return Response({"error": "Project is required"}, status=status.HTTP_400_BAD_REQUEST)

        # The role arrives as raw JSON, so a non-numeric value has to be a
        # validation error rather than an uncaught cast.
        try:
            default_role = int(request.data.get("default_role", ROLE.MEMBER.value))
        except (TypeError, ValueError):
            return Response({"error": "Invalid role"}, status=status.HTTP_400_BAD_REQUEST)
        if default_role not in VALID_PROJECT_ROLES:
            return Response({"error": "Invalid role"}, status=status.HTTP_400_BAD_REQUEST)

        project = Project.objects.filter(pk=project_id, workspace_id=unit.workspace_id).first()
        if project is None:
            return Response(
                {"error": "Project not found in this workspace"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            return Response({"error": "Linked project not found"}, status=status.HTTP_404_NOT_FOUND)

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
            return Response({"error": "Linked project not found"}, status=status.HTTP_404_NOT_FOUND)

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
            return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)

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
        link = IssueOrganizationalUnit.objects.filter(
            issue_id=issue_id, project_id=project_id, workspace__slug=slug
        ).first()
        if link is None:
            return Response({"organizational_unit": None}, status=status.HTTP_200_OK)
        return Response(
            {"organizational_unit": OrganizationalUnitSerializer(link.organizational_unit).data},
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)

        unit = OrganizationalUnit.objects.filter(
            pk=request.data.get("organizational_unit_id"), workspace_id=issue.workspace_id
        ).first()
        if unit is None:
            return Response(
                {"error": "Organizational unit not found in this workspace"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link, _ = IssueOrganizationalUnit.objects.get_or_create(
            issue=issue,
            defaults={
                "organizational_unit": unit,
                "project_id": issue.project_id,
                "workspace_id": issue.workspace_id,
            },
        )
        if link.organizational_unit_id != unit.id:
            link.organizational_unit = unit
            link.save()

        return Response(
            {"organizational_unit": OrganizationalUnitSerializer(unit).data},
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def delete(self, request, slug, project_id, issue_id):
        IssueOrganizationalUnit.objects.filter(issue_id=issue_id, project_id=project_id, workspace__slug=slug).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class IssueOrganizationalUnitAssignEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Assign a work item to the least-loaded member of its responsible unit.

    @description Manual trigger only in v1. By default it assigns only when
    nobody is assigned yet; ``mode=append`` adds a unit member alongside the
    current assignees. Existing assignees are never replaced.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)

        unit_id = request.data.get("organizational_unit_id")
        if unit_id:
            unit = OrganizationalUnit.objects.filter(pk=unit_id, workspace_id=issue.workspace_id).first()
        else:
            link = IssueOrganizationalUnit.objects.filter(issue=issue).select_related("organizational_unit").first()
            unit = link.organizational_unit if link else None

        if unit is None:
            return Response(
                {"error": "No organizational unit is responsible for this work item"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        mode = request.data.get("mode", MODE_FILL_EMPTY)
        if mode not in (MODE_FILL_EMPTY, MODE_APPEND):
            return Response({"error": "Invalid mode"}, status=status.HTTP_400_BAD_REQUEST)

        chosen, reason = assign_from_unit(issue, unit, mode=mode)
        if chosen is None:
            return Response({"assigned": None, "reason": reason}, status=status.HTTP_200_OK)
        return Response({"assigned": chosen.as_dict(), "reason": reason}, status=status.HTTP_200_OK)


class OrganizationalUnitWorkloadEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """Open-work count per unit member, across the unit's own projects."""

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()
        if unit is None:
            return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(workload_snapshot(unit), status=status.HTTP_200_OK)
