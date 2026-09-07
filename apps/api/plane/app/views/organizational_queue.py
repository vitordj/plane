# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The coordinator's board, and the four buttons on a work item that move it.

Everything a person does to an area's work item — take it, hand it to
somebody else, send it back to the queue, move it to another area — goes
through ``assignment_service`` here exactly as the public automation API and
the "assign automatically" button already do (``IssueOrganizationalUnitAssignEndpoint``
in ``organizational_unit.py``). This module adds no allocation logic of its
own: it resolves who is allowed to press which button, and turns the
service's answer into the same ``IssueRoutingSerializer`` shape every other
Orca surface already returns.

The permission a route asks for is deliberately not uniform:

* ``claim`` is a native-permission route — Member/Admin of the *project* —
  because self-claiming is something any eligible person on the item's
  project does, area coordination or not; eligibility for the specific item
  is still enforced by the service (I4).
* ``reassign`` and ``transfer`` require coordinating the area, because
  choosing who does the area's work, or which area owns it, is the
  coordinator's call.
* ``return`` accepts the coordinator, a workspace admin, *or the person
  currently on the item* — nobody else gets to put somebody else's work back
  in the queue, but the person holding it may always let go of it.

The inbox (``OrganizationalUnitQueueEndpoint``) reuses the same queryset and
row shape the public API reads (``queue_queryset``, ``queue_row``) and adds
only what a screen needs and a script does not: ``project``/``state`` as
nested objects instead of a bare id, and per-row ``permissions`` so the
interface can grey out a button instead of letting the API refuse it after
the click.
"""

# Django imports
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.api.serializers.orca import queue_row
from plane.app.permissions.base import ROLE, allow_permission
from plane.app.permissions.organizational_unit import (
    UNIT_COORDINATOR,
    UNIT_MEMBER,
    allow_issue_unit_role,
    allow_unit_role,
    readable_project_ids,
    is_unit_coordinator,
    is_workspace_admin,
    link_for_issue,
    viewer_standing,
)
from plane.app.serializers import (
    AssignmentDecisionDetailSerializer,
    IssueRoutingSerializer,
    OrganizationalUnitCoordinatorSerializer,
)
from plane.app.services.orca import (
    ALL_STATES,
    OrcaDomainError,
    WAITING_STATES,
    claim,
    queue_queryset,
    reassign,
    reconcile_coordinator,
    resolve_policy,
    return_to_queue,
    transfer_unit,
)
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionTrigger,
    Issue,
    OrganizationalUnit,
    OrganizationalUnitCoordinator,
    ResponsibilitySource,
    RoutingState,
    WorkspaceMember,
)
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView, BaseViewSet
from .organizational_unit import OrganizationalUnitFeatureMixin

# Workspace roles a coordinator must hold (M4): a Guest promoted to
# coordinator would be the workspace cap fighting the coordination's intent,
# so the route refuses rather than silently granting a smaller role.
COORDINATOR_ELIGIBLE_ROLES = {ROLE.MEMBER.value, ROLE.ADMIN.value}

# What the ``routing_state`` filter accepts beyond a real state, same set the
# public queue endpoint validates against.
QUEUE_STATE_CHOICES = {*RoutingState.values, ALL_STATES}


def _tri_state(value):
    """Read a query parameter that means yes, no, or "did not ask". See the public endpoint's twin."""
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes"):
        return True
    if lowered in ("0", "false", "no"):
        return False
    return None


