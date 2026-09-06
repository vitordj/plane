# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The area's queue and the coordinator's actions, served under ``/api/orca/``.

Phase 1 gave an automation the same six operations over ``/api/v1/orca/``.
This is the other half of the same domain: the surfaces a person uses inside
the app. They share the service layer on purpose — ``claim``, ``reassign``,
``return_to_queue``, ``suspend`` and ``transfer_unit`` are the same functions
the public API calls, with the same row lock, the same eligibility check at
decision time and the same append-only decision record (RFC §6.5, I5). What
differs is who may call them and what they are handed back:

* a **coordinator** runs the queue — hands work out, takes it back, moves it to
  another area, parks what is blocked, and reads the decision log;
* a **member** of the area claims work from the queue and hands back what they
  hold;
* a **workspace Admin** passes every check, because they can make themselves a
  coordinator in one request anyway (see ``permissions/organizational_unit``).

The project gate comes first on the item routes: ``allow_permission`` answers
whether the caller may see this work item at all, and only then does the area's
own role decide whether they may move it. A coordinator always passes the first
gate, because coordinating an area materializes a native ``ProjectMember`` on
every project it covers (item 2.1).
"""

# Django imports
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import (
    ROLE,
    UNIT_ROLE_COORDINATOR,
    UNIT_ROLE_LEAD,
    UNIT_ROLE_MEMBER,
    allow_permission,
    allow_unit_role,
    is_unit_coordinator,
    is_unit_member,
    is_workspace_admin,
    permission_denied,
    unit_capabilities,
)
from plane.app.serializers import (
    AssignmentDecisionSerializer,
    AssignmentPolicySerializer,
    IssueRoutingSerializer,
    OrganizationalUnitCoordinatorSerializer,
    QueueItemSerializer,
)
from plane.app.services.orca import (
    ALL_STATES,
    OrcaDomainError,
    claim,
    dispatch_reconciliation,
    project_ids_for_unit,
    rank_candidates,
    reassign,
    reconcile_coordinator,
    resolve_policy,
    return_to_queue,
    queue_queryset,
    suspend,
    transfer_unit,
)
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitCoordinator,
    OrganizationalUnitProject,
    QueueReason,
    ResponsibilitySource,
    RoutingState,
    User,
    WorkspaceMember,
)
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView, BaseViewSet
from .organizational_unit import OrganizationalUnitFeatureMixin

# States a queue filter may ask for, plus the "everything this area owns"
# escape hatch the coordinator's "in execution" section needs.
QUEUE_STATE_CHOICES = {state.value for state in RoutingState} | {ALL_STATES}

# How many rows one queue read returns. An area's inbox is a working list, not
# an archive: past a few hundred items the answer is a filter, not a longer
# page, and the interface says so rather than scrolling forever.
QUEUE_DEFAULT_LIMIT = 200
QUEUE_MAX_LIMIT = 500

# Fields of a policy the interface may write (item 2.2). ``version`` is the
# server's, and ``organizational_unit``/``unit_project`` are the row's identity.
POLICY_WRITABLE_FIELDS = ("default_mode", "allowed_modes", "assignment_sla_seconds", "max_open_items_per_member")


def _tri_state(value):
    """
    @description Read a query parameter that means yes, no, or "did not ask".
    @param value: The raw query parameter.
    @returns ``True``, ``False`` or ``None``. Anything unrecognized is ``None``
        rather than an error, so a filter the server does not understand widens
        the result instead of narrowing it to something nobody asked for.
    """
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in ("1", "true", "yes"):
        return True
    if lowered in ("0", "false", "no"):
        return False
    return None


class OrganizationalUnitQueueEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``GET .../organizational-units/{unit_id}/queue/``

    @description What the area has waiting, overdue first and oldest first
    (RFC §8.1). Filters: ``routing_state`` (one state, or ``all``),
    ``overdue=true|false``, ``project``, ``executor``.

    The response carries the caller's own capabilities alongside the rows, so
    the interface renders the actions it is allowed to render instead of
    re-deriving the area's permission rules in TypeScript — and so a person who
    loses the coordinator role sees the buttons disappear on the next read.
    """

    use_read_replica = True

    @allow_unit_role([UNIT_ROLE_COORDINATOR, UNIT_ROLE_MEMBER, UNIT_ROLE_LEAD])
    def get(self, request, slug, unit_id):
        unit = request.orca_unit

        routing_state = request.query_params.get("routing_state")
        if routing_state and routing_state not in QUEUE_STATE_CHOICES:
            return orca_error("ORG_INVALID_ROUTING_TRANSITION")

        try:
            limit = min(int(request.query_params.get("limit", QUEUE_DEFAULT_LIMIT)), QUEUE_MAX_LIMIT)
        except (TypeError, ValueError):
            limit = QUEUE_DEFAULT_LIMIT

        now = timezone.now()
        queryset = queue_queryset(
            unit,
            routing_state=routing_state,
            overdue=_tri_state(request.query_params.get("overdue")),
            project_id=request.query_params.get("project") or None,
            now=now,
        )
        executor_id = request.query_params.get("executor")
        if executor_id:
            queryset = queryset.filter(primary_executor_id=executor_id)

        rows = list(queryset.select_related("issue__project", "issue__state")[:limit])
        return Response(
            {
                "capabilities": unit_capabilities(request.user, unit),
                "items": QueueItemSerializer(rows, many=True, context={"now": now}).data,
            },
            status=status.HTTP_200_OK,
        )


class OrganizationalUnitDecisionsEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``GET .../organizational-units/{unit_id}/decisions/``

    @description The area's decision log, most recent first, with the decision
    each one replaced expanded one level (RFC §8.1). One level and no more: the
    chain is append-only and can be long, and what a reader asks about a
    reassignment is what it overturned, not the whole history of the item.

    Coordinators and workspace Admins only. The log names who decided what
    about whom, which is not a thing every member of an area should read.
    """

    use_read_replica = True

    @allow_unit_role([UNIT_ROLE_COORDINATOR])
    def get(self, request, slug, unit_id):
        queryset = (
            AssignmentDecision.objects.filter(organizational_unit_id=unit_id)
            .select_related("supersedes")
            .order_by("-created_at")
        )
        issue_id = request.query_params.get("issue")
        if issue_id:
            queryset = queryset.filter(issue_id=issue_id)

        def _expand(rows):
            payload = []
            for decision in rows:
                data = AssignmentDecisionSerializer(decision).data
                data["issue"] = str(decision.issue_id)
                data["supersedes_detail"] = (
                    AssignmentDecisionSerializer(decision.supersedes).data if decision.supersedes_id else None
                )
                payload.append(data)
            return payload

        return self.paginate(
            request=request, queryset=queryset, on_results=_expand, default_per_page=50, max_per_page=200
        )


class OrganizationalUnitCoordinatorViewSet(OrganizationalUnitFeatureMixin, BaseViewSet):
    """
    ``GET/POST/DELETE .../organizational-units/{unit_id}/coordinators/``

    @description Who runs this area's queue. Workspace Admin only, for the
    reason unit membership is: naming a coordinator grants that person native
    access to every project the area covers, so it is an authorization
    operation and stays where the other ones are.

    Both mutations end in a reconciliation, and both are inside a transaction
    with it: an interface that showed "coordinator added" while the access had
    not been materialized would be describing a state that does not exist.
    """

    serializer_class = OrganizationalUnitCoordinatorSerializer
    model = OrganizationalUnitCoordinator

    def get_unit(self, slug, unit_id):
        return OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def list(self, request, slug, unit_id):
        unit = self.get_unit(slug, unit_id)
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")
        coordinators = (
            OrganizationalUnitCoordinator.objects.filter(organizational_unit=unit)
            .select_related("workspace_member__member")
            .order_by("-created_at")
        )
        return Response(
            OrganizationalUnitCoordinatorSerializer(coordinators, many=True).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def create(self, request, slug, unit_id):
        unit = self.get_unit(slug, unit_id)
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        workspace_member = WorkspaceMember.objects.filter(
            workspace_id=unit.workspace_id,
            member_id=request.data.get("member_id"),
            is_active=True,
        ).first()
        if workspace_member is None:
            return orca_error("ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE")

        with transaction.atomic():
            coordinator, created = OrganizationalUnitCoordinator.objects.get_or_create(
                organizational_unit=unit,
                workspace_member=workspace_member,
                defaults={"workspace_id": unit.workspace_id, "created_by": request.user},
            )
            if not created and not coordinator.is_active:
                # Re-appointing somebody reuses the row rather than creating a
                # second one, so the ledger's grants keep pointing at one
                # coordination and the unique constraint stays satisfiable.
                coordinator.is_active = True
                coordinator.save(update_fields=["is_active", "updated_at"])
            reconcile_coordinator(coordinator)

        return Response(
            OrganizationalUnitCoordinatorSerializer(coordinator).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def destroy(self, request, slug, unit_id, pk):
        coordinator = OrganizationalUnitCoordinator.objects.filter(
            pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
        ).first()
        if coordinator is None:
            return orca_not_found("ORG_COORDINATOR_NOT_FOUND")

        with transaction.atomic():
            # Deactivated, not deleted: the grants that sourced this person's
            # access point at this row, and the reconciler has to see it become
            # inactive in order to withdraw exactly what it gave.
            coordinator.is_active = False
            coordinator.save(update_fields=["is_active", "updated_at"])
            dispatch_reconciliation(
                coordinator.workspace_id,
                member_ids=[coordinator.workspace_member_id],
                project_ids=project_ids_for_unit(coordinator.organizational_unit_id) or None,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class IssueQueueActionEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Shared plumbing for the five item actions.

    @description Each of them starts the same way: find the work item in this
    project, find the area responsible for it, and answer the same two
    not-found cases identically. Written once here so a new action cannot
    answer them differently.
    """

    def resolve(self, slug, project_id, issue_id):
        """
        @description Load the work item and its responsible area.
        @returns ``(issue, link, error_response)``; the error is ``None`` when
            both were found.
        """
        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()
        if issue is None:
            return None, None, orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        link = (
            IssueOrganizationalUnit.objects.filter(issue=issue)
            .select_related("organizational_unit", "current_assignment_decision")
            .first()
        )
        if link is None:
            return issue, None, orca_error("ORG_WORK_ITEM_HAS_NO_UNIT")
        return issue, link, None

    def coordinates(self, user, unit) -> bool:
        """@description Whether this person may hand out this area's work. @returns bool."""
        return is_unit_coordinator(user, unit) or is_workspace_admin(user, workspace_id=unit.workspace_id)

    def routing_response(self, link, code=status.HTTP_200_OK):
        """@description The item's routing state, which every action answers with. @returns Response."""
        link.refresh_from_db()
        return Response({"routing": IssueRoutingSerializer(link).data}, status=code)


