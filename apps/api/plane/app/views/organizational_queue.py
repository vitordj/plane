# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The area's queue, as the people who run it use it.

Everything here is a thin translation between HTTP and the assignment service:
the service decides, records and locks; these views only say who is allowed to
ask and what the answer looks like. Any rule that lives here instead of there
would apply to the app and not to the API, which is how two callers end up
with two different behaviours.
"""

# Django imports
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE_COORDINATOR, ROLE_UNIT_MEMBER, allow_unit_role, is_workspace_admin
from plane.app.permissions.base import ROLE, allow_permission
from plane.app.permissions.organizational_unit import is_unit_coordinator, is_unit_member, unit_roles_of
from plane.app.serializers import (
    AssignmentDecisionSerializer,
    AssignmentPolicySerializer,
    IssueRoutingSerializer,
)
from plane.app.services.orca import (
    OrcaDomainError,
    claim,
    rank_candidates,
    reassign,
    resolve_policy,
    return_to_queue,
    instance_progress,
    transfer_unit,
    unavailable_member_ids,
    unit_covers_project,
)
from plane.db.models import (
    AssignmentDecision,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitCoordinator,
    OrganizationalUnitProject,
    ProcessInstanceItem,
    User,
    WorkspaceMember,
)
from plane.db.models.organizational_unit import RoutingState
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView
from .organizational_unit import OrganizationalUnitFeatureMixin

# What a queue read returns when no filter narrows it: the work that is
# waiting, which is what somebody opening a queue came to see.
WAITING_STATES = [RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED]


class UnitQueueEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    What is waiting in one area, and what the reader may do about each row.

    @description The capabilities travel with the rows on purpose. The
    interface must not decide for itself whether somebody may claim a work
    item — it would have to reimplement the policy, and the two would drift.
    """

    use_read_replica = True

    @allow_unit_role([ROLE_COORDINATOR, ROLE_UNIT_MEMBER])
    def get(self, request, slug, unit_id):
        unit = OrganizationalUnit.objects.filter(pk=unit_id, workspace__slug=slug).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        links = (
            IssueOrganizationalUnit.objects.filter(organizational_unit=unit)
            .select_related("issue", "issue__project", "issue__state", "primary_executor")
            .order_by("queued_at", "created_at")
        )

        routing_state = request.query_params.get("routing_state")
        links = (
            links.filter(routing_state=routing_state)
            if routing_state
            else links.filter(routing_state__in=WAITING_STATES)
        )
        if request.query_params.get("project"):
            links = links.filter(project_id=request.query_params["project"])
        if request.query_params.get("executor"):
            links = links.filter(primary_executor_id=request.query_params["executor"])
        if request.query_params.get("overdue") == "true":
            links = links.filter(assignment_due_at__lt=timezone.now())

        roles = unit_roles_of(request.user, unit)
        is_admin = is_workspace_admin(request.user, unit.workspace_id)
        # Which executors are away, in one query rather than one per row. The
        # queue is where somebody notices that the person carrying a work item
        # is on holiday, and noticing is the whole point of showing it.
        away = self._away_executors(links, unit)
        # Which process run each work item belongs to, and how far along that
        # run is — so the interface can group four steps of one onboarding
        # together instead of showing them as four unrelated things.
        processes = self._process_by_issue(links)
        rows = [self._row(link, request.user, unit, roles, is_admin, away, processes) for link in links]

        # Overdue first: a queue sorted purely by age buries the thing that
        # somebody already promised would be done by now.
        rows.sort(key=lambda row: (not row["assignment_overdue"], row["queued_at"] or ""))
        return Response(rows, status=status.HTTP_200_OK)

    def _away_executors(self, links, unit) -> set:
        """
        @description The executors on this queue who are unavailable right now,
        as user ids. Empty when the feature is off, so the row simply never
        says anything about availability.
        @param links: The queue's links.
        @param unit: The area, for its workspace.
        @returns: A set of user ids.
        """
        executor_ids = {link.primary_executor_id for link in links if link.primary_executor_id}
        if not executor_ids:
            return set()

        member_to_user = dict(
            WorkspaceMember.objects.filter(
                workspace_id=unit.workspace_id, member_id__in=executor_ids, is_active=True
            ).values_list("id", "member_id")
        )
        away_member_ids = unavailable_member_ids(member_to_user.keys())
        return {member_to_user[member_id] for member_id in away_member_ids}

    def _process_by_issue(self, links) -> dict:
        """
        @description The process step each queued work item is, keyed by issue
        id, with its run's progress. Two queries for the whole queue rather
        than one per row, and an empty map when nothing here is part of a
        process — which is the ordinary case.
        @param links: The queue's links.
        @returns: ``{issue_id: {...}}``.
        """
        items = list(
            ProcessInstanceItem.objects.filter(issue_id__in=[link.issue_id for link in links]).select_related(
                "process_instance"
            )
        )
        if not items:
            return {}

        progress_by_instance = {
            item.process_instance_id: instance_progress(item.process_instance)
            for item in {item.process_instance_id: item for item in items}.values()
        }
        return {
            item.issue_id: {
                "instance_id": item.process_instance.external_instance_id,
                "source": item.process_instance.external_source,
                "template_name": item.process_instance.template_name,
                "step_key": item.step_key,
                "progress": progress_by_instance[item.process_instance_id],
            }
            for item in items
        }

    def _row(self, link, user, unit, roles, is_admin, away=frozenset(), processes=None):
        issue = link.issue
        executor = link.primary_executor
        now = timezone.now()
        waiting = link.routing_state in WAITING_STATES
        may_coordinate = is_admin or ROLE_COORDINATOR in roles
        effective_mode = resolve_policy(unit, link.project_id).effective_mode

        return {
            "id": str(link.id),
            "issue_id": str(issue.id),
            "sequence_id": issue.sequence_id,
            "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
            "name": issue.name,
            "project_id": str(link.project_id),
            "state": {"id": str(issue.state_id), "group": issue.state.group} if issue.state_id else None,
            "routing_state": link.routing_state,
            "queue_reason": link.queue_reason,
            "queued_at": link.queued_at.isoformat() if link.queued_at else None,
            "age_seconds": int((now - link.queued_at).total_seconds()) if link.queued_at else None,
            "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
            "assignment_overdue": bool(link.assignment_due_at and link.assignment_due_at < now),
            "target_date": issue.target_date.isoformat() if issue.target_date else None,
            "primary_executor": (
                {
                    "id": str(executor.id),
                    "display_name": executor.display_name,
                    "avatar_url": executor.avatar_url,
                    # False only when the feature is on and something says they
                    # are away — never "unknown", so the interface has one
                    # thing to render rather than three.
                    "is_available": executor.id not in away,
                }
                if executor
                else None
            ),
            "current_decision": str(link.current_assignment_decision_id)
            if link.current_assignment_decision_id
            else None,
            # The process run this is a step of, when it is one.
            "process": (processes or {}).get(link.issue_id),
            # What this reader may do with this row, decided here so the
            # interface never has to guess.
            "can_claim": bool(waiting and effective_mode == "self_claim" and ROLE_UNIT_MEMBER in roles)
            or bool(waiting and may_coordinate and ROLE_UNIT_MEMBER in roles),
            "can_assign": bool(may_coordinate),
            "can_return": bool(may_coordinate and link.routing_state == RoutingState.ASSIGNED)
            or bool(executor and executor.id == user.id),
            # Same answer as can_assign today, and still its own key: moving
            # work to another area is a different question from picking who
            # takes it here, and the interface should not have to assume the
            # two rules stay married.
            "can_transfer": bool(may_coordinate),
        }


class UnitQueueDecisionsEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Why the work in this area went where it went.

    @description Newest first, with what each decision replaced expanded one
    level — enough to answer "who moved this, and what did they move it from?"
    without walking the chain by hand.
    """

    use_read_replica = True

    @allow_unit_role([ROLE_COORDINATOR, ROLE_UNIT_MEMBER])
    def get(self, request, slug, unit_id):
        decisions = (
            AssignmentDecision.objects.filter(organizational_unit_id=unit_id)
            .select_related("supersedes")
            .order_by("-created_at")
        )
        if request.query_params.get("issue"):
            decisions = decisions.filter(issue_id=request.query_params["issue"])

        return self.paginate(
            request=request,
            queryset=decisions,
            on_results=lambda results: [
                {
                    **AssignmentDecisionSerializer(decision).data,
                    "supersedes": (
                        AssignmentDecisionSerializer(decision.supersedes).data if decision.supersedes_id else None
                    ),
                }
                for decision in results
            ],
        )


class IssueQueueActionEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    The four things somebody does to a work item in a queue.

    @description One view for claim, assign, return and transfer because they
    share everything except the service call: the same lookup, the same
    permission question, the same envelope, the same error translation.
    """

    def _issue(self, slug, project_id, issue_id):
        return Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()

    def _link(self, issue):
        return (
            IssueOrganizationalUnit.objects.select_related("organizational_unit", "primary_executor")
            .filter(issue=issue)
            .first()
        )

    def _respond(self, result):
        return Response(
            {
                "routing": IssueRoutingSerializer(result.link).data,
                "decision": AssignmentDecisionSerializer(result.decision).data if result.decision else None,
            },
            status=status.HTTP_200_OK,
        )


