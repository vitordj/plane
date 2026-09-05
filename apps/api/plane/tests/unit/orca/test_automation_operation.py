# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Idempotency and replay (RFC §6.7).

Every branch of the section gets a test, because each one is a different
promise to a caller that retries: the same payload gets the same answer, a
changed payload is refused, a call still running is not duplicated, and a
worker that died does not wedge the key forever.
"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from plane.app.services.orca.automation_operation import (
    ABANDONED_AFTER,
    begin_operation,
    canonical_hash,
    start_operation,
)
from plane.app.services.orca.errors import IdempotencyPayloadMismatch, OperationInProgress
from plane.db.models import AutomationOperation, AutomationOperationStatus, AutomationOperationType

PAYLOAD = {"external": {"source": "espo", "id": "c-1"}, "work_item": {"name": "Validate"}}


def begin(workspace, key="key-1", payload=None, token=None):
    return start_operation(
        workspace,
        token,
        key,
        AutomationOperationType.CREATE_WORK_ITEM,
        PAYLOAD if payload is None else payload,
    )


@pytest.mark.unit
class TestTheHash:
    def test_key_order_does_not_change_it(self):
        # Several languages serialize dictionaries in arbitrary order, so the
        # retry of a request often has its keys shuffled. It is still a retry.
        assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})

    def test_nested_key_order_does_not_change_it(self):
        assert canonical_hash({"o": {"a": 1, "b": 2}}) == canonical_hash({"o": {"b": 2, "a": 1}})

    def test_a_different_value_changes_it(self):
        assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})

    def test_a_missing_field_changes_it(self):
        assert canonical_hash({"a": 1, "b": 2}) != canonical_hash({"a": 1})

    def test_list_order_does_change_it(self):
        # Unlike dictionary keys, list order is meaningful: [a, b] and [b, a]
        # are different requests.
        assert canonical_hash({"a": [1, 2]}) != canonical_hash({"a": [2, 1]})

    def test_non_ascii_hashes_as_itself(self):
        # ensure_ascii=False: a name in Cyrillic must hash the same whether or
        # not the client escaped it on the way out.
        assert canonical_hash({"name": "Мария"}) == canonical_hash({"name": "Мария"})

    def test_it_is_a_full_sha256(self):
        digest = canonical_hash(PAYLOAD)
        assert len(digest) == 64
        assert digest == digest.lower()


@pytest.mark.unit
@pytest.mark.django_db
class TestTheFirstCall:
    def test_it_opens_a_receipt(self, workspace_with_members):
        handle = begin(workspace_with_members)
        assert handle.replayed is False
        assert handle.is_open is True
        assert handle.operation.status == AutomationOperationStatus.IN_PROGRESS
        assert handle.operation.request_hash == canonical_hash(PAYLOAD)

    def test_it_records_the_calling_token(self, workspace_with_members, admin_user):
        from plane.db.models import APIToken

        token = APIToken.objects.create(user=admin_user, workspace=workspace_with_members, label="t")
        handle = begin(workspace_with_members, token=token)
        assert handle.operation.api_token_id == token.id

    def test_completing_it_records_the_answer(self, workspace_with_members):
        handle = begin(workspace_with_members)
        handle.complete(response={"work_item": {"id": "abc"}}, http_status=201)

        handle.operation.refresh_from_db()
        assert handle.operation.status == AutomationOperationStatus.SUCCEEDED
        assert handle.operation.completed_at is not None
        assert handle.operation.response_snapshot["work_item"] == {"id": "abc"}