class IssueOrganizationalUnitClaimEndpoint(IssueQueueActionEndpoint):
    """
    ``POST .../issues/{issue_id}/organizational-unit/claim/``

    @description Take a queued item for yourself. Members of the area only —
    including a workspace Admin who is one, and *excluding* one who is not:
    claiming is not an administrative act, it is saying "I am doing this", and
    the invariant that the executor belongs to the area (I4) is the service's
    to enforce either way.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue, link, error = self.resolve(slug, project_id, issue_id)
        if error is not None:
            return error

        if not is_unit_member(request.user, link.organizational_unit):
            return permission_denied()

        try:
            result = claim(issue, request.user, actor=request.user)
        except OrcaDomainError as exc:
            # The payload carries the winner of a contested claim, so the loser
            # is told who took it instead of having to reload to find out.
            return orca_error(exc.error_code, exc.http_status, **exc.payload)
        return self.routing_response(result.link)


class IssueOrganizationalUnitReassignEndpoint(IssueQueueActionEndpoint):
    """
    ``POST .../issues/{issue_id}/organizational-unit/reassign/``

    @description Hand the item to somebody else. Coordinator of the area or
    workspace Admin. ``expected_decision_id`` is optional here and required in
    the public API: a person is looking at the item they are moving, and the
    interface passes what it last read so two coordinators clicking at once do
    not silently overwrite each other.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue, link, error = self.resolve(slug, project_id, issue_id)
        if error is not None:
            return error

        if not self.coordinates(request.user, link.organizational_unit):
            return permission_denied()

        executor_id = request.data.get("executor_id") or request.data.get("assignee_id")
        if not executor_id:
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE")

        try:
            result = reassign(
                issue,
                executor_id,
                actor=request.user,
                reason=request.data.get("reason", ""),
                expected_decision_id=request.data.get("expected_decision_id"),
                trigger="ui_coordinator",
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status, **exc.payload)
        return self.routing_response(result.link)


