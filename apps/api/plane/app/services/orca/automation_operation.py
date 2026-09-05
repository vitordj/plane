# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Idempotency for the public automation API (RFC §6.7).

The callers of ``/api/v1/orca/`` are programs, and programs retry: a webhook
redelivered after a timeout, a queue worker restarted mid-batch, a client that
did not see the response. Without a receipt, each retry allocates the work
again — a second work item, a second person answerable for it, a second
decision in the log.

This module is that receipt. One row per ``Idempotency-Key``, holding the hash
of the payload it was opened for and the response that was sent. A retry with
the same payload gets the recorded answer back; a *different* payload under
the same key is a conflict, not a replay.

**A replay answers the original, not the present.** If somebody reassigned the
item between the first call and the retry, the retry still reports the first
allocation. That is the point: a retry must not read as though it changed
something, and a caller that wants current state asks for it with a ``GET``.

Usage, which is what keeps a crashed operation from staying `in_progress`
forever::

    with begin_operation(...) as handle:
        if handle.replayed:
            return handle.replay_response()
        ...                      # do the work
        handle.complete(issue=issue, response=body)
"""

# Python imports
import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta

# Django imports
from django.db import IntegrityError, transaction
from django.utils import timezone

# Module imports
from plane.db.models import AutomationOperation, AutomationOperationStatus

from .errors import IdempotencyPayloadMismatch, OperationInProgress

# How long an in-progress row is believed before the next caller may take it
# over. Long enough that a slow allocation is not stolen mid-flight, short
# enough that a worker killed by an OOM does not wedge the key until somebody
# notices. RFC §6.7 fixes it at sixty seconds.
ABANDONED_AFTER = timedelta(seconds=60)

# Where the original HTTP status is kept inside response_snapshot. Underscored
# so it cannot collide with a field of the real response body, and stripped
# before the snapshot is handed back to a caller.
SNAPSHOT_STATUS_KEY = "_http_status"


def canonical_hash(payload) -> str:
    """
    Fingerprint a request body so a retry can be told from a new request.

    @description Canonical on purpose: keys sorted, no incidental whitespace,
    non-ASCII left as itself. A client that serializes its JSON with keys in a
    different order on the retry — which many do, dictionaries being unordered
    in several languages — is still retrying, and must not be told its payload
    changed.

    ``ensure_ascii=False`` matters for the same reason: a name written in
    Cyrillic must hash the same whether or not the client escaped it.

    @param payload: The request body, already parsed. Anything
        ``json.dumps`` accepts.
    @returns 64 lowercase hex characters.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OperationHandle:
    """
    One operation in flight, and what the caller should do about it.

    Attributes:
        operation (AutomationOperation): The row.
        replayed (bool): The key was already spent and finished; the caller
            must return the recorded response and do no work.
    """

    def __init__(self, operation, replayed=False):
        self.operation = operation
        self.replayed = replayed

    @property
    def is_open(self) -> bool:
        """Whether this handle still needs an outcome written to it."""
        return not self.replayed and self.operation.status == AutomationOperationStatus.IN_PROGRESS

    def replay_response(self):
        """
        @description The body and status recorded the first time round.
        @returns ``(body, http_status)``. The status is the one the original
            call answered with, so a replayed failure stays a failure rather
            than turning into a 200 with an error body.

        The stored status is kept *inside* the snapshot rather than in a column
        of its own — one JSON write instead of a schema change — and stripped
        back out here, so it never reaches the caller as a field of the body.
        """
        body = dict(self.operation.response_snapshot or {})
        http_status = body.pop(SNAPSHOT_STATUS_KEY, 200)
        return body, http_status

    def complete(self, *, response, issue=None, status=AutomationOperationStatus.SUCCEEDED, http_status=201):
        """
        @description Record the answer the caller is getting, so a later retry
        can be given the same one.
        @param response: The response body, as sent.
        @param issue: The work item the operation touched, when there is one.
        @param http_status: Stored alongside the body so a replay reproduces
            the original status too.
        """
        complete_operation(self, response=response, issue=issue, status=status, http_status=http_status)

    def fail(self, *, error_code, response=None, http_status=400):
        """@description Record that the operation failed, and why."""
        fail_operation(self, error_code=error_code, response=response, http_status=http_status)


def _snapshot(response, http_status):
    """Store the status inside the snapshot so a replay can reproduce it."""
    body = dict(response or {})
    body[SNAPSHOT_STATUS_KEY] = http_status
    return body


def _open(workspace, api_token, key, operation_type, request_hash) -> OperationHandle:
    """Create the receipt, or raise if another caller won the race."""
    operation = AutomationOperation.objects.create(
        workspace=workspace,
        api_token=api_token,
        idempotency_key=key,
        request_hash=request_hash,
        operation_type=operation_type,
        status=AutomationOperationStatus.IN_PROGRESS,
    )
    return OperationHandle(operation)


def _resume(operation, request_hash, api_token) -> OperationHandle:
    """
    Take over a row whose worker never came back.

    @description Rewrites ``created_at`` so the sixty-second clock restarts;
    otherwise a slow operation resumed at second fifty-nine would immediately
    look abandoned to the next caller and be resumed again, in a loop.
    """
    operation.request_hash = request_hash
    operation.api_token = api_token
    operation.created_at = timezone.now()
    operation.error_code = ""
    operation.save(update_fields=["request_hash", "api_token", "created_at", "error_code", "updated_at"])
    return OperationHandle(operation)


