# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The composed work-item operation, and the two ways to change it afterwards.

One call does what an integration would otherwise do in four: find or create
the work item behind its own key, make an area responsible, run that area's
policy, and record which call caused all of it. Four calls would mean four
chances to half-succeed — a work item created and then never assigned is worse
than no work item, because a person has to find it before anybody can fix it.

The order in ``POST`` is fixed by RFC §7.2 and each step is where it is for a
reason:

1. **the receipt, outside the transaction.** It has to survive a rollback of
   the work it describes, or a failed operation would leave no trace and the
   next retry would run as if it were the first;
2. **the binding, then the work item.** Looking the binding up first is what
   makes a redelivered webhook find the item it already created instead of
   creating a second one;
3. **the allocation, through the D0.5 service.** Not a line of the assignment
   rules lives here;
4. **the native activity, on commit.** A work item created by a robot has to
   look like any other to webhooks, notifications and the activity feed.

Everything from step 2 on is inside one ``transaction.atomic()``. A refusal
anywhere in it leaves no work item and no binding — and still leaves the
receipt, marked failed, because ``fail_operation`` runs after the rollback in
a transaction of its own.
"""

# Python imports
import json

# Django imports
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

# Module imports
from plane.api.serializers import IssueSerializer
from plane.api.serializers.orca import (
    CompleteStepSerializer,
    ReassignSerializer,
    TransferSerializer,
    WorkItemAutomationSerializer,
    work_item_envelope,
)
from plane.app.permissions import ProjectEntityPermission
from plane.app.services.orca import (
    ExternalBindingConflict,
    OrcaDomainError,
    UnitNotInWorkspace,
    WorkItemHasNoUnit,
    WorkItemNotFound,
    begin_operation,
    complete_step,
    instance_payload,
    project_process,
    reassign,
    record_service_level,
    resolve_policy,
    return_to_queue,
    set_responsibility,
    transfer_unit,
)
from plane.app.services.orca.errors import IfMatchRequired
from plane.bgtasks.issue_activities_task import issue_activity
from plane.bgtasks.webhook_task import model_activity
from plane.db.models import (
    AutomationOperationType,
    DecisionTrigger,
    ExternalWorkItemBinding,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    ProcessInstanceReference,
    Project,
    ProjectMember,
    RequestedAssignmentMode,
    ResponsibilitySource,
    ServiceLevelSource,
    Workspace,
    WorkspaceMember,
)
from plane.utils.exception_logger import log_exception
from plane.utils.host import base_host

from .base import (
    VALIDATION_ERROR,
    OrcaPublicBaseAPIView,
    error_body,
    domain_error_response,
    read_idempotency_key,
    replay_response,
    validation_error_response,
)


class OrcaWorkItemBaseEndpoint(OrcaPublicBaseAPIView):
    """
    What the three mutating routes share: a receipt around the work, and one
    way of turning a refusal into an answer.

    @description The pattern each ``post`` follows is deliberately rigid. A
    refusal raised inside the operation is caught, **recorded on the receipt**
    with the code and status the caller is about to see, and then returned — so
    a retry of a request that cannot succeed is answered rather than attempted
    a second time. Only an unexpected exception reaches the context manager,
    which marks the receipt ``ORG_INTERNAL_ERROR`` and re-raises.
    """

    permission_classes = [ProjectEntityPermission]

    def run_operation(self, request, workspace, operation_type, work, success_status=status.HTTP_200_OK):
        """
        @description Wrap one mutation in its receipt (RFC §6.7).
        @param work: A callable taking the ``OperationHandle`` and returning
            ``(body, issue)``.
        @param success_status: The status a first, successful call answers with
            — stored on the receipt so a replay reproduces it.
        @returns A DRF ``Response``.
        """
        key = read_idempotency_key(request)
        with begin_operation(workspace, self.api_token, key, operation_type, request.data) as handle:
            if handle.replayed:
                return replay_response(handle)
            try:
                body, issue = work(handle)
            except OrcaDomainError as exc:
                # Caught here, not left to ``handle_exception``, because the
                # receipt has to record the refusal before the caller sees it:
                # a retry of a request that cannot succeed is answered rather
                # than attempted a second time.
                body, http_status = domain_error_response(exc)
                handle.fail(error_code=exc.error_code, response=body, http_status=http_status)
                return Response(body, status=http_status)
            except ValidationError as exc:
                body, http_status = validation_error_response(exc)
                # Recorded as a failure like any other: a caller that fixes its
                # payload has changed the request, and a changed request needs
                # a new key. Said plainly in the guide.
                handle.fail(error_code=VALIDATION_ERROR, response=body, http_status=http_status)
                return Response(body, status=http_status)
            handle.complete(issue=issue, response=body, http_status=success_status)
            return Response(body, status=success_status)

    def resolve_project(self, slug, project_id):
        """@description The project, guaranteed a member by the permission class. @returns Project."""
        return Project.objects.select_related("workspace").get(pk=project_id, workspace__slug=slug)

    def resolve_unit(self, workspace_id, slug):
        """@description An active area of this workspace. @raises UnitNotInWorkspace."""
        unit = OrganizationalUnit.objects.filter(workspace_id=workspace_id, slug=slug, is_active=True).first()
        if unit is None:
            raise UnitNotInWorkspace(unit=slug)
        return unit

    def _resolve_issue(self, project, issue_id):
        """@description The item, in this project. @raises WorkItemNotFound."""
        issue = Issue.objects.filter(pk=issue_id, project_id=project.id).first()
        if issue is None:
            raise WorkItemNotFound(issue_id=str(issue_id))
        return issue

    def resolve_link(self, issue):
        """@description The area responsible for an item. @raises WorkItemHasNoUnit."""
        link = (
            IssueOrganizationalUnit.objects.filter(issue=issue)
            .select_related("organizational_unit", "current_assignment_decision")
            .first()
        )
        if link is None:
            raise WorkItemHasNoUnit(issue_id=str(issue.id))
        return link

    def envelope_for(self, issue, project, link, decision, *, operation=None):
        """@description The standard response for an item that already exists. @returns dict."""
        binding = ExternalWorkItemBinding.objects.filter(issue=issue).first()
        return work_item_envelope(
            issue,
            project,
            link,
            decision,
            binding=binding or _UnboundBinding(),
            binding_created=False,
            operation=operation,
        )


class _UnboundBinding:
    """
    Stands in for a binding on an item that has none.

    @description Reassignment and transfer work on any work item of the area,
    including ones a person created in the interface. The envelope has a
    ``binding`` block regardless, because a client that parses one response
    shape should not have to parse two — so an unbound item reports nulls
    rather than omitting the block.
    """

    external_source = None
    external_id = None


class WorkItemAutomationEndpoint(OrcaWorkItemBaseEndpoint):
    """
    ``POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/``

    @description Create or find a work item by the caller's own key, and put an
    area in charge of it (RFC §7.2).
    """

    def post(self, request, slug, project_id):
        project = self.resolve_project(slug, project_id)
        return self.run_operation(
            request,
            project.workspace,
            AutomationOperationType.CREATE_WORK_ITEM,
            lambda handle: self._create(request, handle, project),
            success_status=status.HTTP_201_CREATED,
        )

    def _create(self, request, handle, project):
        """@description Steps 2-4 of RFC §7.2, in one transaction. @returns ``(body, issue)``."""
        payload = WorkItemAutomationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        external = data["external"]
        responsibility = data["responsibility"]
        assignment = responsibility.get("assignment") or {}
        mode = assignment.get("mode", RequestedAssignmentMode.DEFAULT)
        explicit = mode == RequestedAssignmentMode.EXPLICIT

        with transaction.atomic():
            unit = self.resolve_unit(project.workspace_id, responsibility["unit"])
            issue, binding, created = self._bind(request, project, external)
            link, decision = self._place(request, handle, issue, unit, mode, explicit, assignment, responsibility)
            process = self._project_process(issue, data.get("process"), project.workspace_id)
            self._record_deadlines(request, issue, link, responsibility, process)

            body = work_item_envelope(
                issue,
                project,
                link,
                decision,
                binding=binding,
                binding_created=created,
                operation=handle.operation,
                replay=False,
            )

            if created:
                # Only on creation, and only after the row is really there:
                # the native activity and the webhook are what make a work item
                # made by a robot indistinguishable from one made by a person.
                self._announce(request, project, issue)

        return body, issue

    def _project_process(self, issue, block, workspace_id):
        """
        @description Record which step of which process instance this item is,
        when the caller sent a ``process`` block (item 4.3). Inside the same
        transaction as everything else, and idempotent, because the
        orchestrator retries.
        @returns The ``ProcessInstanceItem`` or ``None``.
        """
        if not block:
            return None
        return project_process(issue, block, workspace_id=workspace_id)

    def _record_deadlines(self, request, issue, link, responsibility, process):
        """
        @description Write the item's service level when the caller sent a
        completion deadline, or when a process step set one (RFC §5.2). The
        assignment deadline is already on the routing link — the queue reads it
        on every page — and this is the record around it: where it came from,
        which version of that source, and what it originally was.
        @returns None.
        """
        completion_due_at = responsibility.get("completion_due_at")
        if completion_due_at is None and process is None:
            return

        source = ServiceLevelSource.PROCESS if process is not None else ServiceLevelSource.UNIT
        version = process.process_instance.template_version if process is not None else ""
        record_service_level(
            issue,
            assignment_due_at=link.assignment_due_at if link else None,
            completion_due_at=completion_due_at,
            source=source,
            source_version=version,
            actor=request.user,
        )

    def _place(self, request, handle, issue, unit, mode, explicit, assignment, responsibility):
        """
        @description Put the area in charge, unless it already is.
        @returns ``(link, decision)`` — the state of the item afterwards.

        **The early return is the important half.** This route is
        create-or-find, and integrations call it on every webhook a record
        emits: a status change, a comment, a field edit. Each of those is a
        different event, so each derives a different idempotency key, so each
        is a genuinely new operation rather than a replay. If every one of them
        re-ran the allocation, an item assigned under ``least_loaded`` would be
        re-ranked — and possibly handed to somebody else — every time the
        customer record was touched, and an area whose policy is ``manual``
        would have its assigned work pushed back into the queue.

        So when the item already exists **and the area asking is the area that
        already owns it**, this reports the current state and changes nothing.
        Moving work is what ``reassign/`` and ``transfer/`` are for, and they
        say so in their names. A *different* area in the body is a real
        instruction and still goes through the service, which transfers.
        """
        if not issue._state.adding:
            existing = (
                IssueOrganizationalUnit.objects.filter(issue=issue)
                .select_related("organizational_unit", "current_assignment_decision", "primary_executor")
                .first()
            )
            if existing is not None and existing.organizational_unit_id == unit.id:
                return existing, existing.current_assignment_decision

        result = set_responsibility(
            issue,
            unit,
            actor=request.user,
            source=ResponsibilitySource.PUBLIC_API,
            trigger=DecisionTrigger.PUBLIC_API,
            requested_mode=None if explicit else mode,
            explicit_executor=assignment.get("primary_executor") if explicit else None,
            collaborators=assignment.get("collaborators") or (),
            assignment_due_at=responsibility.get("assignment_due_at"),
            automation_operation=handle.operation,
        )
        return result.link, result.decision

    def _bind(self, request, project, external):
        """
        @description Find the work item this external key already names, or
        create it and claim the key.
        @returns ``(issue, binding, created)``.
        @raises ExternalBindingConflict: The key is held by an item in another
            project — reusing it would move work between projects behind the
            caller's back.
        """
        existing = (
            ExternalWorkItemBinding.objects.filter(
                workspace_id=project.workspace_id,
                external_source=external["source"],
                external_id=external["id"],
            )
            .select_related("issue")
            .first()
        )
        if existing is not None:
            if existing.issue.project_id != project.id:
                raise ExternalBindingConflict(
                    issue_id=str(existing.issue_id), project_id=str(existing.issue.project_id)
                )
            return existing.issue, existing, False

        issue = self._create_issue(request, project, external)
        try:
            # Its own savepoint: two first calls with the same key race here,
            # and the loser has to be able to read the winner's row rather than
            # poison the whole transaction with the IntegrityError.
            with transaction.atomic():
                binding = ExternalWorkItemBinding.objects.create(
                    workspace_id=project.workspace_id,
                    external_source=external["source"],
                    external_id=external["id"],
                    issue=issue,
                )
        except IntegrityError:
            raise ExternalBindingConflict(external_source=external["source"], external_id=external["id"])
        return issue, binding, True

    def _create_issue(self, request, project, external):
        """
        @description Create the work item through the native v1 serializer, so
        it is an ordinary Plane work item in every respect.
        @returns The ``Issue``.

        Two deliberate arguments to that serializer:

        * ``assignees=[]`` — the area decides who does this, not the request;
        * ``default_assignee_id=None`` — without it the native serializer falls
          back to the project's default assignee whenever ``assignees`` is
          empty, which is exactly the inherited-assignee defect (D2) this
          namespace exists to keep out. Passing ``None`` switches the fallback
          off for this path without touching the serializer everyone else uses.
        """
        body = dict(request.data.get("work_item") or {})
        body["assignees"] = []
        body["external_source"] = external["source"]
        body["external_id"] = external["id"]

        serializer = IssueSerializer(
            data=body,
            context={
                "project_id": project.id,
                "workspace_id": project.workspace_id,
                "default_assignee_id": None,
            },
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        issue = Issue.objects.get(pk=serializer.data["id"])
        # The native endpoint stamps these after saving too: the serializer
        # sets `created_by` from the instance, not from the request.
        issue.created_by_id = request.user.id
        issue.save(update_fields=["created_by"])
        return issue

    def _announce(self, request, project, issue):
        """
        @description Queue the native activity and the webhook, after commit.

        **A failure here must not fail the operation**, and that is not a
        convenience — it is the difference between two bad outcomes. These run
        in ``on_commit``, so by the time they run the work item is already
        committed and cannot be taken back. If a broker that is down were
        allowed to raise, the caller would receive 500 for an operation that
        succeeded, the receipt would be marked failed, and every retry with
        that key would replay the 500 forever — leaving a real work item, with
        a real area answerable for it, that the calling system believes does
        not exist. Losing an activity row and a webhook is worse than nothing
        and much better than that; the exception goes to the log, where an
        operator can see the broker is down.
        """
        actor_id = str(request.user.id)
        requested_data = json.dumps(request.data.get("work_item") or {}, cls=DjangoJSONEncoder)
        origin = base_host(request=request, is_app=True)
        issue_id = str(issue.id)

        def _dispatch():
            try:
                issue_activity.delay(
                    type="issue.activity.created",
                    requested_data=requested_data,
                    actor_id=actor_id,
                    issue_id=issue_id,
                    project_id=str(project.id),
                    current_instance=None,
                    epoch=int(timezone.now().timestamp()),
                    notification=True,
                    origin=origin,
                )
                model_activity.delay(
                    model_name="issue",
                    model_id=issue_id,
                    requested_data=request.data.get("work_item") or {},
                    current_instance=None,
                    actor_id=request.user.id,
                    slug=project.workspace.slug,
                    origin=origin,
                )
            except Exception as exc:
                log_exception(exc)

        transaction.on_commit(_dispatch)


class WorkItemByExternalEndpoint(OrcaPublicBaseAPIView):
    """
    ``GET /api/v1/orca/workspaces/{slug}/work-items/by-external/{source}/{id}/``

    @description The read half of the binding: the caller knows its own key and
    wants the current state. Same envelope as creation, so a client keeps one
    parser — with ``operation`` null, because a read is not an operation.

    Scoped to the workspace rather than a project on purpose: the caller does
    not necessarily know which project its key landed in, and having to guess
    would defeat the point of asking. Authorization therefore cannot come from
    the URL, and is checked against the project the binding resolves to.
    """

    def get(self, request, slug, source, external_id):
        binding = (
            ExternalWorkItemBinding.objects.filter(
                workspace__slug=slug, external_source=source, external_id=external_id
            )
            .select_related("issue", "issue__project", "issue__workspace")
            .first()
        )
        if binding is None:
            raise WorkItemNotFound(external_source=source, external_id=external_id)

        issue = binding.issue
        if not self._may_read(request, issue):
            return Response({"error": "You don't have the required permissions."}, status=status.HTTP_403_FORBIDDEN)

        link = (
            IssueOrganizationalUnit.objects.filter(issue=issue)
            .select_related("organizational_unit", "current_assignment_decision", "primary_executor")
            .first()
        )
        if link is None:
            raise WorkItemHasNoUnit(issue_id=str(issue.id))

        body = work_item_envelope(
            issue,
            issue.project,
            link,
            link.current_assignment_decision,
            binding=binding,
            binding_created=False,
        )
        return Response(body, status=status.HTTP_200_OK)

    def _may_read(self, request, issue):
        """
        @description Any active member of the item's project may read it — the
        same rule the native work-item read applies. The token grants nothing
        of its own (RFC §7.1).
        @returns bool.
        """
        return ProjectMember.objects.filter(project_id=issue.project_id, member=request.user, is_active=True).exists()


class WorkItemReassignEndpoint(OrcaWorkItemBaseEndpoint):
    """
    ``POST .../work-items/{issue_id}/reassign/``

    @description Hand the item to somebody else, or put it back in the area's
    queue. Requires ``If-Match`` carrying the decision the caller believes is
    current: two coordinators — or a coordinator and a robot — reassigning at
    once must not have the second silently overwrite the first.
    """

    def post(self, request, slug, project_id, issue_id):
        project = self.resolve_project(slug, project_id)
        issue = self._resolve_issue(project, issue_id)
        # Read before the receipt is opened, like the idempotency key: a
        # missing precondition header is a malformed request, not a failed
        # operation, and it should not spend the caller's key.
        expected = self._read_if_match(request)
        return self.run_operation(
            request,
            project.workspace,
            AutomationOperationType.REASSIGN,
            lambda handle: self._reassign(request, handle, project, issue, expected),
        )

    def _read_if_match(self, request):
        value = (request.headers.get("If-Match") or "").strip().strip('"')
        if not value:
            raise IfMatchRequired()
        return value

    def _reassign(self, request, handle, project, issue, expected):
        payload = ReassignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        # Raises if no area owns the item: this API reassigns work an area is
        # answerable for, and a bare Plane work item is not that.
        self.resolve_link(issue)
        if data.get("return_to_queue"):
            result = return_to_queue(
                issue,
                actor=request.user,
                reason=data.get("reason", ""),
                trigger=DecisionTrigger.PUBLIC_API,
                expected_decision_id=expected,
                automation_operation=handle.operation,
            )
        else:
            result = reassign(
                issue,
                data["primary_executor"],
                actor=request.user,
                reason=data.get("reason", ""),
                expected_decision_id=expected,
                trigger=DecisionTrigger.PUBLIC_API,
                automation_operation=handle.operation,
            )

        body = self.envelope_for(issue, project, result.link, result.decision, operation=handle.operation)
        return body, issue


class WorkItemTransferEndpoint(OrcaWorkItemBaseEndpoint):
    """
    ``POST .../work-items/{issue_id}/transfer/``

    @description Move responsibility to another area (RFC §6.8). No
    ``If-Match``: a transfer is not a contested edit of one decision, it is a
    statement that the work belongs somewhere else, and the losing side of a
    race still ends with the item in one area with one history.
    """

    def post(self, request, slug, project_id, issue_id):
        project = self.resolve_project(slug, project_id)
        issue = self._resolve_issue(project, issue_id)
        return self.run_operation(
            request,
            project.workspace,
            AutomationOperationType.TRANSFER_UNIT,
            lambda handle: self._transfer(request, handle, project, issue),
        )

    def _transfer(self, request, handle, project, issue):
        payload = TransferSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        self.resolve_link(issue)
        to_unit = self.resolve_unit(project.workspace_id, data["unit"])
        transfer = transfer_unit(
            issue,
            to_unit,
            actor=request.user,
            source=ResponsibilitySource.PUBLIC_API,
            reason=data.get("reason", ""),
            trigger=DecisionTrigger.PUBLIC_API,
            automation_operation=handle.operation,
        )

        link = transfer.allocation.link if transfer.allocation else self.resolve_link(issue)
        decision = transfer.allocation.decision if transfer.allocation else link.current_assignment_decision
        body = self.envelope_for(issue, project, link, decision, operation=handle.operation)
        return body, issue


class WorkItemCompleteEndpoint(OrcaWorkItemBaseEndpoint):
    """
    ``POST .../work-items/{issue_id}/complete/`` (RFC §7.2)

    @description A claim from outside that a step of a process is finished.
    What happens next is the step's own ``completion_mode``: ``automatic``
    moves the item to a completed state, ``automatic_with_review`` moves it to
    the area's review state (or labels it, when the area named none) and leaves
    it open, and ``manual`` refuses with ``ORG_COMPLETION_MANUAL_ONLY``.

    Behind an ``Idempotency-Key`` like every other mutation here, because the
    orchestrator retries and closing a step twice should be one closure and one
    answer, not two.

    Note this is the one mutation that does not touch the assignment: a step
    being done says nothing about who did it, which is why it writes a
    ``ProcessCompletionEvent`` and not an ``AssignmentDecision``.
    """

    def post(self, request, slug, project_id, issue_id):
        project = self.resolve_project(slug, project_id)
        issue = self._resolve_issue(project, issue_id)
        return self.run_operation(
            request,
            project.workspace,
            AutomationOperationType.COMPLETE,
            lambda handle: self._complete(request, handle, project, issue),
        )

    def _complete(self, request, handle, project, issue):
        payload = CompleteStepSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        # The area's policy is what names the states a completed or
        # under-review step lands in, so it is resolved for the item's own
        # project rather than assumed.
        link = IssueOrganizationalUnit.objects.filter(issue=issue).select_related("organizational_unit").first()
        policy = resolve_policy(link.organizational_unit, issue.project_id).policy if link is not None else None

        event, item = complete_step(
            issue,
            evidence=data.get("evidence") or {},
            rule_version=data.get("rule_version", ""),
            source=data.get("source", ""),
            event_id=data.get("event_id", ""),
            actor=request.user,
            policy=policy,
        )

        issue.refresh_from_db()
        body = self.envelope_for(
            issue,
            project,
            link,
            link.current_assignment_decision if link else None,
            operation=handle.operation,
        )
        body["completion"] = {
            "mode": event.mode,
            "applied": event.applied,
            "event_id": str(event.id),
            "state": issue.state.name if issue.state_id else None,
            "step_key": item.step_key if item is not None else None,
        }
        return body, issue


class ProcessInstanceEndpoint(OrcaPublicBaseAPIView):
    """
    ``GET /api/v1/orca/workspaces/{slug}/process-instances/{source}/{instance_id}/``

    @description One run of a process, with its steps as Plane sees them: the
    native state, the routing state, the executor and the deadlines.

    ``status`` is derived from the steps rather than read off the row — the
    column is a cache the completion path keeps, and a person closing the last
    step in the interface finishes the instance just as truly as the
    orchestrator does.
    """

    use_read_replica = True

    def get(self, request, slug, source, instance_id):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response(error_body("ORG_DIRECTORY_WORKSPACE_NOT_FOUND"), status=status.HTTP_404_NOT_FOUND)
        if not WorkspaceMember.objects.filter(workspace=workspace, member=request.user, is_active=True).exists():
            return Response(error_body("ORG_UNIT_PERMISSION_DENIED"), status=status.HTTP_403_FORBIDDEN)

        instance = ProcessInstanceReference.objects.filter(
            workspace=workspace, external_source=source, external_instance_id=instance_id
        ).first()
        if instance is None:
            return Response(error_body("ORG_WORK_ITEM_NOT_FOUND"), status=status.HTTP_404_NOT_FOUND)

        return Response(instance_payload(instance), status=status.HTTP_200_OK)


__all__ = [
    "ProcessInstanceEndpoint",
    "WorkItemAutomationEndpoint",
    "WorkItemByExternalEndpoint",
    "WorkItemCompleteEndpoint",
    "WorkItemReassignEndpoint",
    "WorkItemTransferEndpoint",
]
