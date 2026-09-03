# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Every branch of the idempotency contract.

A robot cannot tell a timeout from a slow success, so it retries. Each branch
below is one way that retry can go, and the file exists because the wrong
answer to any of them is either a duplicate work item or a key nobody can ever
use again.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.app.services.orca.automation_operation import (
    begin_operation,
    canonical_hash,
    complete_operation,
    fail_operation,
)
from plane.app.services.orca.errors import IdempotencyPayloadMismatch, OperationInProgress
from plane.db.models import AutomationOperation

PAYLOAD = {"work_item": {"name": "Validate documents"}, "responsibility": {"unit": "compliance"}}


def claim(workspace, key="key-1", payload=None, operation_type="create_work_item"):
    return begin_operation(workspace, None, key, operation_type, payload if payload is not None else PAYLOAD)


@pytest.mark.unit
class TestTheFingerprint:
    def test_key_order_does_not_change_it(self):
        """Two JSON libraries, one request: the caller must not pay for that."""
        first = canonical_hash({"a": 1, "b": {"c": 2, "d": 3}})
        second = canonical_hash({"b": {"d": 3, "c": 2}, "a": 1})

        assert first == second

    def test_a_different_value_changes_it(self):
        assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})

    def test_it_is_a_full_sha256(self):
        assert len(canonical_hash({"a": 1})) == 64


@pytest.mark.unit
@pytest.mark.django_db
class TestTheBranches:
    def test_a_new_key_is_claimed(self, workspace_with_members):
        handle = claim(workspace_with_members)

        assert handle.replayed is False
        assert handle.operation.status == "in_progress"

    def test_a_finished_key_replays_the_original_answer(self, workspace_with_members):
        first = claim(workspace_with_members)
        complete_operation(first, response={"work_item": {"id": "abc"}})

        second = claim(workspace_with_members)

        assert second.replayed is True
        assert second.snapshot == {"work_item": {"id": "abc"}}

    def test_a_failed_key_replays_the_original_refusal(self, workspace_with_members):
        """
        Otherwise a caller retrying a request that failed for a reason of its
        own gets a fresh attempt every time, forever.
        """
        first = claim(workspace_with_members)
        fail_operation(first, error_code="ORG_UNIT_NOT_COVERING_PROJECT", response={"error_code": 4916})

        second = claim(workspace_with_members)

        assert second.replayed is True
        assert second.status == "failed"
        assert second.snapshot == {"error_code": 4916}

    def test_the_same_key_with_another_payload_is_refused(self, workspace_with_members):
        claim(workspace_with_members)

        with pytest.raises(IdempotencyPayloadMismatch):
            claim(workspace_with_members, payload={"work_item": {"name": "Something else"}})

    def test_a_call_still_in_flight_asks_the_caller_to_wait(self, workspace_with_members):
        claim(workspace_with_members)

        with pytest.raises(OperationInProgress):
            claim(workspace_with_members)

    def test_an_abandoned_claim_is_taken_over(self, workspace_with_members):
        """A crashed worker must not poison that key for good."""
        handle = claim(workspace_with_members)
        AutomationOperation.objects.filter(pk=handle.operation.pk).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )

        resumed = claim(workspace_with_members)

        assert resumed.replayed is False
        assert resumed.operation.pk == handle.operation.pk

    def test_keys_are_scoped_to_a_workspace(self, workspace_with_members, other_workspace):
        claim(workspace_with_members)

        handle = claim(other_workspace)

        assert handle.replayed is False


@pytest.mark.unit
@pytest.mark.django_db
class TestTheHandleClosesTheReceipt:
    def test_an_unexpected_error_leaves_the_receipt_failed(self, workspace_with_members):
        """
        The receipt is the only evidence the call was ever attempted. If a bug
        left it in progress, the key would be unusable until it aged out.
        """
        with pytest.raises(RuntimeError):
            with claim(workspace_with_members) as handle:
                raise RuntimeError("boom")

        handle.operation.refresh_from_db()
        assert handle.operation.status == "failed"
        assert handle.operation.error_code == "ORG_INTERNAL_ERROR"

    def test_a_completed_operation_is_not_reopened_on_the_way_out(self, workspace_with_members):
        with claim(workspace_with_members) as handle:
            complete_operation(handle, response={"ok": True})

        handle.operation.refresh_from_db()
        assert handle.operation.status == "succeeded"