def _existing(operation, request_hash, api_token) -> OperationHandle:
    """Decide what an already-present row means for this caller (RFC §6.7)."""
    if operation.request_hash != request_hash:
        # Checked before status on purpose: a changed payload is a caller bug
        # whether the first attempt succeeded, failed or is still running, and
        # saying so is more useful than reporting the first call's outcome.
        raise IdempotencyPayloadMismatch(idempotency_key=operation.idempotency_key)

    if operation.status in (AutomationOperationStatus.SUCCEEDED, AutomationOperationStatus.FAILED):
        return OperationHandle(operation, replayed=True)

    if timezone.now() - operation.created_at < ABANDONED_AFTER:
        raise OperationInProgress(idempotency_key=operation.idempotency_key)

    return _resume(operation, request_hash, api_token)


def start_operation(workspace, api_token, key, operation_type, payload) -> OperationHandle:
    """
    Open or recover the receipt for one ``Idempotency-Key`` (RFC §6.7).

    @description Runs in its own transaction, before and outside the caller's:
    the receipt has to survive a rollback of the work it describes, or a failed
    operation would leave no trace and the next retry would run as if it were
    the first.

    The race between two simultaneous first calls is settled by the unique
    constraint rather than by a lock — the loser catches ``IntegrityError``,
    re-reads the winner's row, and takes the ordinary existing-row path.

    @param workspace: The workspace the key is scoped to.
    @param api_token: The credential making the call; may be ``None``.
    @param key: The caller's ``Idempotency-Key``.
    @param operation_type: Member of ``AutomationOperationType``.
    @param payload: The request body, for the hash.
    @returns A handle: either open, or marked ``replayed``.
    @raises IdempotencyPayloadMismatch: Key reused with a different payload.
    @raises OperationInProgress: The first call is still running.
    """
    request_hash = canonical_hash(payload)

    existing = AutomationOperation.objects.filter(workspace=workspace, idempotency_key=key).first()
    if existing is not None:
        return _existing(existing, request_hash, api_token)

    try:
        # Its own atomic block so a lost race marks only this INSERT as failed;
        # without it the IntegrityError would poison the caller's transaction.
        with transaction.atomic():
            return _open(workspace, api_token, key, operation_type, request_hash)
    except IntegrityError:
        winner = AutomationOperation.objects.get(workspace=workspace, idempotency_key=key)
        return _existing(winner, request_hash, api_token)


def complete_operation(
    handle,
    *,
    response,
    issue=None,
    status=AutomationOperationStatus.SUCCEEDED,
    http_status=201,
    error_code="",
):
    """
    @description Write the outcome onto the receipt. Uses its own transaction
    for the same reason ``start_operation`` does.
    @param error_code: Symbolic Orca code when the outcome is a failure. Kept
        in its own column as well as in the body, so "which operations failed,
        and why" is a query rather than a JSON scan.
    """
    operation = handle.operation
    operation.status = status
    operation.issue = issue
    operation.response_snapshot = _snapshot(response, http_status)
    operation.error_code = error_code or ""
    operation.completed_at = timezone.now()
    with transaction.atomic():
        operation.save(
            update_fields=["status", "issue", "response_snapshot", "error_code", "completed_at", "updated_at"]
        )


def fail_operation(handle, *, error_code, response=None, http_status=400):
    """
    @description Record a failure, so a retry of a request that cannot succeed
    is answered rather than re-attempted.

    Called outside the caller's transaction — see ``begin_operation`` — because
    the interesting case is exactly the one where that transaction rolled back.
    """
    body = response if response is not None else {"error_code": error_code}
    complete_operation(
        handle,
        response=body,
        issue=None,
        status=AutomationOperationStatus.FAILED,
        http_status=http_status,
        error_code=error_code,
    )


@contextmanager
def begin_operation(workspace, api_token, key, operation_type, payload):
    """
    ``start_operation`` with a guarantee that the row never stays in progress.

    @description An unhandled exception inside the block marks the receipt
    failed with ``ORG_INTERNAL_ERROR`` and re-raises. Without that, a crash
    would leave the key wedged for sixty seconds and then hand the next retry a
    resumed operation — which is recoverable, but tells the caller nothing.

    The failure is written **after** the caller's ``atomic()`` block has
    unwound, which is the whole reason ``start_operation`` and
    ``complete_operation`` open transactions of their own: a write made inside
    a transaction that is rolling back does not survive it, and this is the
    case where surviving matters most.

    Domain errors raised deliberately by the endpoint — a forbidden mode, an
    ineligible executor — are *not* recorded here. The endpoint knows the code
    and the status those deserve and calls ``handle.fail`` itself; catching
    them here would flatten every one of them into a generic internal error.

    @yields An ``OperationHandle``.
    """
    handle = start_operation(workspace, api_token, key, operation_type, payload)
    try:
        yield handle
    except Exception:
        if handle.is_open:
            fail_operation(
                handle,
                error_code="ORG_INTERNAL_ERROR",
                response={"error_code": "ORG_INTERNAL_ERROR"},
                http_status=500,
            )
        raise
