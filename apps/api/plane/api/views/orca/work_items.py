# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Creating and moving work from outside Plane.

One composed call does what an integration actually wants: make the work item,
tie it to the record that caused it, give it to the area that owns that kind
of work, and let the area's policy decide who does it — in one transaction,
so there is no state where the work item exists and nobody knows whose it is.

Two orderings are deliberate and easy to undo by accident. The idempotency
receipt is claimed **before** the transaction, so a rollback cannot erase the
evidence that the call happened — otherwise the retry finds a clean slate and
does the work twice. The native activity and webhooks fire **after** commit,
because a webhook announcing a work item that was rolled back is worse than a
late webhook.
"""

# Python imports
import json
import logging

# Django imports
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.api.serializers import IssueSerializer
from plane.api.serializers.orca import (
    CompleteStepSerializer,
    ReassignSerializer,
    TransferSerializer,
    WorkItemAutomationSerializer,
)
from plane.app.permissions import ProjectEntityPermission
from plane.app.services.orca import (
    OrcaDomainError,
    attach_to_process,
    refresh_instance_status,
    complete_step,
    instance_progress,
    process_projection_enabled,
    reassign,
    resolve_policy,
    return_to_queue,
    set_responsibility,
    transfer_unit,
)
from plane.app.services.orca.automation_operation import (
    begin_operation,
    complete_operation,
    fail_operation,
)
from plane.app.services.orca.errors import (
    ExternalBindingConflict,
    IdempotencyKeyRequired,
    IfMatchRequired,
    UnitNotFound,
    WorkItemNotFound,
)
from plane.bgtasks.issue_activities_task import issue_activity
from plane.bgtasks.webhook_task import model_activity
from plane.utils.host import base_host
from plane.app.services.orca.service_level import record_service_level
from plane.db.models import (
    ExternalWorkItemBinding,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    ProcessInstanceItem,
    ProcessInstanceReference,
    Project,
    ServiceLevelSource,
)
from plane.utils.orca_error_codes import (
    ORCA_ERROR_CODES,
    ORCA_ERROR_MESSAGES,
    orca_error,
    orca_not_found,
)

from .base import OrcaPublicBaseAPIView

logger = logging.getLogger("plane.orca.public_api")

IDEMPOTENCY_HEADER = "Idempotency-Key"
IF_MATCH_HEADER = "If-Match"
REPLAY_HEADER = "Idempotent-Replay"


def error_body(code):
    """@returns: The standard Orca error body for a code, for the receipt."""
    return {
        "error": ORCA_ERROR_MESSAGES[code],
        "error_code": ORCA_ERROR_CODES[code],
        "error_message": code,
    }


class OrcaWorkItemMixin:
    """Header handling and the one response envelope these endpoints share."""

    def idempotency_key(self, request):
        """
        @description Read the key every mutation must carry.
        @raises IdempotencyKeyRequired: when the header is missing.
        """
        key = request.headers.get(IDEMPOTENCY_HEADER)
        if not key:
            raise IdempotencyKeyRequired("this request must carry an Idempotency-Key header")
        return key

    def replayed_response(self, handle):
        """@returns: The original answer, marked so the caller can tell."""
        response = Response(handle.snapshot, status=status.HTTP_200_OK)
        response[REPLAY_HEADER] = "true"
        return response

    def envelope(self, issue, link, decision, *, binding=None, binding_created=False, key="", replay=False):
        """
        @description The single shape every endpoint here answers with, so a
        client writes one parser and a replay is identical to what it replays.
        """
        executor = link.primary_executor if link else None
        return {
            "work_item": {
                "id": str(issue.id),
                "sequence_id": issue.sequence_id,
                "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
                "name": issue.name,
                "state": str(issue.state_id) if issue.state_id else None,
            },
            "binding": (
                {"source": binding.external_source, "id": binding.external_id, "created": binding_created}
                if binding
                else None
            ),
            "responsibility": (
                {
                    "unit": {"id": str(link.organizational_unit_id), "slug": link.organizational_unit.slug},
                    "routing_state": link.routing_state,
                    "queue_reason": link.queue_reason,
                    "primary_executor": (
                        {"id": str(executor.id), "email": executor.email, "display_name": executor.display_name}
                        if executor
                        else None
                    ),
                    "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
                }
                if link
                else None
            ),
            "decision": (
                {
                    "id": str(decision.id),
                    "requested_mode": decision.requested_mode,
                    "effective_mode": decision.effective_mode,
                    "policy_source": decision.policy_source,
                    "policy_version": decision.policy_version,
                    "algorithm_version": decision.algorithm_version,
                    "outcome": decision.outcome,
                }
                if decision
                else None
            ),
            "operation": {"idempotency_key": key, "replay": replay},
        }

    def routing_of(self, issue):
        """@returns: The work item's area link, with what the envelope needs."""
        return (
            IssueOrganizationalUnit.objects.select_related("organizational_unit", "primary_executor")
            .filter(issue=issue)
            .first()
        )

    def find_unit(self, workspace, slug_or_id):
        """
        @description Areas are addressed by slug in this API — an integration
        should not have to store Plane's ids to talk about "compliance".
        @raises UnitNotFound
        """
        unit = OrganizationalUnit.objects.filter(workspace=workspace, slug=slug_or_id, is_active=True).first()
        if unit is None:
            raise UnitNotFound(f"no active area '{slug_or_id}' in this workspace")
        return unit

    def find_issue(self, slug, project_id, issue_id):
        """@raises WorkItemNotFound"""
        issue = (
            Issue.objects.select_related("project", "workspace")
            .filter(pk=issue_id, project_id=project_id, workspace__slug=slug)
            .first()
        )
        if issue is None:
            raise WorkItemNotFound("no such work item in this project")
        return issue

    def run_operation(self, request, workspace, key, operation_type, work):
        """
        @description Claim the key, run the work, and make sure the receipt
        ends in a resting state whatever happens.
        @param work: callable taking the handle and returning ``(body, status)``.
        @returns: A DRF ``Response``.
        """
        try:
            handle = begin_operation(workspace, self.current_api_token(request), key, operation_type, request.data)
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        if handle.replayed:
            return self.replayed_response(handle)

        try:
            with handle:
                body, http_status = work(handle)
        except OrcaDomainError as error:
            fail_operation(handle, error_code=error.error_code, response=error_body(error.error_code))
            return orca_error(error.error_code, error.http_status)

        return Response(body, status=http_status)


