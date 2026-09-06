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
    OrcaDomainError,
    orca_public_api_enabled,
    organizational_units_enabled,
    plan_access,
    reconcile_membership,
    reconcile_unit,
    reconcile_unit_project,
    resolve_policy,
    set_responsibility,
    unit_covers_project,
    workload_snapshot,
)
from plane.db.models import (
    AssignmentMode,
    DecisionOutcome,
    Issue,
    IssueAssignee,
    IssueResponsibilityEvent,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    ResponsibilitySource,
    Project,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView, BaseViewSet

# Native project roles a unit may hand out, mirroring ``ROLE_CHOICES``.
VALID_PROJECT_ROLES = {ROLE.GUEST.value, ROLE.MEMBER.value, ROLE.ADMIN.value}

# The assignment route answers in its own vocabulary, older than the service's
# outcomes and still what the interface reads.
ASSIGN_REASON_FOR_OUTCOME = {
    DecisionOutcome.ASSIGNED: "assigned",
    DecisionOutcome.ALLOCATION_FAILED: "no_eligible_member",
    DecisionOutcome.QUEUED: "queued",
}


def _assigned_payload(result):
    """
    @description The person the allocation chose, with the numbers that chose
    them, read back out of the decision so the caller sees what was recorded
    rather than a second count.
    @param result: An ``AllocationResult``.
    @returns dict, or ``None`` when nobody was assigned.
    """
    if result.chosen_user_id is None:
        return None
    chosen_id = str(result.chosen_user_id)
    snapshot = next(
        (row for row in (result.decision.candidates_snapshot or []) if row.get("user_id") == chosen_id),
        {},
    )
    return {
        "user_id": chosen_id,
        "open_issues": snapshot.get("total_open", 0),
        "last_assigned_at": snapshot.get("last_auto_at"),
    }


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
                # So the app can show the automation instructions only where
                # they would work. The two switches are independent: the layer
                # can be on for people while /api/v1/orca/ stays shut, and a UI
                # that told everyone to go and call it would be wrong on every
                # instance that has not opened it.
                "public_api_enabled": orca_public_api_enabled(),
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
            # Feeds OrganizationalUnitSerializer.project_ids in one query
            # instead of one per area, and applies the same archived-project
            # rule the coverage check uses.
            .prefetch_related(
                Prefetch(
                    "unit_projects",
                    queryset=OrganizationalUnitProject.objects.filter(project__archived_at__isnull=True),
                    to_attr=OrganizationalUnitSerializer.COVERED_PROJECTS_ATTR,
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
    ``Issue``. Marking an area responsible is where the area's policy applies,
    so the response says what happened to the item — queued for a coordinator,
    waiting for a claim, or assigned — rather than only which area it is.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        link = (
            IssueOrganizationalUnit.objects.filter(issue_id=issue_id, project_id=project_id, workspace__slug=slug)
            .select_related("organizational_unit", "current_assignment_decision")
            .first()
        )
        if link is None:
            return Response({"organizational_unit": None, "routing": None}, status=status.HTTP_200_OK)
        return Response(
            {
                "organizational_unit": OrganizationalUnitSerializer(link.organizational_unit).data,
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

        try:
            # The service creates the link (or transfers it), records the
            # responsibility event, applies the area's policy and writes the
            # decision. Coverage (defect D1) is checked in there, so this view
            # no longer has its own copy of the rule.
            result = set_responsibility(
                issue,
                unit,
                actor=request.user,
                source=ResponsibilitySource.INTERNAL_API,
                requested_mode=request.data.get("mode"),
                reason=request.data.get("reason", ""),
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status)

        return Response(
            {
                "organizational_unit": OrganizationalUnitSerializer(unit).data,
                "routing": IssueRoutingSerializer(result.link).data,
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

        with transaction.atomic():
            # The event is what keeps "this used to belong to Support" from
            # disappearing with the link (invariant I6). Assignees stay: the
            # item goes back to being an ordinary Plane work item.
            IssueResponsibilityEvent.objects.create(
                issue_id=link.issue_id,
                workspace_id=link.workspace_id,
                from_unit=link.organizational_unit,
                to_unit=None,
                actor=request.user,
                source=ResponsibilitySource.INTERNAL_API,
            )
            link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class IssueOrganizationalUnitAssignEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Run the area's allocation for a work item.

    @description Everything happens in ``assignment_service``: the row lock,
    the advisory lock per area for automatic allocation, eligibility at
    decision time, and the decision record. Existing assignees are never
    replaced. The area is made responsible if it was not already, so pressing
    this leaves the same trail as marking the area by hand.

    This is a person asking for the ranking, so it requests ``least_loaded``
    rather than taking the area's default — an area whose policy says work is
    handed out by a coordinator would otherwise answer the button by queueing
    the item again. An area that has configured ``allowed_modes`` without
    ``least_loaded`` refuses (``ORG_ASSIGNMENT_MODE_NOT_ALLOWED``); one with no
    policy at all permits it. Pass ``assignment_mode`` to ask for something
    else.
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

        # Checked here as well as in the service: the service answers "nobody
        # eligible", which is true but unhelpful, and this route can say the
        # actual reason — the project was unlinked or archived after the item
        # was marked as the area's (defect D1).
        if not unit_covers_project(unit, issue.project_id):
            return orca_error("ORG_UNIT_NOT_COVERING_PROJECT")

        # `fill_empty` and `append` are the legacy shape of this route: they say
        # what to do about people already on the item, not how to choose one.
        # The service speaks in assignment modes, so the two are kept apart and
        # `mode` here is deprecated — `assignment_mode` is the new field.
        mode = request.data.get("mode", MODE_FILL_EMPTY)
        if mode not in (MODE_FILL_EMPTY, MODE_APPEND):
            return orca_error("ORG_INVALID_ASSIGNMENT_MODE")

        current_assignees = list(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True))
        if current_assignees and mode == MODE_FILL_EMPTY:
            link = IssueOrganizationalUnit.objects.filter(issue=issue).first()
            return Response(
                {
                    "assigned": None,
                    "reason": "already_assigned",
                    "routing": IssueRoutingSerializer(link).data if link else None,
                },
                status=status.HTTP_200_OK,
            )

        try:
            # Through the service, so an assignment made from this route has the
            # same row lock, routing state and decision record as any other —
            # the link is created here too when the caller named the area.
            result = set_responsibility(
                issue,
                unit,
                actor=request.user,
                source=ResponsibilitySource.INTERNAL_API,
                requested_mode=request.data.get("assignment_mode") or AssignmentMode.LEAST_LOADED.value,
                exclude_user_ids=current_assignees if mode == MODE_APPEND else (),
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status)

        return Response(
            {
                "assigned": _assigned_payload(result),
                "reason": ASSIGN_REASON_FOR_OUTCOME.get(result.outcome, result.outcome),
                "routing": IssueRoutingSerializer(result.link).data,
            },
            status=status.HTTP_200_OK,
        )


class OrganizationalUnitPolicyEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    The assignment policy in force for an area, optionally for one project.

    @description Resolved rather than stored: the interface needs to know what
    *would* happen, and that is the project policy over the area policy over
    the fallback. Without this, a panel offering "assign automatically" cannot
    tell whether the area even allows it.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id, project_id=None):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        resolution = resolve_policy(unit, project_id)
        return Response(
            {
                "effective_mode": resolution.effective_mode,
                "policy_source": resolution.policy_source,
                "policy_version": resolution.policy_version,
                "allowed_modes": list(resolution.allowed_modes),
                "assignment_sla_seconds": resolution.sla_seconds,
                "max_open_items_per_member": resolution.max_open_items_per_member,
                "policy": AssignmentPolicySerializer(resolution.policy).data if resolution.policy else None,
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