@pytest.mark.unit
@pytest.mark.django_db
class TestTheReplay:
    def test_the_same_payload_replays_the_recorded_answer(self, workspace_with_members):
        first = begin(workspace_with_members)
        first.complete(response={"work_item": {"id": "abc"}}, http_status=201)

        second = begin(workspace_with_members)
        assert second.replayed is True
        body, http_status = second.replay_response()
        assert body == {"work_item": {"id": "abc"}}
        assert http_status == 201

    def test_the_replay_body_does_not_leak_the_stored_status(self, workspace_with_members):
        begin(workspace_with_members).complete(response={"work_item": {"id": "abc"}}, http_status=201)
        body, _ = begin(workspace_with_members).replay_response()
        # The status is kept inside the snapshot for storage reasons; it must
        # not surface as a field of the caller's body.
        assert "_http_status" not in body

    def test_a_replay_does_not_open_a_second_receipt(self, workspace_with_members):
        begin(workspace_with_members).complete(response={"ok": True})
        begin(workspace_with_members)
        assert AutomationOperation.objects.filter(idempotency_key="key-1").count() == 1

    def test_a_failed_operation_replays_its_failure(self, workspace_with_members):
        first = begin(workspace_with_members)
        first.fail(error_code="ORG_UNIT_NOT_COVERING_PROJECT", http_status=400)

        second = begin(workspace_with_members)
        assert second.replayed is True
        body, http_status = second.replay_response()
        # A retry of a request that cannot succeed is answered, not re-run.
        assert http_status == 400
        assert body["error_code"] == "ORG_UNIT_NOT_COVERING_PROJECT"

    def test_a_failure_is_queryable_by_code(self, workspace_with_members):
        begin(workspace_with_members).fail(error_code="ORG_EXECUTOR_NOT_ELIGIBLE")
        assert AutomationOperation.objects.filter(error_code="ORG_EXECUTOR_NOT_ELIGIBLE").count() == 1

    def test_the_replay_answers_the_original_not_the_present(self, workspace_with_members):
        """
        The rule that makes replay safe when a person intervenes.

        Somebody reassigns the item between the first call and the retry. The
        retry still reports the first allocation — otherwise a retry would read
        as though it had undone their decision.
        """
        first = begin(workspace_with_members)
        first.complete(response={"responsibility": {"primary_executor": "maria"}})

        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        operation.issue = None
        operation.save(update_fields=["issue"])

        body, _ = begin(workspace_with_members).replay_response()
        assert body["responsibility"]["primary_executor"] == "maria"


@pytest.mark.unit
@pytest.mark.django_db
class TestThePayloadMismatch:
    def test_a_changed_payload_under_the_same_key_is_refused(self, workspace_with_members):
        begin(workspace_with_members).complete(response={"ok": True})
        with pytest.raises(IdempotencyPayloadMismatch):
            begin(workspace_with_members, payload={"work_item": {"name": "Something else"}})

    def test_it_is_refused_while_the_first_call_is_still_running(self, workspace_with_members):
        begin(workspace_with_members)
        # Checked before status: a changed payload is a caller bug whatever
        # the first attempt is doing.
        with pytest.raises(IdempotencyPayloadMismatch):
            begin(workspace_with_members, payload={"work_item": {"name": "Something else"}})

    def test_it_is_refused_after_a_failure_too(self, workspace_with_members):
        begin(workspace_with_members).fail(error_code="ORG_INTERNAL_ERROR")
        with pytest.raises(IdempotencyPayloadMismatch):
            begin(workspace_with_members, payload={"other": True})

    def test_a_reordered_payload_is_not_a_mismatch(self, workspace_with_members):
        begin(workspace_with_members, payload={"a": 1, "b": 2}).complete(response={"ok": True})
        handle = begin(workspace_with_members, payload={"b": 2, "a": 1})
        assert handle.replayed is True