class IssueClaimEndpoint(IssueQueueActionEndpoint):
    """Take a queued work item for yourself."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = self._issue(slug, project_id, issue_id)
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        link = self._link(issue)
        if link is None:
            return orca_error("ORG_WORK_ITEM_HAS_NO_UNIT")

        # Claiming is for the area's own people. A project Admin who is not in
        # the area coordinates it instead — the difference matters, because
        # claiming means "I will do this".
        if not is_unit_member(request.user, link.organizational_unit):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        try:
            result = claim(issue, request.user, actor=request.user)
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)
        return self._respond(result)


class IssueAssignToEndpoint(IssueQueueActionEndpoint):
    """Give a queued work item to a member of the area."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = self._issue(slug, project_id, issue_id)
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        link = self._link(issue)
        if link is None:
            return orca_error("ORG_WORK_ITEM_HAS_NO_UNIT")

        unit = link.organizational_unit
        if not (is_unit_coordinator(request.user, unit) or is_workspace_admin(request.user, unit.workspace_id)):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        executor_id = request.data.get("primary_executor")
        if not executor_id:
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE")

        try:
            result = reassign(
                issue,
                executor_id,
                actor=request.user,
                reason=request.data.get("reason", ""),
                expected_decision_id=request.data.get("expected_decision_id"),
            )
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)
        return self._respond(result)


class IssueReturnToQueueEndpoint(IssueQueueActionEndpoint):
    """Hand a work item back to its area's queue."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = self._issue(slug, project_id, issue_id)
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        link = self._link(issue)
        if link is None:
            return orca_error("ORG_WORK_ITEM_HAS_NO_UNIT")

        unit = link.organizational_unit
        # The person carrying it may always put it down; otherwise it takes a
        # coordinator. Nobody else gets to decide somebody's work is not theirs.
        is_the_executor = link.primary_executor_id == request.user.id
        may_coordinate = is_unit_coordinator(request.user, unit) or is_workspace_admin(request.user, unit.workspace_id)
        if not (is_the_executor or may_coordinate):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        try:
            result = return_to_queue(
                issue,
                actor=request.user,
                reason=request.data.get("reason", ""),
                trigger="ui_coordinator" if may_coordinate and not is_the_executor else "return_to_queue",
            )
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)
        return self._respond(result)


class IssueTransferUnitEndpoint(IssueQueueActionEndpoint):
    """Move responsibility for a work item to another area."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = self._issue(slug, project_id, issue_id)
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        link = self._link(issue)
        if link is None:
            return orca_error("ORG_WORK_ITEM_HAS_NO_UNIT")

        unit = link.organizational_unit
        if not (is_unit_coordinator(request.user, unit) or is_workspace_admin(request.user, unit.workspace_id)):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        destination = OrganizationalUnit.objects.filter(
            pk=request.data.get("organizational_unit_id"), workspace_id=unit.workspace_id, is_active=True
        ).first()
        if destination is None:
            return orca_error("ORG_UNIT_NOT_IN_WORKSPACE")

        try:
            result = transfer_unit(
                issue,
                destination,
                actor=request.user,
                source="ui",
                reason=request.data.get("reason", ""),
                trigger="ui_coordinator",
            )
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)
        return self._respond(result.allocation)


class IssueCandidatesEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Who could take this work item, and how loaded each of them is.

    @description Backs the "assign to…" list. Showing the load is the point:
    a coordinator picking a name without it is guessing, and the ranking's
    own choice becomes something they can agree or disagree with.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        issue = Issue.objects.filter(pk=issue_id, project_id=project_id, workspace__slug=slug).first()
        if issue is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        link = IssueOrganizationalUnit.objects.select_related("organizational_unit").filter(issue=issue).first()
        if link is None:
            return orca_error("ORG_WORK_ITEM_HAS_NO_UNIT")

        resolution = resolve_policy(link.organizational_unit, link.project_id)
        ranked = rank_candidates(link.organizational_unit, link.project_id, resolution.policy)
        users = {
            str(user.id): user
            for user in User.objects.filter(id__in=[row["user_id"] for row in ranked.elected + ranked.excluded])
        }

        def describe(row):
            user = users.get(row["user_id"])
            return {
                **row,
                "display_name": user.display_name if user else None,
                "avatar_url": user.avatar_url if user else None,
            }

        return Response(
            {
                "effective_mode": resolution.effective_mode,
                "candidates": [describe(row) for row in ranked.elected],
                "excluded": [describe(row) for row in ranked.excluded],
            },
            status=status.HTTP_200_OK,
        )


class UnitPolicyWriteEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Set how an area assigns work — for all of it, or for one project.

    @description Admin only. A policy decides where every future work item in
    the area lands, which is a workspace-shaped decision rather than a
    coordinator-shaped one.
    """

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug, unit_id, pk=None):
        unit = OrganizationalUnit.objects.filter(pk=unit_id, workspace__slug=slug).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        unit_project = None
        if pk is not None:
            if not unit_covers_project(unit, pk):
                return orca_error("ORG_UNIT_NOT_COVERING_PROJECT")
            unit_project = OrganizationalUnitProject.objects.filter(organizational_unit=unit, project_id=pk).first()

        default_mode = request.data.get("default_mode", "manual")
        allowed_modes = request.data.get("allowed_modes") or [default_mode]
        if default_mode not in allowed_modes:
            return orca_error("ORG_ASSIGNMENT_MODE_NOT_ALLOWED")

        policy, _ = OrganizationalUnitAssignmentPolicy.objects.get_or_create(
            organizational_unit=unit,
            unit_project=unit_project,
            defaults={"workspace_id": unit.workspace_id},
        )
        policy.default_mode = default_mode
        policy.allowed_modes = allowed_modes
        policy.assignment_sla_seconds = request.data.get("assignment_sla_seconds")
        policy.max_open_items_per_member = request.data.get("max_open_items_per_member")
        policy.is_active = request.data.get("is_active", True)
        policy.save()

        return Response(AssignmentPolicySerializer(policy).data, status=status.HTTP_200_OK)


class UnitCoordinatorEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    Who runs this area's queue.

    @description Admin only, and a coordination is withdrawn rather than
    deleted: the access ledger has to be able to say why somebody had access
    last month.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id):
        coordinators = OrganizationalUnitCoordinator.objects.filter(
            organizational_unit_id=unit_id, is_active=True
        ).select_related("workspace_member", "workspace_member__member")
        return Response(
            [
                {
                    "id": str(coordinator.id),
                    "workspace_member": str(coordinator.workspace_member_id),
                    "display_name": coordinator.workspace_member.member.display_name,
                    "avatar_url": coordinator.workspace_member.member.avatar_url,
                }
                for coordinator in coordinators
            ],
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, unit_id):
        unit = OrganizationalUnit.objects.filter(pk=unit_id, workspace__slug=slug).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        member = WorkspaceMember.objects.filter(
            pk=request.data.get("workspace_member"), workspace_id=unit.workspace_id, is_active=True
        ).first()
        if member is None:
            return orca_error("ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE")

        coordinator, created = OrganizationalUnitCoordinator.objects.get_or_create(
            organizational_unit=unit,
            workspace_member=member,
            defaults={"workspace_id": unit.workspace_id},
        )
        if not created and not coordinator.is_active:
            coordinator.is_active = True
            coordinator.save(update_fields=["is_active"])

        # Coordinating an area comes with access to its projects; the
        # reconciler is the only thing allowed to write that.
        self._reconcile(unit, member)

        return Response({"id": str(coordinator.id)}, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, unit_id, pk):
        coordinator = OrganizationalUnitCoordinator.objects.filter(
            pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
        ).first()
        if coordinator is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        coordinator.is_active = False
        coordinator.save(update_fields=["is_active"])
        self._reconcile(coordinator.organizational_unit, coordinator.workspace_member)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _reconcile(self, unit, member):
        """@description Bring project access in line, for this person only."""
        from plane.app.services.orca import reconcile_access

        reconcile_access(unit.workspace_id, member_ids=[member.id])