class OrcaWorkItemListCreateEndpoint(OrcaWorkItemMixin, OrcaPublicBaseAPIView):
    """
    Create a work item with an area already responsible for it.

    @description Binding, work item, responsibility and allocation in one
    transaction. A retry with the same ``Idempotency-Key`` gets the first
    call's answer instead of a second work item.
    """

    permission_classes = [ProjectEntityPermission]

    def post(self, request, slug, project_id):
        try:
            key = self.idempotency_key(request)
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        serializer = WorkItemAutomationSerializer(data=request.data)
        if not serializer.is_valid():
            if self._tried_to_assign(serializer.errors):
                # The specific refusal, because "invalid payload" would send
                # the caller looking in the wrong place.
                return orca_error("ORG_ASSIGNEES_NOT_ALLOWED_HERE")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        payload = serializer.validated_data
        if payload.get("process"):
            # Phase 4 turns this on. Refused rather than ignored: an
            # orchestrator that believed it registered a process step would
            # build the rest of the run on that belief.
            return orca_error("ORG_PROCESS_PROJECTION_DISABLED")

        project = Project.objects.select_related("workspace").filter(pk=project_id, workspace__slug=slug).first()
        if project is None:
            return orca_error("ORG_UNIT_PROJECT_NOT_IN_WORKSPACE", status.HTTP_404_NOT_FOUND)

        return self.run_operation(
            request,
            project.workspace,
            key,
            "create_work_item",
            lambda handle: self._create(request, slug, project, payload, key, handle),
        )

    def _tried_to_assign(self, errors):
        """@returns: Whether the caller put assignees on the work item block."""
        forbidden = WorkItemAutomationSerializer.FORBIDDEN_WORK_ITEM_FIELDS
        blob = str(errors)
        return any(field in blob for field in forbidden)

    def _create(self, request, slug, project, payload, key, handle):
        """
        @description The transaction, in the order the contract fixes.
        @returns: ``(body, http status)``.
        """
        external = payload["external"]
        responsibility = payload["responsibility"]
        assignment = responsibility.get("assignment") or {}

        with transaction.atomic():
            binding = (
                ExternalWorkItemBinding.objects.select_for_update()
                .filter(
                    workspace=project.workspace,
                    external_source=external["source"],
                    external_id=external["id"],
                )
                .select_related("issue")
                .first()
            )

            created = False
            if binding is not None:
                # The same external record, seen again — reuse its work item,
                # unless it lives in another project, which means two
                # integrations are fighting over one reference.
                if binding.issue.project_id != project.id:
                    raise ExternalBindingConflict(
                        "this external reference already points at a work item in another project",
                        issue_id=str(binding.issue_id),
                    )
                issue = Issue.objects.select_related("project").get(pk=binding.issue_id)
            else:
                issue = self._create_issue(project, payload["work_item"], external)
                binding = ExternalWorkItemBinding.objects.create(
                    workspace=project.workspace,
                    external_source=external["source"],
                    external_id=external["id"],
                    issue=issue,
                )
                created = True

            unit = self.find_unit(project.workspace, responsibility["unit"])
            result = set_responsibility(
                issue,
                unit,
                actor=request.user,
                source="public_api",
                requested_mode=self._requested_mode(assignment),
                explicit_executor=assignment.get("primary_executor"),
                collaborators=assignment.get("collaborators") or (),
                trigger="public_api",
                assignment_due_at=responsibility.get("assignment_due_at"),
                reason=responsibility.get("reason", ""),
            )

            # After responsibility, so a step always has an area: the read
            # endpoint reports a run's progress from its work items' states,
            # and a step nobody owns never reaches one.
            process_item = None
            if payload.get("process"):
                process_item = attach_to_process(issue, payload["process"])
                record_service_level(
                    issue,
                    assignment_due_at=responsibility.get("assignment_due_at"),
                    completion_due_at=responsibility.get("completion_due_at"),
                    source=ServiceLevelSource.PROCESS,
                    source_version=payload["process"]["template_version"],
                    changed_by=request.user,
                    reason=responsibility.get("reason", ""),
                )
            elif responsibility.get("completion_due_at") is not None:
                record_service_level(
                    issue,
                    assignment_due_at=responsibility.get("assignment_due_at"),
                    completion_due_at=responsibility["completion_due_at"],
                    source=ServiceLevelSource.MANUAL,
                    changed_by=request.user,
                    reason=responsibility.get("reason", ""),
                )

            body = self.envelope(
                issue,
                self.routing_of(issue),
                result.decision,
                binding=binding,
                binding_created=created,
                key=key,
                replay=False,
            )
            if process_item is not None:
                body["process"] = {
                    "instance_id": process_item.process_instance.external_instance_id,
                    "source": process_item.process_instance.external_source,
                    "template_name": process_item.process_instance.template_name,
                    "template_version": process_item.process_instance.template_version,
                    "step_key": process_item.step_key,
                    "completion_mode": process_item.completion_mode,
                }
            complete_operation(handle, issue=issue, response=body)

            if created:
                transaction.on_commit(lambda: self._track(request, project, issue))

        return body, (status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def _requested_mode(self, assignment):
        """@description ``default`` and ``explicit`` both mean "no named mode" to the service."""
        mode = assignment.get("mode")
        if not mode or mode in ("default", "explicit"):
            return None
        return mode

    def _create_issue(self, project, work_item, external):
        """
        @description Create through the native v1 serializer, so sequence ids,
        validation and activity behave exactly as for any other API client.
        Assignees are forced empty and the project default is not applied: who
        does this is the area's decision, taken a few lines later and recorded.
        """
        data = dict(work_item)
        data["assignees"] = []
        data.setdefault("external_source", external["source"])
        data.setdefault("external_id", external["id"])

        serializer = IssueSerializer(
            data=data,
            context={
                "project_id": project.id,
                "workspace_id": project.workspace_id,
                "default_assignee_id": None,
            },
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Issue.objects.select_related("project").get(pk=serializer.data["id"])

    def _track(self, request, project, issue):
        """
        @description The native creation activity and the native webhook, as
        the v1 endpoint records them.

        Both, not just the activity: a work item created here has to be
        indistinguishable from one created through `/api/v1/`, or an
        integration listening for `issue.created` silently misses every work
        item an automation makes — which is most of them.
        """
        issue_activity.delay(
            type="issue.activity.created",
            requested_data=json.dumps(request.data, cls=DjangoJSONEncoder),
            actor_id=str(request.user.id),
            issue_id=str(issue.id),
            project_id=str(project.id),
            current_instance=None,
            epoch=int(timezone.now().timestamp()),
            notification=True,
            origin=request.headers.get("origin", ""),
        )
        model_activity.delay(
            model_name="issue",
            model_id=str(issue.id),
            requested_data=request.data,
            current_instance=None,
            actor_id=request.user.id,
            slug=project.workspace.slug,
            origin=base_host(request=request, is_app=True),
        )


class OrcaWorkItemByExternalEndpoint(OrcaWorkItemMixin, OrcaPublicBaseAPIView):
    """
    Find a work item by the reference the caller knows it as.

    @description The read that makes retries unnecessary in the first place:
    an integration unsure whether its last call landed asks here instead of
    creating again.
    """

    def get(self, request, slug, source, external_id):
        binding = (
            ExternalWorkItemBinding.objects.select_related("issue", "issue__project")
            .filter(workspace__slug=slug, external_source=source, external_id=external_id)
            .first()
        )
        if binding is None:
            return orca_error("ORG_WORK_ITEM_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        issue = binding.issue
        link = self.routing_of(issue)
        decision = link.current_assignment_decision if link else None
        return Response(
            self.envelope(issue, link, decision, binding=binding, binding_created=False),
            status=status.HTTP_200_OK,
        )


class OrcaWorkItemReassignEndpoint(OrcaWorkItemMixin, OrcaPublicBaseAPIView):
    """
    Move a work item to somebody else, or back to the area's queue.

    @description Requires ``If-Match`` with the decision the caller believes
    is current. An automation acting on a view of the queue that has since
    moved — a coordinator reassigned it two seconds ago — is refused rather
    than allowed to undo a person's decision silently.
    """

    permission_classes = [ProjectEntityPermission]

    def post(self, request, slug, project_id, issue_id):
        try:
            key = self.idempotency_key(request)
            expected = request.headers.get(IF_MATCH_HEADER)
            if not expected:
                raise IfMatchRequired("this request must carry an If-Match header with the current decision id")
            issue = self.find_issue(slug, project_id, issue_id)
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        serializer = ReassignSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        payload = serializer.validated_data

        def work(handle):
            with transaction.atomic():
                if payload.get("return_to_queue"):
                    link = self.routing_of(issue)
                    if link and str(link.current_assignment_decision_id) != str(expected):
                        from plane.app.services.orca.errors import DecisionStale

                        raise DecisionStale(
                            "this work item has moved since you read it",
                            current_decision_id=str(link.current_assignment_decision_id),
                        )
                    result = return_to_queue(
                        issue, actor=request.user, reason=payload.get("reason", ""), trigger="public_api"
                    )
                else:
                    result = reassign(
                        issue,
                        payload["primary_executor"],
                        actor=request.user,
                        reason=payload.get("reason", ""),
                        expected_decision_id=expected,
                    )

                body = self.envelope(issue, self.routing_of(issue), result.decision, key=key, replay=False)
                complete_operation(handle, issue=issue, response=body)
            return body, status.HTTP_200_OK

        return self.run_operation(request, issue.workspace, key, "reassign", work)


class OrcaWorkItemTransferEndpoint(OrcaWorkItemMixin, OrcaPublicBaseAPIView):
    """Hand a work item to a different area."""

    permission_classes = [ProjectEntityPermission]

    def post(self, request, slug, project_id, issue_id):
        try:
            key = self.idempotency_key(request)
            issue = self.find_issue(slug, project_id, issue_id)
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        serializer = TransferSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        payload = serializer.validated_data

        def work(handle):
            with transaction.atomic():
                unit = self.find_unit(issue.workspace, payload["unit"])
                result = transfer_unit(
                    issue,
                    unit,
                    actor=request.user,
                    source="public_api",
                    reason=payload.get("reason", ""),
                    trigger="public_api",
                )
                decision = result.allocation.decision if result.allocation else None
                body = self.envelope(issue, self.routing_of(issue), decision, key=key, replay=False)
                complete_operation(handle, issue=issue, response=body)
            return body, status.HTTP_200_OK

        return self.run_operation(request, issue.workspace, key, "transfer_unit", work)


class OrcaWorkItemCompleteEndpoint(OrcaWorkItemMixin, OrcaPublicBaseAPIView):
    """
    An outside system saying a step is done.

    @description Only for work items that are steps of a process run, and only
    in the way the step's template allows. A step whose ``completion_mode`` is
    ``manual`` is refused — the whole point of that setting is that an API key
    does not get to decide it.
    """

    permission_classes = [ProjectEntityPermission]

    def post(self, request, slug, project_id, issue_id):
        if not process_projection_enabled():
            return orca_error("ORG_PROCESS_PROJECTION_DISABLED")

        try:
            key = self.idempotency_key(request)
            issue = self.find_issue(slug, project_id, issue_id)
        except OrcaDomainError as error:
            return orca_error(error.error_code, error.http_status)

        serializer = CompleteStepSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        payload = serializer.validated_data

        def work(handle):
            with transaction.atomic():
                link = self.routing_of(issue)
                # The area's policy names where a closed step lands, when it
                # has an opinion; without an area there is nothing to ask.
                policy = None
                if link is not None:
                    policy = resolve_policy(link.organizational_unit, issue.project_id).policy

                event, item = complete_step(
                    issue,
                    source=payload.get("source") or "public_api",
                    event_id=payload.get("event_id", ""),
                    rule_version=payload.get("rule_version", ""),
                    evidence=payload.get("evidence") or {},
                    actor=request.user,
                    policy=policy,
                )
                issue.refresh_from_db()
                body = {
                    "work_item": {
                        "id": str(issue.id),
                        "sequence_id": issue.sequence_id,
                        "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
                        "state": str(issue.state_id) if issue.state_id else None,
                    },
                    "process": {
                        "instance_id": item.process_instance.external_instance_id,
                        "source": item.process_instance.external_source,
                        "step_key": item.step_key,
                        "completion_mode": item.completion_mode,
                        "status": item.process_instance.status,
                        "progress": instance_progress(item.process_instance),
                    },
                    "completion": {
                        "id": str(event.id),
                        "source": event.source,
                        "event_id": event.event_id,
                        "rule_version": event.rule_version,
                        "recorded_at": event.created_at.isoformat(),
                    },
                    "operation": {"idempotency_key": key, "replay": False},
                }
                complete_operation(handle, issue=issue, response=body)
            return body, status.HTTP_200_OK

        return self.run_operation(request, issue.workspace, key, "complete_step", work)


class OrcaProcessInstanceEndpoint(OrcaWorkItemMixin, OrcaPublicBaseAPIView):
    """
    One run of a process, as it stands.

    @description What the orchestrator needs to decide what to do next after a
    restart: every step, its native state, where it is in its area's queue, who
    is executing it, and what was promised. Read from the work items rather
    than from a counter kept up to date as steps close, so a step reopened by
    hand in the app is reflected here — the app is allowed to be right.

    Not marked ``use_read_replica`` even though it is a read: deriving the
    status can write it back, and a view that sometimes writes has no business
    pointing its reads at a replica.
    """

    def get(self, request, slug, source, instance_id):
        if not process_projection_enabled():
            return orca_error("ORG_PROCESS_PROJECTION_DISABLED")

        instance = ProcessInstanceReference.objects.filter(
            workspace__slug=slug, external_source=source, external_instance_id=instance_id
        ).first()
        if instance is None:
            return orca_not_found("ORG_WORK_ITEM_NOT_FOUND")

        items = (
            ProcessInstanceItem.objects.filter(process_instance=instance)
            .select_related("issue", "issue__project", "issue__state", "issue__orca_service_level")
            .order_by("created_at")
        )
        routing_by_issue = {
            link.issue_id: link
            for link in IssueOrganizationalUnit.objects.filter(
                issue_id__in=[item.issue_id for item in items]
            ).select_related("organizational_unit", "primary_executor")
        }

        # Derived here rather than trusted from the column: a run whose steps
        # were all closed in the app should read as completed even though no
        # `complete/` call ever arrived.
        refresh_instance_status(instance)
        instance.refresh_from_db()

        return Response(
            {
                "instance": {
                    "source": instance.external_source,
                    "instance_id": instance.external_instance_id,
                    "template_name": instance.template_name,
                    "template_version": instance.template_version,
                    "status": instance.status,
                    "started_at": instance.started_at.isoformat() if instance.started_at else None,
                    "completed_at": instance.completed_at.isoformat() if instance.completed_at else None,
                    "progress": instance_progress(instance),
                },
                "items": [self._item(item, routing_by_issue.get(item.issue_id)) for item in items],
            },
            status=status.HTTP_200_OK,
        )

    def _item(self, item, link):
        issue = item.issue
        service_level = getattr(issue, "orca_service_level", None)
        executor = link.primary_executor if link else None
        return {
            "step_key": item.step_key,
            "completion_mode": item.completion_mode,
            "work_item": {
                "id": str(issue.id),
                "sequence_id": issue.sequence_id,
                "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
                "name": issue.name,
                "project_id": str(issue.project_id),
                "state": {"id": str(issue.state_id), "group": issue.state.group} if issue.state_id else None,
            },
            "responsibility": {
                "unit": link.organizational_unit.slug if link else None,
                "routing_state": link.routing_state if link else None,
                "queue_reason": link.queue_reason if link else None,
                "primary_executor": (
                    {"id": str(executor.id), "email": executor.email, "display_name": executor.display_name}
                    if executor
                    else None
                ),
            },
            "service_level": (
                {
                    "assignment_due_at": (
                        service_level.assignment_due_at.isoformat() if service_level.assignment_due_at else None
                    ),
                    "completion_due_at": (
                        service_level.completion_due_at.isoformat() if service_level.completion_due_at else None
                    ),
                    "original_completion_due_at": (
                        service_level.original_completion_due_at.isoformat()
                        if service_level.original_completion_due_at
                        else None
                    ),
                    "source": service_level.source,
                    "source_version": service_level.source_version,
                }
                if service_level
                else None
            ),
        }
