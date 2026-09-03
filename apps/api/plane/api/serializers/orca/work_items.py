# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What an automation may say when it creates or moves work.

The shape is a composed request — the work item, the external reference that
identifies it, and who should end up doing it — because those three are one
decision for the caller and there is no useful state where only some of them
happened.
"""

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import AssignmentMode, RequestedAssignmentMode


class ExternalRefSerializer(serializers.Serializer):
    """The caller's own identifier for this piece of work."""

    source = serializers.CharField(max_length=255)
    id = serializers.CharField(max_length=255)


class AssignmentBlockSerializer(serializers.Serializer):
    """
    How the caller wants the work assigned.

    @description ``default`` means "whatever the area decided"; naming a mode
    asks for it and is refused if the area does not allow it. ``explicit``
    names the person, and then ``primary_executor`` is not optional — an
    explicit assignment with nobody named is a request that cannot mean
    anything.
    """

    mode = serializers.ChoiceField(
        choices=[choice.value for choice in RequestedAssignmentMode],
        default=RequestedAssignmentMode.DEFAULT.value,
    )
    primary_executor = serializers.UUIDField(required=False, allow_null=True)
    collaborators = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)

    def validate(self, attrs):
        if attrs.get("mode") == AssignmentMode.EXPLICIT and not attrs.get("primary_executor"):
            raise serializers.ValidationError(
                {"primary_executor": "explicit assignment must name the person who will do the work"}
            )
        return attrs


class ResponsibilityBlockSerializer(serializers.Serializer):
    """Which area owns the work, and by when somebody should have taken it."""

    unit = serializers.CharField(max_length=255)
    assignment = AssignmentBlockSerializer(required=False)
    assignment_due_at = serializers.DateTimeField(required=False, allow_null=True)
    completion_due_at = serializers.DateTimeField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class WorkItemAutomationSerializer(serializers.Serializer):
    """
    The composed create request.

    @description Validation here is about what the caller may *say*; whether
    the area covers the project, whether the named person is eligible and
    whether the mode is allowed are the service's questions, because they are
    the same questions wherever the request came from.
    """

    external = ExternalRefSerializer()
    work_item = serializers.DictField()
    responsibility = ResponsibilityBlockSerializer()
    process = serializers.DictField(required=False)

    # Fields of the native work item that this API does not let a caller set,
    # because the area decides them and the decision has to be recorded.
    FORBIDDEN_WORK_ITEM_FIELDS = ("assignees", "assignee_ids")

    def validate_work_item(self, value):
        if not value.get("name"):
            raise serializers.ValidationError({"name": "a work item needs a name"})
        for field in self.FORBIDDEN_WORK_ITEM_FIELDS:
            if field in value:
                # Not silently dropped: a caller that thought it was assigning
                # somebody should find out it was not.
                raise serializers.ValidationError(
                    {field: "assignment goes through the responsibility block, not the work item"}
                )
        return value


class ReassignSerializer(serializers.Serializer):
    """
    Move a work item to somebody else, or back to the queue.

    @description Exactly one of the two: naming a person and asking for the
    queue at the same time is a request with no meaning, and guessing which
    half the caller meant is worse than refusing.
    """

    primary_executor = serializers.UUIDField(required=False, allow_null=True)
    return_to_queue = serializers.BooleanField(required=False, default=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        wants_queue = attrs.get("return_to_queue")
        executor = attrs.get("primary_executor")
        if wants_queue and executor:
            raise serializers.ValidationError("send either primary_executor or return_to_queue, not both")
        if not wants_queue and not executor:
            raise serializers.ValidationError("send primary_executor, or return_to_queue: true")
        return attrs


class TransferSerializer(serializers.Serializer):
    """Hand a work item to a different area."""

    unit = serializers.CharField(max_length=255)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