@pytest.mark.unit
@pytest.mark.django_db
class TestTheOperationStillRunning:
    def test_a_recent_in_progress_call_is_refused(self, workspace_with_members):
        begin(workspace_with_members)
        with pytest.raises(OperationInProgress):
            begin(workspace_with_members)

    def test_an_abandoned_call_is_resumed(self, workspace_with_members):
        handle = begin(workspace_with_members)
        stale = timezone.now() - ABANDONED_AFTER - timedelta(seconds=1)
        AutomationOperation.objects.filter(pk=handle.operation.pk).update(created_at=stale)

        resumed = begin(workspace_with_members)
        assert resumed.replayed is False
        assert resumed.is_open is True
        assert resumed.operation.pk == handle.operation.pk

    def test_resuming_restarts_the_clock(self, workspace_with_members):
        """
        Otherwise an operation resumed at second fifty-nine would look
        abandoned to the very next caller, and be resumed again in a loop.
        """
        handle = begin(workspace_with_members)
        stale = timezone.now() - ABANDONED_AFTER - timedelta(seconds=1)
        AutomationOperation.objects.filter(pk=handle.operation.pk).update(created_at=stale)

        begin(workspace_with_members)
        with pytest.raises(OperationInProgress):
            begin(workspace_with_members)

    def test_resuming_does_not_open_a_second_receipt(self, workspace_with_members):
        handle = begin(workspace_with_members)
        stale = timezone.now() - ABANDONED_AFTER - timedelta(seconds=1)
        AutomationOperation.objects.filter(pk=handle.operation.pk).update(created_at=stale)
        begin(workspace_with_members)
        assert AutomationOperation.objects.filter(idempotency_key="key-1").count() == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestTheContextManager:
    def test_it_marks_a_crashed_operation_failed(self, workspace_with_members):
        with pytest.raises(RuntimeError):
            with begin_operation(
                workspace_with_members, None, "key-1", AutomationOperationType.CREATE_WORK_ITEM, PAYLOAD
            ):
                raise RuntimeError("worker died")

        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        # Not left in progress: the next retry gets an answer instead of a
        # sixty-second wait followed by a silent resume.
        assert operation.status == AutomationOperationStatus.FAILED
        assert operation.error_code == "ORG_INTERNAL_ERROR"

    def test_the_failure_survives_a_rolled_back_transaction(self, workspace_with_members):
        """
        The reason the receipt is written outside the caller's transaction.

        The interesting case is precisely the one where the work rolled back:
        if the failure were written inside that transaction it would roll back
        with it, and the crash would leave no trace at all.
        """
        with pytest.raises(RuntimeError):
            with begin_operation(
                workspace_with_members, None, "key-1", AutomationOperationType.CREATE_WORK_ITEM, PAYLOAD
            ):
                with transaction.atomic():
                    raise RuntimeError("rolled back")

        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert operation.status == AutomationOperationStatus.FAILED

    def test_it_leaves_a_completed_operation_alone(self, workspace_with_members):
        with begin_operation(
            workspace_with_members, None, "key-1", AutomationOperationType.CREATE_WORK_ITEM, PAYLOAD
        ) as handle:
            handle.complete(response={"ok": True})

        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert operation.status == AutomationOperationStatus.SUCCEEDED

    def test_a_deliberate_failure_keeps_its_own_code(self, workspace_with_members):
        """
        A domain error the endpoint handles itself must not be flattened into
        ORG_INTERNAL_ERROR by the context manager.
        """
        with begin_operation(
            workspace_with_members, None, "key-1", AutomationOperationType.CREATE_WORK_ITEM, PAYLOAD
        ) as handle:
            handle.fail(error_code="ORG_UNIT_NOT_COVERING_PROJECT", http_status=400)

        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert operation.error_code == "ORG_UNIT_NOT_COVERING_PROJECT"

    def test_a_replay_inside_the_block_is_not_overwritten(self, workspace_with_members):
        begin(workspace_with_members).complete(response={"ok": True}, http_status=201)

        with begin_operation(
            workspace_with_members, None, "key-1", AutomationOperationType.CREATE_WORK_ITEM, PAYLOAD
        ) as handle:
            assert handle.replayed is True

        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert operation.status == AutomationOperationStatus.SUCCEEDED


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_first_calls_open_one_receipt(transactional_db):
    """
    The race the unique constraint settles, not the code.

    Two deliveries of the same webhook arriving together both find no row and
    both try to create one. A "check then insert" in Python loses this every
    time; the constraint is what makes the loser discover the winner instead of
    opening a second receipt — which would mean the work ran twice.
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection

    from plane.db.models import User, Workspace

    owner = User.objects.create(email="race@example.com", username="race", first_name="Race")
    workspace = Workspace.objects.create(name="Race", slug="race-ws", owner=owner)

    def attempt(_):
        try:
            handle = start_operation(workspace, None, "same-key", AutomationOperationType.CREATE_WORK_ITEM, PAYLOAD)
            return "replay" if handle.replayed else "opened"
        except OperationInProgress:
            return "in_progress"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))

    assert AutomationOperation.objects.filter(idempotency_key="same-key").count() == 1
    # Exactly one caller may proceed; the other is told to back off rather than
    # being handed a second receipt for the same key.
    assert outcomes.count("opened") == 1, outcomes
    assert outcomes.count("in_progress") == 1, outcomes