class IssueOrganizationalUnitReturnEndpoint(IssueQueueActionEndpoint):
    """
    ``POST .../issues/{issue_id}/organizational-unit/return/``

    @description Put the item back in the area's queue. A coordinator may
    return anybody's; the executor may always return their own, which is the
    honest way for a person to say "not me" and is why this is not a
    coordinator-only route.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue, link, error = self.resolve(slug, project_id, issue_id)
        if error is not None:
            return error

        is_own = str(link.primary_executor_id) == str(request.user.id)
        if not (is_own or self.coordinates(request.user, link.organizational_unit)):
            return permission_denied()

        try:
            result = return_to_queue(
                issue,
                actor=request.user,
                reason=request.data.get("reason", ""),
                queue_reason=QueueReason.MANUALLY_RETURNED,
                expected_decision_id=request.data.get("expected_decision_id"),
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status, **exc.payload)
        return self.routing_response(result.link)


class IssueOrganizationalUnitSuspendEndpoint(IssueQueueActionEndpoint):
    """
    ``POST .../issues/{issue_id}/organizational-unit/suspend/``

    @description Park an item blocked on something outside the area, and take
    it back off the queue's clock (RFC §6.2). Coordinator or workspace Admin.
    Resuming is ``return/``, which is the same transition the state machine
    draws from ``suspended`` back to ``queued``.

    Not in the item list of plan 2.2, which named the six routes the automation
    API already had. Without it the two transitions the RFC's state machine
    gives the coordinator have no way to happen, and the interface's "Attention"
    section would show a state nothing could produce.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue, link, error = self.resolve(slug, project_id, issue_id)
        if error is not None:
            return error

        if not self.coordinates(request.user, link.organizational_unit):
            return permission_denied()

        try:
            result = suspend(
                issue,
                actor=request.user,
                reason=request.data.get("reason", ""),
                expected_decision_id=request.data.get("expected_decision_id"),
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status, **exc.payload)
        return self.routing_response(result.link)


class IssueOrganizationalUnitTransferEndpoint(IssueQueueActionEndpoint):
    """
    ``POST .../issues/{issue_id}/organizational-unit/transfer/``

    @description Move responsibility to another area (RFC §6.8). Coordinator of
    the area that holds it today, or workspace Admin — the receiving area does
    not get a veto, which is deliberate: work arrives at an area the way it
    arrives at a person, and the receiving coordinator's answer is to transfer
    it on rather than to refuse it in advance.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue, link, error = self.resolve(slug, project_id, issue_id)
        if error is not None:
            return error

        if not self.coordinates(request.user, link.organizational_unit):
            return permission_denied()

        to_unit = OrganizationalUnit.objects.filter(
            pk=request.data.get("organizational_unit_id"), workspace_id=issue.workspace_id
        ).first()
        if to_unit is None:
            return orca_error("ORG_UNIT_NOT_IN_WORKSPACE")

        try:
            transfer_unit(
                issue,
                to_unit,
                actor=request.user,
                source=ResponsibilitySource.UI,
                reason=request.data.get("reason", ""),
                trigger="ui_coordinator",
            )
        except OrcaDomainError as exc:
            return orca_error(exc.error_code, exc.http_status, **exc.payload)

        # A transfer answers with the event and the allocation the new area
        # made, and the allocation is what carries the link; re-reading the
        # link is what the caller wants either way, since the transfer may have
        # left the item queued in the area that just received it.
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        return self.routing_response(link)


class IssueOrganizationalUnitCandidatesEndpoint(IssueQueueActionEndpoint):
    """
    ``GET .../issues/{issue_id}/organizational-unit/candidates/``

    @description Who could take this item, in the order the allocator would
    pick them, with the load that put them there — what the "assign to…" modal
    lists. Excluded people come back too, with the reason, because "why is she
    not in this list?" is the first question a coordinator asks.

    Read-only and decides nothing: the ranking is recomputed at decision time
    inside the row lock, so a stale list can never assign the wrong person.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        issue, link, error = self.resolve(slug, project_id, issue_id)
        if error is not None:
            return error

        unit = link.organizational_unit
        if not (self.coordinates(request.user, unit) or is_unit_member(request.user, unit)):
            return permission_denied()

        resolution = resolve_policy(unit, link.project_id)
        ranked = rank_candidates(unit, link.project_id, policy=resolution)
        user_ids = [candidate.user_id for candidate in ranked.eligible] + [
            candidate.user_id for candidate in ranked.excluded
        ]
        people = {user.id: user for user in User.objects.filter(id__in=user_ids)}

        def _row(candidate, eligible):
            person = people.get(candidate.user_id)
            return {
                "user_id": str(candidate.user_id),
                "display_name": getattr(person, "display_name", ""),
                "avatar_url": getattr(person, "avatar_url", ""),
                "total_open": candidate.total_open,
                "unit_open": candidate.unit_open,
                "last_auto_at": candidate.last_auto_at.isoformat() if candidate.last_auto_at else None,
                "eligible": eligible,
                "excluded_reason": candidate.excluded_reason,
            }

        return Response(
            {
                "effective_mode": resolution.effective_mode,
                "candidates": [_row(candidate, True) for candidate in ranked.eligible]
                + [_row(candidate, False) for candidate in ranked.excluded],
            },
            status=status.HTTP_200_OK,
        )


class OrganizationalUnitPolicyWriteEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``PUT .../organizational-units/{unit_id}/policy/`` and
    ``PUT .../organizational-units/{unit_id}/projects/{project_id}/policy/``

    @description Write the area's assignment policy, or the one that overrides
    it for a single project (RFC §8.1). Workspace Admin only: a policy decides
    whether work is handed out automatically and how much of it one person can
    be given, which is a governance setting rather than a day-to-day one.

    The read stays on ``OrganizationalUnitPolicyEndpoint``, which answers with
    the *resolved* policy — project over area over fallback. This one writes a
    single stored row, and answers with it, so an admin sees what was saved
    rather than what the resolution would make of it.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug, unit_id, project_id=None):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=unit_id).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        unit_project = None
        if project_id is not None:
            unit_project = OrganizationalUnitProject.objects.filter(
                organizational_unit=unit, project_id=project_id
            ).first()
            if unit_project is None:
                return orca_error("ORG_UNIT_LINK_NOT_FOUND")

        payload, error = self._clean(request.data)
        if error is not None:
            return error

        with transaction.atomic():
            policy, _ = OrganizationalUnitAssignmentPolicy.objects.select_for_update().get_or_create(
                organizational_unit=unit,
                unit_project=unit_project,
                defaults={"workspace_id": unit.workspace_id, "created_by": request.user},
            )
            for field, value in payload.items():
                setattr(policy, field, value)
            # Checked against the row as it will be saved, not against the body
            # alone: a request that only moves the default has to be judged
            # against the allowed list already stored, or it could leave behind
            # a policy that rejects its own default on every allocation (I7).
            if policy.default_mode not in (policy.allowed_modes or []):
                return orca_error("ORG_POLICY_DEFAULT_MODE_NOT_ALLOWED")
            policy.is_active = bool(request.data.get("is_active", True))
            # ``version`` moves in the model's own ``save`` — it is frozen into
            # every decision this policy governs, so bumping it here as well
            # would count one write twice.
            policy.save()

        return Response(AssignmentPolicySerializer(policy).data, status=status.HTTP_200_OK)

    def _clean(self, data):
        """
        @description Validate the writable half of a policy.
        @param data: The request body.
        @returns ``(payload, error_response)``; the error is ``None`` when the
            body is usable.
        """
        modes = {mode.value for mode in AssignmentMode} - {AssignmentMode.EXPLICIT.value}
        payload = {}

        default_mode = data.get("default_mode")
        if default_mode is not None:
            if default_mode not in modes:
                return None, orca_error("ORG_POLICY_INVALID_MODE")
            payload["default_mode"] = default_mode

        allowed_modes = data.get("allowed_modes")
        if allowed_modes is not None:
            if not isinstance(allowed_modes, list) or not allowed_modes:
                return None, orca_error("ORG_POLICY_INVALID_MODE")
            if any(mode not in modes for mode in allowed_modes):
                return None, orca_error("ORG_POLICY_INVALID_MODE")
            payload["allowed_modes"] = list(dict.fromkeys(allowed_modes))

        for field in ("assignment_sla_seconds", "max_open_items_per_member"):
            if field not in data:
                continue
            value = data.get(field)
            if value in (None, ""):
                payload[field] = None
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                return None, orca_error("ORG_POLICY_INVALID_VALUE")
            if number <= 0:
                return None, orca_error("ORG_POLICY_INVALID_VALUE")
            payload[field] = number

        return payload, None
