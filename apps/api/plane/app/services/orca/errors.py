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
