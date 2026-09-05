# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The two tables behind the public automation API.

Both exist to make retries safe, and both do it with a database constraint
rather than with application logic — because the race these guard against is
two requests arriving at once, which no amount of "check then write" in Python
survives. These tests pin the constraints themselves.
"""

import uuid

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from plane.db.models import (
    AutomationOperation,
    AutomationOperationStatus,
    AutomationOperationType,
    ExternalWorkItemBinding,
)


def make_operation(workspace, key="key-1", **kwargs):
    defaults = {
        "workspace": workspace,
        "idempotency_key": key,
        "request_hash": "a" * 64,
        "operation_type": AutomationOperationType.CREATE_WORK_ITEM,
    }
    defaults.update(kwargs)
    return AutomationOperation.objects.create(**defaults)


@pytest.mark.unit
@pytest.mark.django_db
class TestTheExternalBinding:
    def test_one_external_key_maps_to_one_work_item(self, workspace_with_members, project, make_issue):
        first = make_issue(project)
        second = make_issue(project)
        ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=first
        )

        # Without this the same webhook, redelivered, would create a second
        # work item and nothing would record that they are the same thing.
        with pytest.raises(IntegrityError), transaction.atomic():
            ExternalWorkItemBinding.objects.create(
                workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=second
            )

    def test_the_same_external_id_from_another_source_is_a_different_thing(
        self, workspace_with_members, project, make_issue
    ):
        first = make_issue(project)
        second = make_issue(project)
        ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=first
        )
        # Two systems numbering their records from 1 must not collide.
        ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members, external_source="crm", external_id="c-1", issue=second
        )
        assert ExternalWorkItemBinding.objects.count() == 2

    def test_the_same_external_key_in_another_workspace_is_a_different_thing(
        self, workspace_with_members, other_workspace, project, foreign_project, make_issue, admin_user
    ):
        from plane.db.models import Issue, State

        mine = make_issue(project)
        state, _ = State.objects.get_or_create(
            project=foreign_project, group="unstarted", defaults={"name": "unstarted-state"}
        )
        theirs = Issue.objects.create(name="Theirs", project=foreign_project, workspace=other_workspace, state=state)

        ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=mine
        )
        ExternalWorkItemBinding.objects.create(
            workspace=other_workspace, external_source="espo", external_id="c-1", issue=theirs
        )
        assert ExternalWorkItemBinding.objects.count() == 2

    def test_a_work_item_carries_at_most_one_binding(self, workspace_with_members, project, make_issue):
        issue = make_issue(project)
        ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=issue
        )

        # Two external keys claiming one work item would make "which external
        # record is this?" unanswerable.
        with pytest.raises(IntegrityError), transaction.atomic():
            ExternalWorkItemBinding.objects.create(
                workspace=workspace_with_members, external_source="crm", external_id="other", issue=issue
            )

    def test_a_soft_deleted_binding_frees_the_key(self, workspace_with_members, project, make_issue):
        first = make_issue(project)
        second = make_issue(project)
        binding = ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=first
        )
        binding.deleted_at = timezone.now()
        binding.save(update_fields=["deleted_at"])

        # Both constraints are conditional on deleted_at, so a retired binding
        # does not block a workspace from re-binding the key.
        ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=second
        )

    def test_the_workspace_is_taken_from_the_work_item(self, workspace_with_members, project, make_issue):
        issue = make_issue(project)
        binding = ExternalWorkItemBinding(external_source="espo", external_id="c-1", issue=issue)
        binding.save()
        assert binding.workspace_id == workspace_with_members.id


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAutomationOperation:
    def test_one_idempotency_key_per_workspace(self, workspace_with_members):
        make_operation(workspace_with_members, "key-1")
        with pytest.raises(IntegrityError), transaction.atomic():
            make_operation(workspace_with_members, "key-1")

    def test_the_same_key_in_another_workspace_is_a_different_operation(self, workspace_with_members, other_workspace):
        make_operation(workspace_with_members, "key-1")
        make_operation(other_workspace, "key-1")
        assert AutomationOperation.objects.count() == 2

    def test_a_soft_deleted_operation_does_not_free_the_key(self, workspace_with_members):
        """
        The one Orca uniqueness rule with no ``deleted_at`` condition.

        A spent key has to stay spent: if retiring the receipt freed it, a
        retry arriving after a cleanup would execute the operation a second
        time, which is exactly what the table exists to prevent.
        """
        operation = make_operation(workspace_with_members, "key-1")
        operation.deleted_at = timezone.now()
        operation.save(update_fields=["deleted_at"])

        with pytest.raises(IntegrityError), transaction.atomic():
            make_operation(workspace_with_members, "key-1")

    def test_it_starts_in_progress(self, workspace_with_members):
        assert make_operation(workspace_with_members).status == AutomationOperationStatus.IN_PROGRESS

    def test_the_request_hash_holds_a_full_sha256(self, workspace_with_members):
        digest = "b" * 64
        operation = make_operation(workspace_with_members, request_hash=digest)
        operation.refresh_from_db()
        # 64 chars is the whole point: a truncated hash collides.
        assert operation.request_hash == digest
        assert len(operation.request_hash) == 64

    def test_an_unknown_status_is_rejected(self, workspace_with_members):
        operation = make_operation(workspace_with_members)
        operation.status = "halfway"
        with pytest.raises(Exception):
            operation.full_clean()

    def test_an_unknown_operation_type_is_rejected(self, workspace_with_members):
        operation = make_operation(workspace_with_members)
        operation.operation_type = "teleport"
        with pytest.raises(Exception):
            operation.full_clean()

    def test_it_survives_the_token_that_made_it(self, workspace_with_members, admin_user):
        from plane.db.models import APIToken

        token = APIToken.objects.create(user=admin_user, workspace=workspace_with_members, label="t")
        operation = make_operation(workspace_with_members, api_token=token)
        token.delete()
        operation.refresh_from_db()
        # The receipt outlives the credential: SET_NULL, not CASCADE. Deleting
        # a token must not erase the record of what it did.
        assert operation.api_token_id is None
        assert AutomationOperation.objects.filter(pk=operation.pk).exists()


@pytest.mark.unit
@pytest.mark.django_db
class TestTheDecisionPointsAtTheOperation:
    """
    The FK 0137 deliberately left out (D0.4) — a foreign key cannot point at a
    table that does not exist yet, so it lands with 0138.
    """

    def test_a_decision_can_name_the_operation_that_caused_it(
        self, workspace_with_members, project, unit, make_issue, link_project
    ):
        from plane.db.models import AssignmentDecision

        link_project(unit, project)
        issue = make_issue(project)
        operation = make_operation(workspace_with_members)

        decision = AssignmentDecision.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            automation_operation=operation,
            trigger="public_api",
            effective_mode="manual",
            policy_source="fallback",
            algorithm_version="lb-1",
            outcome="queued",
            candidates_snapshot=[],
        )
        assert decision.automation_operation_id == operation.id
        assert list(operation.assignment_decisions.all()) == [decision]

    def test_a_decision_taken_outside_the_api_has_none(
        self, workspace_with_members, project, unit, make_issue, link_project
    ):
        from plane.db.models import AssignmentDecision

        link_project(unit, project)
        decision = AssignmentDecision.objects.create(
            issue=make_issue(project),
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            trigger="ui_claim",
            effective_mode="self_claim",
            policy_source="fallback",
            algorithm_version="lb-1",
            outcome="assigned",
            candidates_snapshot=[],
        )
        assert decision.automation_operation_id is None


@pytest.mark.unit
@pytest.mark.django_db
def test_the_binding_str_names_both_sides(workspace_with_members, project, make_issue):
    issue = make_issue(project)
    binding = ExternalWorkItemBinding.objects.create(
        workspace=workspace_with_members, external_source="espo", external_id="c-1", issue=issue
    )
    assert "espo:c-1" in str(binding)
    assert str(issue.id) in str(binding)


@pytest.mark.unit
@pytest.mark.django_db
def test_the_operation_str_names_key_and_status(workspace_with_members):
    operation = make_operation(workspace_with_members, key=f"k-{uuid.uuid4()}")
    rendered = str(operation)
    assert operation.idempotency_key in rendered
    assert "in_progress" in rendered
