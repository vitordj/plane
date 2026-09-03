# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Making a mutation safe to send twice.

A robot calling an HTTP API cannot tell a timeout from a slow success, so it
retries — and every retry that is treated as a new request creates a second
work item somebody has to notice and delete. The contract here is the usual
one: the caller invents a key it can reproduce, and the first call to use that
key writes down what it was asked and, at the end, exactly what it answered.
A repeat gets that answer back.

Two details are easy to get wrong and expensive to get wrong:

* The **failure** of an operation is recorded outside the business
  transaction. Otherwise the rollback erases the evidence that anything was
  ever attempted, and the caller's retry finds a clean slate — which is the
  one case where retrying repeats the damage.
* A receipt left ``in_progress`` by a crashed worker has to become claimable
  again, or that key is poisoned forever. After a minute it is fair game.
"""

# Python imports
import hashlib
import json
import logging
from datetime import timedelta

# Django imports
from django.db import IntegrityError, transaction
from django.utils import timezone

# Module imports
from plane.db.models import AutomationOperation, AutomationOperationStatus

from .errors import IdempotencyPayloadMismatch, OperationInProgress

logger = logging.getLogger("plane.orca.automation")

# How long a receipt may sit in progress before another call may take it over.
# Long enough that a slow-but-alive request is not stolen from, short enough
# that a crashed worker does not block the caller for the rest of the day.
ABANDONED_AFTER = timedelta(seconds=60)


def canonical_hash(payload) -> str:
    """
    Fingerprint a request body, independent of how it was serialized.

    @description Sorted keys and no spaces, so the same request hashes the
    same whether it came from Python, Node or a shell script — otherwise a
    caller that changed JSON libraries would find every one of its keys
    "already used with a different payload".
    @param payload: The request body, as parsed data.
    @returns: The SHA-256 hex digest, 64 characters.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OperationHandle:
    """
    A claim on one idempotency key.

    @description Used as a context manager around the work it protects: on the
    way out it makes sure the receipt ends in a resting state, so no key is
    left claimed by a request that died halfway.
    """

    def __init__(self, operation, replayed=False):
        self.operation = operation
        # True when the answer came from an earlier call rather than this one.
        self.replayed = replayed
        self._closed = replayed

    @property
    def snapshot(self):
        """@returns: The response the original call sent back."""
        return self.operation.response_snapshot or {}

    @property
    def status(self):
        return self.operation.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # A business error (a refusal the caller can understand) is recorded by
        # the view, which knows its code. Anything else — a bug, a database
        # error — lands here, and the receipt has to say so rather than stay
        # claimed forever. Recorded outside the caller's transaction on
        # purpose: see the module docstring.
        if exc_type is not None and not self._closed:
            fail_operation(self, error_code="ORG_INTERNAL_ERROR", response={"error_code": "ORG_INTERNAL_ERROR"})
        return False


def begin_operation(workspace, api_token, key, operation_type, payload) -> OperationHandle:
    """
    Claim an idempotency key, or hand back what it answered before.

    @description Every branch of the contract lives here: a new key proceeds,
    a finished key replays, a key used for something else is refused, and a
    key still in flight either asks the caller to wait or — if whoever claimed
    it has clearly died — is taken over.
    @param workspace: The workspace the call is against.
    @param api_token: The token that made the call, for the audit trail.
    @param key: The caller's ``Idempotency-Key``.
    @param operation_type: Which mutation this is.
    @param payload: The request body, for the fingerprint.
    @returns: An ``OperationHandle``; ``replayed`` is True when the caller
        should send back the snapshot untouched.
    @raises IdempotencyPayloadMismatch: Same key, different request.
    @raises OperationInProgress: An identical call is still running.
    """
    request_hash = canonical_hash(payload)

    try:
        with transaction.atomic():
            operation = AutomationOperation.objects.create(
                workspace=workspace,
                api_token=api_token,
                idempotency_key=key,
                request_hash=request_hash,
                operation_type=operation_type,
                status=AutomationOperationStatus.IN_PROGRESS,
            )
        return OperationHandle(operation)
    except IntegrityError:
        # Somebody else holds the key. Whether they finished is the next question.
        pass

    operation = AutomationOperation.objects.filter(workspace=workspace, idempotency_key=key).first()
    if operation is None:
        # Vanishingly rare: the row lost the race and then disappeared. Treat
        # the key as free rather than failing the caller for our own timing.
        operation = AutomationOperation.objects.create(
            workspace=workspace,
            api_token=api_token,
            idempotency_key=key,
            request_hash=request_hash,
            operation_type=operation_type,
            status=AutomationOperationStatus.IN_PROGRESS,
        )
        return OperationHandle(operation)

    if operation.request_hash != request_hash:
        raise IdempotencyPayloadMismatch(
            "this idempotency key was used with a different request",
            idempotency_key=key,
        )

    if operation.status in (AutomationOperationStatus.SUCCEEDED, AutomationOperationStatus.FAILED):
        logger.info(
            "orca.automation.replay",
            extra={
                "workspace_id": str(workspace.id),
                "idempotency_key": key,
                "operation_id": str(operation.id),
                "status": operation.status,
            },
        )
        return OperationHandle(operation, replayed=True)

    if timezone.now() - operation.created_at < ABANDONED_AFTER:
        raise OperationInProgress("an identical request is still being processed", idempotency_key=key)

    # Whoever claimed this is not coming back. Take it over rather than leave
    # the key poisoned for good.
    logger.warning(
        "orca.automation.resumed_abandoned",
        extra={"workspace_id": str(workspace.id), "idempotency_key": key, "operation_id": str(operation.id)},
    )
    AutomationOperation.objects.filter(pk=operation.pk).update(
        api_token=api_token, status=AutomationOperationStatus.IN_PROGRESS, error_code=""
    )
    operation.refresh_from_db()
    return OperationHandle(operation)


def complete_operation(handle, *, issue=None, response, status=AutomationOperationStatus.SUCCEEDED):
    """
    @description Record what the call answered, so a replay can answer the
    same. Stores the response verbatim: a replay must not re-derive anything,
    because the world may have moved since.
    @param handle: The handle from ``begin_operation``.
    @param issue: The work item the call produced, when there is one.
    @param response: The response body, exactly as sent.
    @returns: The updated operation.
    """
    AutomationOperation.objects.filter(pk=handle.operation.pk).update(
        status=status,
        issue=issue,
        response_snapshot=response,
        completed_at=timezone.now(),
    )
    handle.operation.refresh_from_db()
    handle._closed = True
    return handle.operation


def fail_operation(handle, *, error_code, response=None):
    """
    @description Record a refusal as a resting state, with the body that was
    sent. A caller retrying a request that failed for a reason of its own gets
    the same refusal instead of a fresh attempt each time.
    @param handle: The handle from ``begin_operation``.
    @param error_code: The Orca error code.
    @param response: The response body, when there is one.
    @returns: The updated operation.
    """
    AutomationOperation.objects.filter(pk=handle.operation.pk).update(
        status=AutomationOperationStatus.FAILED,
        error_code=error_code or "",
        response_snapshot=response or {},
        completed_at=timezone.now(),
    )
    handle.operation.refresh_from_db()
    handle._closed = True
    return handle.operation
