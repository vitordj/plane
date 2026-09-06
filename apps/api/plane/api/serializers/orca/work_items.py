# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The request and response shapes of the composed work-item operation (RFC §7.2).

The body arrives in blocks — ``external``, ``work_item``, ``responsibility``,
``process`` — because they answer different questions and fail for different
reasons. ``external`` is the caller's own key; ``work_item`` is ordinary Plane
content; ``responsibility`` is the part this API exists for.

Two refusals here are not ordinary validation errors and carry Orca codes
instead:

* ``assignees`` inside ``work_item`` — the area decides who does the work, and
  a silently dropped assignee would leave the caller believing it assigned
  somebody (``ORG_ASSIGNEES_NOT_ALLOWED_HERE``);
* a ``process`` block — part of the published contract, but Phase 4
  (``ORG_PROCESS_PROJECTION_DISABLED``).

Both raise the domain exception rather than a ``ValidationError``, so the view
handles them on the same path as a refusal from the assignment service: one
``except OrcaDomainError`` that closes the receipt with the right code and the
right status.
"""

# Django imports
from django.conf import settings

# Third party imports
from rest_framework import serializers

# Module imports
from plane.app.services.orca import AssigneesNotAllowedHere, ProcessProjectionDisabled
from plane.db.models import Issue, RequestedAssignmentMode

from .base import StrictSerializer


class ExternalReferenceSerializer(StrictSerializer):
    """The caller's own identity for this piece of work."""

    source = serializers.CharField(max_length=255)
    id = serializers.CharField(max_length=255)


class WorkItemBodySerializer(StrictSerializer):
    """
    The Plane content of the work item.

    @description Deliberately narrower than the native ``IssueSerializer``:
    only fields an automation has a reason to set. ``assignees`` is absent by
    design and refused explicitly below rather than by the unknown-field rule,
    because a caller that sends it has a wrong model of the API, not a typo.
    """

    name = serializers.CharField(max_length=255)
    description_html = serializers.CharField(required=False, allow_blank=True)
    state = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=[choice[0] for choice in Issue.PRIORITY_CHOICES], required=False)
    labels = serializers.ListField(child=serializers.UUIDField(), required=False)
    start_date = serializers.DateField(required=False, allow_null=True)
    target_date = serializers.DateField(required=False, allow_null=True)
    parent = serializers.UUIDField(required=False, allow_null=True)
    estimate_point = serializers.UUIDField(required=False, allow_null=True)

    def to_internal_value(self, data):
        # Before the unknown-field sweep, so the caller gets the code that
        # explains the rule instead of "unknown field: assignees".
        if isinstance(data, dict) and "assignees" in data:
            raise AssigneesNotAllowedHere()
        return super().to_internal_value(data)


class AssignmentSerializer(StrictSerializer):
    """
    How the area should turn responsibility into an assignee (RFC §7.2).

    @description ``explicit`` is the only mode that names people, and it is the
    only one that may: the other three ask the area's policy to decide, and a
    ``primary_executor`` alongside them would be a caller trying to have it
    both ways.
    """

    mode = serializers.ChoiceField(choices=RequestedAssignmentMode.values, default=RequestedAssignmentMode.DEFAULT)
    primary_executor = serializers.UUIDField(required=False, allow_null=True)
    collaborators = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)

    def validate(self, data):
        explicit = data.get("mode") == RequestedAssignmentMode.EXPLICIT
        if explicit and not data.get("primary_executor"):
            raise serializers.ValidationError({"primary_executor": "Required when mode is 'explicit'."})
        if not explicit and data.get("primary_executor"):
            raise serializers.ValidationError(
                {"primary_executor": "Only accepted when mode is 'explicit'; otherwise the area's policy decides."}
            )
        if not explicit and data.get("collaborators"):
            raise serializers.ValidationError({"collaborators": "Only accepted when mode is 'explicit'."})
        return data