def _internal_queue_row(link, *, now, viewer, user_id, self_claim_allowed):
    """
    @description The public queue row, plus what a screen needs and a script
    does not: nested ``project``/``state``, ``priority``/``target_date``,
    ``current_decision_id`` (the interface's If-Match), and per-row
    ``permissions`` so a button can be greyed out instead of refused after the
    click (RFC §1.2: the UI filters, the API rejects).
    @param self_claim_allowed: Whether ``self_claim`` is in the project's
        effective ``allowed_modes`` — resolved once per project per page by the
        caller, not once per row.
    """
    payload = queue_row(link, now=now)
    issue = link.issue
    state = issue.state

    del payload["project_id"]
    payload["project"] = {
        "id": str(issue.project_id),
        "identifier": issue.project.identifier,
        "name": issue.project.name,
    }
    payload["state"] = (
        {"id": str(state.id), "name": state.name, "color": state.color, "group": state.group}
        if state is not None
        else None
    )
    payload["priority"] = issue.priority
    payload["target_date"] = issue.target_date.isoformat() if issue.target_date else None
    payload["current_decision_id"] = (
        str(link.current_assignment_decision_id) if link.current_assignment_decision_id else None
    )

    is_current_executor = user_id is not None and link.primary_executor_id == user_id
    payload["permissions"] = {
        "can_claim": (link.routing_state in WAITING_STATES and bool(self_claim_allowed) and bool(viewer["is_member"])),
        "can_assign": bool(viewer["is_admin"] or viewer["is_coordinator"]),
        "can_return": (
            link.routing_state == RoutingState.ASSIGNED
            and bool(viewer["is_admin"] or viewer["is_coordinator"] or is_current_executor)
        ),
    }
    return payload


class IssueClaimEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``POST .../organizational-unit/claim/`` — take a queued item for yourself.

    @description Gated the way the assign-automatically button is: Member or
    Admin of the *project* (``allow_permission``), not area coordination —
    self-claiming is something any eligible project member does. Whether this
    particular person may hold this particular item is the service's call
    (I4), and whether self-claim is even allowed here is the effective policy's.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")
        if link_for_issue(issue_id, slug=slug, project_id=project_id) is None:
            return orca_not_found("ORG_WORK_ITEM_HAS_NO_UNIT")

        try:
            result = claim(issue, request.user)
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status)

        return Response(_routing_data(result.link), status=status.HTTP_200_OK)


class IssueReturnEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``POST .../organizational-unit/return/`` — put an assigned item back in the queue.

    @description The one route in this module whose permission is not a fixed
    tie: the coordinator and a workspace admin may always do this, and so may
    whoever currently holds the item — letting go of your own work needs
    nobody's permission. Nobody else may return somebody else's item.
    """

    def post(self, request, slug, project_id, issue_id):
        link = link_for_issue(issue_id, slug=slug, project_id=project_id)
        if link is None:
            return orca_not_found("ORG_WORK_ITEM_HAS_NO_UNIT")

        unit = link.organizational_unit
        is_current_executor = link.primary_executor_id == request.user.id
        if not (
            is_workspace_admin(request.user, unit.workspace_id)
            or is_unit_coordinator(request.user, unit)
            or is_current_executor
        ):
            return orca_error("ORG_NOT_THE_EXECUTOR", status.HTTP_403_FORBIDDEN)

        try:
            result = return_to_queue(
                link.issue,
                actor=request.user,
                reason=request.data.get("reason", ""),
                expected_decision_id=request.data.get("expected_decision_id"),
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status)

        return Response(_routing_data(result.link), status=status.HTTP_200_OK)


class IssueReassignEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``POST .../organizational-unit/reassign/`` — hand the item to somebody else.

    @description Coordinator-only: choosing who does the area's work is what
    coordinating it means. ``expected_decision_id`` is this layer's If-Match,
    checked by the service under the item's row lock (RFC §8.1).
    """

    @allow_issue_unit_role([UNIT_COORDINATOR], error_code="ORG_NOT_UNIT_COORDINATOR")
    def post(self, request, slug, project_id, issue_id):
        link = request.organizational_unit_link
        executor_id = request.data.get("executor_id")
        if not executor_id:
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE")

        try:
            result = reassign(
                link.issue,
                executor_id,
                actor=request.user,
                reason=request.data.get("reason", ""),
                expected_decision_id=request.data.get("expected_decision_id"),
                trigger=DecisionTrigger.UI_COORDINATOR,
            )
        except (OrcaDomainError, ValueError) as exc:
            if isinstance(exc, OrcaDomainError):
                return orca_error(exc.error_code, exc.http_status)
            # A malformed executor_id reaches the ORM as a ValueError rather
            # than a domain error; it means the same thing an ineligible
            # executor does, so it answers with the same code.
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE")

        return Response(_routing_data(result.link), status=status.HTTP_200_OK)


class IssueTransferEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``POST .../organizational-unit/transfer/`` — move responsibility to another area.

    @description Gated by coordination of the *origin* area: moving work away
    from an area is that area's coordinator's call, not the destination's.
    """

    @allow_issue_unit_role([UNIT_COORDINATOR], error_code="ORG_NOT_UNIT_COORDINATOR")
    def post(self, request, slug, project_id, issue_id):
        link = request.organizational_unit_link
        to_unit = OrganizationalUnit.objects.filter(
            pk=request.data.get("unit_id"), workspace_id=link.workspace_id
        ).first()
        if to_unit is None:
            return orca_error("ORG_UNIT_NOT_IN_WORKSPACE")

        try:
            transfer_unit(
                link.issue,
                to_unit,
                actor=request.user,
                source=ResponsibilitySource.UI,
                reason=request.data.get("reason", ""),
                trigger=DecisionTrigger.UI_COORDINATOR,
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status)

        updated_link = link_for_issue(issue_id, slug=slug, project_id=project_id)
        return Response(_routing_data(updated_link), status=status.HTTP_200_OK)


class OrganizationalUnitQueueEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``GET organizational-units/<unit_id>/queue/`` — the coordinator's inbox.

    @description The same rows and order the public queue reads
    (``queue_queryset``), because "what is waiting" must not depend on which
    door the caller came through — plus what only a screen needs. ``viewer``
    rides alongside ``results`` so the interface renders its own chrome
    (show the "Assign" column at all, for instance) without a second request.
    """

    use_read_replica = True

    @allow_unit_role([UNIT_MEMBER, UNIT_COORDINATOR])
    def get(self, request, slug, unit_id):
        unit = request.organizational_unit

        routing_state = request.query_params.get("routing_state")
        if routing_state and routing_state not in QUEUE_STATE_CHOICES:
            return orca_error("ORG_INVALID_ROUTING_TRANSITION")

        now = timezone.now()
        queryset = queue_queryset(
            unit,
            routing_state=routing_state,
            overdue=_tri_state(request.query_params.get("overdue")),
            project_id=request.query_params.get("project"),
            now=now,
            visible_project_ids=readable_project_ids(request.user, unit),
        ).select_related("issue__state", "issue__project")

        executor_id = request.query_params.get("executor")
        if executor_id:
            queryset = queryset.filter(primary_executor_id=executor_id)

        viewer = viewer_standing(request.user, unit)
        self_claim_cache = {}

        def on_results(rows):
            results = []
            for row in rows:
                if row.project_id not in self_claim_cache:
                    # Resolved once per project per page (M6), not once per
                    # row: a page of forty items in one project would
                    # otherwise re-resolve the same policy forty times.
                    self_claim_cache[row.project_id] = (
                        AssignmentMode.SELF_CLAIM.value in resolve_policy(unit, row.project_id).allowed_modes
                    )
                results.append(
                    _internal_queue_row(
                        row,
                        now=now,
                        viewer=viewer,
                        user_id=request.user.id,
                        self_claim_allowed=self_claim_cache[row.project_id],
                    )
                )
            return results

        response = self.paginate(request=request, queryset=queryset, on_results=on_results)
        response.data["viewer"] = viewer
        return response


class OrganizationalUnitDecisionsEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``GET organizational-units/<unit_id>/decisions/`` — the area's allocation log.

    @description Coordinator-only: the log names who was and was not chosen
    for every item, which is the coordinator's business more than a
    workspace-wide read. ``supersedes`` rides one level expanded so a
    coordinator can see what a reassignment overturned without a second call.
    """

    use_read_replica = True

    @allow_unit_role([UNIT_COORDINATOR])
    def get(self, request, slug, unit_id):
        unit = request.organizational_unit
        queryset = (
            AssignmentDecision.objects.filter(organizational_unit=unit)
            .select_related("issue", "issue__project", "supersedes")
            .order_by("-created_at")
        )
        issue_id = request.query_params.get("issue")
        if issue_id:
            queryset = queryset.filter(issue_id=issue_id)

        return self.paginate(
            request=request,
            queryset=queryset,
            on_results=lambda rows: AssignmentDecisionDetailSerializer(rows, many=True).data,
        )


class OrganizationalUnitCoordinatorViewSet(OrganizationalUnitFeatureMixin, BaseViewSet):
    """
    CRUD for who coordinates an area.

    @description Reads are open to anybody with standing in the area (member,
    coordinator, or admin) — knowing who to ask is not privileged. Writes are
    workspace-Admin-only: naming a coordinator is an authorization decision
    (it can reach every project the area covers, M3), the same reasoning that
    keeps unit membership itself Admin-only.
    """

    serializer_class = OrganizationalUnitCoordinatorSerializer
    model = OrganizationalUnitCoordinator

    def get_unit(self, slug, unit_id):
        return OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()

    @allow_unit_role([UNIT_MEMBER, UNIT_COORDINATOR])
    def list(self, request, slug, unit_id):
        unit = request.organizational_unit
        coordinators = OrganizationalUnitCoordinator.objects.filter(
            organizational_unit=unit, is_active=True
        ).select_related("workspace_member", "workspace_member__member")
        serializer = OrganizationalUnitCoordinatorSerializer(coordinators, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug, unit_id):
        unit = self.get_unit(slug, unit_id)
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        # Accept either id the caller might reasonably have on hand: the
        # WorkspaceMember row's own id, or the person's user id (documented in
        # the RFC precisely because both are plausible here).
        candidate_id = request.data.get("workspace_member_id") or request.data.get("member_id")
        if not candidate_id:
            return orca_error("ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE")

        workspace_member = WorkspaceMember.objects.filter(
            pk=candidate_id, workspace_id=unit.workspace_id, is_active=True
        ).first()
        if workspace_member is None:
            workspace_member = WorkspaceMember.objects.filter(
                member_id=candidate_id, workspace_id=unit.workspace_id, is_active=True
            ).first()
        if workspace_member is None:
            return orca_error("ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE")

        # M4: a Guest promoted to coordinator would be the workspace cap
        # overriding the intent rather than honoring it — refused outright
        # rather than silently capped, the same spirit as I7.
        if workspace_member.role not in COORDINATOR_ELIGIBLE_ROLES:
            return orca_error("ORG_COORDINATOR_MUST_BE_MEMBER")

        existing = OrganizationalUnitCoordinator.objects.filter(
            organizational_unit=unit, workspace_member=workspace_member
        ).first()
        if existing is not None and existing.is_active:
            return orca_error("ORG_COORDINATOR_ALREADY_SET", status.HTTP_409_CONFLICT)

        with transaction.atomic():
            if existing is not None:
                existing.is_active = True
                existing.save()
                coordinator = existing
            else:
                coordinator = OrganizationalUnitCoordinator.objects.create(
                    organizational_unit=unit, workspace_member=workspace_member, workspace_id=unit.workspace_id
                )
            # Small fan-out (one member, this area's projects) stays inline so
            # the response reflects the access this call just granted.
            reconcile_coordinator(coordinator)

        return Response(OrganizationalUnitCoordinatorSerializer(coordinator).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, unit_id, pk):
        coordinator = OrganizationalUnitCoordinator.objects.filter(
            pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
        ).first()
        if coordinator is None:
            return orca_not_found("ORG_COORDINATOR_NOT_FOUND")

        # Deactivate then reconcile synchronously, same order the membership
        # viewset uses: the reconciler has to see the coordinator as inactive
        # while deciding what to withdraw, all inside one transaction so a
        # failure never strands the withdrawn access.
        with transaction.atomic():
            coordinator.is_active = False
            coordinator.save()
            reconcile_coordinator(coordinator, force_sync=True)
            coordinator.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _routing_data(link):
    """The response body every action route in this module answers with."""
    return IssueRoutingSerializer(link).data


__all__ = [
    "IssueClaimEndpoint",
    "IssueReassignEndpoint",
    "IssueReturnEndpoint",
    "IssueTransferEndpoint",
    "OrganizationalUnitCoordinatorViewSet",
    "OrganizationalUnitDecisionsEndpoint",
    "OrganizationalUnitQueueEndpoint",
]
