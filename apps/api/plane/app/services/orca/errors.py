# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Domain errors of the assignment layer.

@description The service refuses things for reasons the caller needs to tell
apart — a mode the policy forbids is not the same failure as an executor who
is not eligible, and a client that retried a stale reassignment needs to know
that rather than "400". Each error carries the name of its code in
``ORCA_ERROR_CODES`` and the status it should answer with, so a view converts
the whole family in one place:

```python
except OrcaDomainError as exc:
    return orca_error(exc.error_code, exc.http_status)
```

Raising here rather than returning a ``Response`` keeps the service usable
from a management command and from a Celery task, neither of which has a
request to answer.
"""

# Django imports
from rest_framework import status


class OrcaDomainError(Exception):
    """
    Base of the family.

    Attributes:
        error_code (str): Key in ``ORCA_ERROR_CODES``.
        http_status (int): Status the view should answer with.
        payload (dict): Extra fields the caller can act on (the winner of a
            contested claim, for instance). Never contains personal data
            beyond ids.
    """

    error_code = "ORG_INVALID_ROUTING_TRANSITION"
    http_status = status.HTTP_400_BAD_REQUEST

    def __init__(self, message="", **payload):
        super().__init__(message or self.error_code)
        self.payload = payload


class AssignmentModeNotAllowed(OrcaDomainError):
    """The caller asked for a mode the effective policy does not allow.

    @description Never degraded to the default: a caller that asked for
    ``least_loaded`` and silently got ``manual`` would believe the item was
    assigned when it is sitting in a queue (invariant I7).
    """

    error_code = "ORG_ASSIGNMENT_MODE_NOT_ALLOWED"


class UnitNotCoveringProject(OrcaDomainError):
    """The area does not cover the work item's project (defect D1, invariant I2)."""

    error_code = "ORG_UNIT_NOT_COVERING_PROJECT"


class ExecutorNotEligible(OrcaDomainError):
    """
    The person named cannot hold this work.

    @description Either not an active member of the area, or not an active
    project member with a role that can be assigned. Checked at decision time
    (invariant I4), because both can change between two requests.
    """

    error_code = "ORG_EXECUTOR_NOT_ELIGIBLE"


class AlreadyClaimed(OrcaDomainError):
    """
    Somebody else took the item first.

    @description Carries ``primary_executor_id`` so the interface can say who,
    instead of making the loser reload to find out.
    """

    error_code = "ORG_WORK_ITEM_ALREADY_CLAIMED"
    http_status = status.HTTP_409_CONFLICT


class DecisionStale(OrcaDomainError):
    """
    The reassignment was based on a decision that is no longer current.

    @description The If-Match of this layer: two coordinators reassigning the
    same item at once must not have the second silently overwrite the first.
    Carries ``current_decision_id`` so the caller can re-read and retry.
    """

    error_code = "ORG_DECISION_STALE"
    http_status = status.HTTP_409_CONFLICT


class InvalidTransition(OrcaDomainError):
    """The routing state cannot go from where it is to where it was asked to go (RFC §6.2)."""

    error_code = "ORG_INVALID_ROUTING_TRANSITION"


class IdempotencyKeyRequired(OrcaDomainError):
    """A mutation on the public API arrived without ``Idempotency-Key``.

    @description Required rather than optional because the callers are
    programs that retry: without a key, a redelivered webhook is
    indistinguishable from a second piece of work, and the API would create
    two work items for one event.
    """

    error_code = "ORG_IDEMPOTENCY_KEY_REQUIRED"


class IdempotencyPayloadMismatch(OrcaDomainError):
    """The key was already used, with a different payload.

    @description The one branch of §6.7 that must never be answered as a
    replay. The caller either reused a key by mistake or two events collided
    on one key; returning the earlier response would silently drop the second
    request, and executing it would break the promise the key makes.
    """

    error_code = "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH"
    http_status = status.HTTP_409_CONFLICT


class OperationInProgress(OrcaDomainError):
    """The same key is being processed right now.

    @description A retry that arrived before the first call finished. Answered
    rather than queued: the caller should back off and retry, and holding the
    request would tie up a worker for the length of the first one.
    """

    error_code = "ORG_OPERATION_IN_PROGRESS"
    http_status = status.HTTP_409_CONFLICT


class ExternalBindingConflict(OrcaDomainError):
    """The external key already points at a different work item.

    @description Carries ``issue_id`` of the item that holds the binding, so
    the caller can look at it instead of guessing which of its own records
    collided.
    """

    error_code = "ORG_EXTERNAL_BINDING_CONFLICT"
    http_status = status.HTTP_409_CONFLICT
