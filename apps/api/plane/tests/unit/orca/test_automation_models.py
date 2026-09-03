# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The two tables that keep a retry from becoming a duplicate.

Everything here is a uniqueness rule, and every one of them is the difference
between "the robot called twice" and "the customer has two onboarding tasks".
"""

import pytest
from django.db import IntegrityError, transaction

from plane.db.models import AutomationOperation, ExternalWorkItemBinding


@pytest.fixture
def make_binding(workspace_with_members, project, make_issue):
    def _make(source="espo", external_id="client-1", issue=None):
        return ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members,
            external_source=source,
            external_id=external_id,
            issue=issue or make_issue(project),
        )

    return _make


@pytest.fixture
def make_operation(workspace_with_members):
    def _make(key="key-1", **kwargs):
        return AutomationOperation.objects.create(
            workspace=workspace_with_members,
            idempotency_key=key,
            request_hash=kwargs.pop("request_hash", "a" * 64),
            operation_type=kwargs.pop("operation_type", "create_work_item"),
            **kwargs,
        )

    return _make


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
class TestTheBinding:
    def test_i8_one_binding_per_external_record(self, make_binding):
        make_binding(source="espo", external_id="client-1")

        with pytest.raises(IntegrityError), transaction.atomic():
            make_binding(source="espo", external_id="client-1")

    def test_i8_one_binding_per_work_item(self, make_binding, project, make_issue):
        issue = make_issue(project)
        make_binding(external_id="client-1", issue=issue)

        with pytest.raises(IntegrityError), transaction.atomic():
            make_binding(external_id="client-2", issue=issue)

    def test_the_same_id_in_another_source_is_a_different_record(self, make_binding):
        first = make_binding(source="espo", external_id="client-1")
        second = make_binding(source="hr", external_id="client-1")

        assert first.pk != second.pk

    def test_a_cleared_binding_frees_the_reference(self, make_binding):
        """Soft delete keeps the row as history; the reference must be reusable."""
        binding = make_binding(source="espo", external_id="client-1")
        binding.delete()

        assert make_binding(source="espo", external_id="client-1").pk != binding.pk


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
class TestTheOperation:
    def test_i9_a_key_can_only_be_used_once_per_workspace(self, make_operation):
        make_operation(key="abc")

        with pytest.raises(IntegrityError), transaction.atomic():
            make_operation(key="abc")

    def test_the_uniqueness_ignores_soft_deletion(self, make_operation):
        """
        Unlike the rest of the layer, on purpose: this constraint is what makes
        two simultaneous calls race for one row, and a partial index would
        leave that race open for any key whose receipt was soft-deleted.
        """
        operation = make_operation(key="abc")
        operation.delete()

        with pytest.raises(IntegrityError), transaction.atomic():
            make_operation(key="abc")

    def test_a_receipt_starts_in_progress(self, make_operation):
        assert make_operation().status == "in_progress"

    def test_the_hash_holds_a_full_sha256(self, make_operation):
        operation = make_operation(request_hash="f" * 64)

        assert len(operation.request_hash) == 64