class ResponsibilitySerializer(StrictSerializer):
    """Which area owns the item, and by when somebody must be on it."""

    unit = serializers.CharField(max_length=100)
    assignment = AssignmentSerializer(required=False)
    assignment_due_at = serializers.DateTimeField(required=False, allow_null=True)
    # Accepted in the RFC's example body, refused until Phase 4 gives it
    # somewhere to live (IssueServiceLevel). Taking it and dropping it would be
    # a lie the caller cannot see.
    completion_due_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_completion_due_at(self, value):
        raise serializers.ValidationError(
            "Completion deadlines arrive with process projection (Phase 4). Use assignment_due_at."
        )


class WorkItemAutomationSerializer(StrictSerializer):
    """The whole composed operation."""

    external = ExternalReferenceSerializer()
    work_item = WorkItemBodySerializer()
    responsibility = ResponsibilitySerializer()
    process = serializers.DictField(required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict) and "process" in data:
            raise ProcessProjectionDisabled()
        return super().to_internal_value(data)


class ReassignSerializer(StrictSerializer):
    """
    Hand the item to somebody else, or put it back in the queue.

    @description Exactly one of the two, because "reassign to nobody" and
    "return to the queue" are the same act and the caller should not have to
    guess which spelling this API took.
    """

    primary_executor = serializers.UUIDField(required=False, allow_null=True)
    return_to_queue = serializers.BooleanField(required=False, default=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        named = data.get("primary_executor")
        returning = data.get("return_to_queue")
        if bool(named) == bool(returning):
            raise serializers.ValidationError("Send exactly one of 'primary_executor' or 'return_to_queue': true.")
        return data


class TransferSerializer(StrictSerializer):
    """Move the item to another area (RFC §6.8)."""

    unit = serializers.CharField(max_length=100)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


# --- response ----------------------------------------------------------------


def _person(user):
    """@description The shape a person takes in this API. @returns dict or None."""
    if user is None:
        return None
    return {"id": str(user.id), "email": user.email, "display_name": user.display_name}


def work_item_url(issue, project):
    """
    @description The address a human opens to see this item — the same route
    the web app registers (``:workspaceSlug/browse/:workItem``), not an API
    path, because the field exists so a robot's message can link a person to
    the work.
    @returns Absolute URL as a string.
    """
    base = (settings.WEB_URL or "").rstrip("/")
    return f"{base}/{issue.workspace.slug}/browse/{project.identifier}-{issue.sequence_id}/"


def work_item_envelope(issue, project, link, decision, *, binding, binding_created, operation=None, replay=False):
    """
    @description The one response shape of this namespace (RFC §7.2), used by
    creation, by ``by-external``, and by both mutations of item 1.5. A client
    that can read one of them can read all of them, and a replay is
    byte-identical to the answer it replays.
    @param link: The ``IssueOrganizationalUnit`` after the operation.
    @param decision: The decision in force; ``None`` before any was taken.
    @param binding: The ``ExternalWorkItemBinding`` for this item.
    @param binding_created: Whether this call created it.
    @param operation: The receipt, when the call had one; ``None`` on reads.
    @returns A JSON-serializable dict.
    """
    return {
        "work_item": {
            "id": str(issue.id),
            "sequence_id": issue.sequence_id,
            "identifier": f"{project.identifier}-{issue.sequence_id}",
            "url": work_item_url(issue, project),
        },
        "binding": {
            "source": binding.external_source,
            "id": binding.external_id,
            "created": binding_created,
        },
        "responsibility": {
            "unit": {"id": str(link.organizational_unit_id), "slug": link.organizational_unit.slug},
            "routing_state": link.routing_state,
            "queue_reason": link.queue_reason,
            "primary_executor": _person(link.primary_executor),
            "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
        },
        "decision": None
        if decision is None
        else {
            "id": str(decision.id),
            "requested_mode": decision.requested_mode,
            "effective_mode": decision.effective_mode,
            "policy_source": decision.policy_source,
            "policy_version": decision.policy_version,
            "algorithm_version": decision.algorithm_version,
            "outcome": decision.outcome,
        },
        "operation": None if operation is None else {"idempotency_key": operation.idempotency_key, "replay": replay},
    }
