# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Domain errors for the assignment layer.

Each one carries the Orca error code and the HTTP status it should surface as,
so a view converts the whole family with a single ``except`` instead of
translating case by case — and so the same refusal reads the same way whether
it came from the app, the public API or a management command.
"""

# Third party imports
from rest_framework import status


class OrcaDomainError(Exception):
    """Base for every refusal the assignment layer makes on purpose."""

    error_code = "ORG_INVALID_ROUTING_TRANSITION"
    http_status = status.HTTP_400_BAD_REQUEST

    def __init__(self, message="", **context):
        super().__init__(message or self.error_code)
        # Extra facts the caller can put in the response body — the winner of a
        # claim, the decision that was current. Never anything private.
        self.context = context


class AssignmentModeNotAllowed(OrcaDomainError):
    """The caller asked for a mode the area's policy does not allow (I7)."""

    error_code = "ORG_ASSIGNMENT_MODE_NOT_ALLOWED"


class UnitNotCoveringProject(OrcaDomainError):
    """The area does not cover the work item's project (I2)."""

    error_code = "ORG_UNIT_NOT_COVERING_PROJECT"


class ExecutorNotEligible(OrcaDomainError):
    """The named person is not someone this area can hand this work to (I4)."""

    error_code = "ORG_EXECUTOR_NOT_ELIGIBLE"


class AlreadyClaimed(OrcaDomainError):
    """Somebody else took it first. ``context["executor_id"]`` says who."""

    error_code = "ORG_WORK_ITEM_ALREADY_CLAIMED"
    http_status = status.HTTP_409_CONFLICT


class DecisionStale(OrcaDomainError):
    """
    The caller acted on a view of the queue that has since moved.

    @description 412 rather than 409: this is a failed precondition, and the
    client's next step is to re-read and decide again, not to retry blindly.
    """

    error_code = "ORG_DECISION_STALE"
    http_status = status.HTTP_412_PRECONDITION_FAILED


class InvalidTransition(OrcaDomainError):
    """The work item is not in a state this operation can act on."""

    error_code = "ORG_INVALID_ROUTING_TRANSITION"
    http_status = status.HTTP_409_CONFLICT


class IdempotencyKeyRequired(OrcaDomainError):
    """A mutation arrived with no ``Idempotency-Key``."""

    error_code = "ORG_IDEMPOTENCY_KEY_REQUIRED"


class IdempotencyPayloadMismatch(OrcaDomainError):
    """
    The same key, a different request.

    @description Almost always a bug in the caller — a key derived from
    something that varies, a timestamp inside the payload. Refused rather than
    treated as a new request, because the alternative silently does the work
    twice under one receipt.
    """

    error_code = "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH"
    http_status = status.HTTP_409_CONFLICT


class OperationInProgress(OrcaDomainError):
    """An identical call is still running; the caller should wait and retry."""

    error_code = "ORG_OPERATION_IN_PROGRESS"
    http_status = status.HTTP_409_CONFLICT


class ExternalBindingConflict(OrcaDomainError):
    """That external reference already points at a different work item (I8)."""

    error_code = "ORG_EXTERNAL_BINDING_CONFLICT"
    http_status = status.HTTP_409_CONFLICT


class IfMatchRequired(OrcaDomainError):
    """
    A reassignment arrived without saying which decision it was acting on.

    @description 428 Precondition Required, the status invented for exactly
    this: the request is fine, but this endpoint refuses to act blindly.
    """

    error_code = "ORG_IF_MATCH_REQUIRED"
    http_status = status.HTTP_428_PRECONDITION_REQUIRED


class AssigneesNotAllowedHere(OrcaDomainError):
    """
    The caller put assignees on the work item block.

    @description Refused rather than honoured: assignment through this API is
    the area's decision, recorded and explainable, and a native assignee list
    smuggled in beside it would be neither.
    """

    error_code = "ORG_ASSIGNEES_NOT_ALLOWED_HERE"


class ProcessProjectionDisabled(OrcaDomainError):
    """The request carried a ``process`` block and the instance has it off."""

    error_code = "ORG_PROCESS_PROJECTION_DISABLED"
