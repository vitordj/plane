# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Retention for the automation API's idempotency receipts.

``AutomationOperation`` gains a row per accepted mutation, each carrying the
whole response body, and until this task nothing ever removed one. The window
is the easy half to test; the hard half is that expiring a receipt **un-spends
its idempotency key**, which is the opposite of the guarantee the table exists
to provide.

So the tests that matter here are not the ones about the cutoff. They are the
ones that pin what a key arriving after its receipt is gone actually does: a
creation must still resolve to the same work item through the binding this task
never touches, and must not re-run the allocation. If that ever stops being
true, no retention window is safe and this task has to go.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.bgtasks.orca_automation_cleanup_task import delete_orca_automation_operations
from plane.db.models import (
    AssignmentDecision,
    AutomationOperation,
    AutomationOperationStatus,
    AutomationOperationType,
    ExternalWorkItemBinding,
    Issue,
    IssueAssignee,
    State,
    StateGroup,
)

from .conftest import ROLE_MEMBER, public_work_items_url


@pytest.fixture
def world(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user):
    """An area that covers the project, with two people who can hold its work."""
    link_project(unit, project, ROLE_MEMBER)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    return unit


@pytest.fixture
def caller(world, admin_user, project, grant_manual_access, token_client):
    """An API-key client whose user may create work in the project."""
    grant_manual_access(project, admin_user)
    return token_client(admin_user)


def body(external_id="cliente-1", mode="default"):
    return {
        "external": {"source": "espo-onboarding", "id": external_id},
        "work_item": {"name": "Validate registration documents"},
        "responsibility": {"unit": "compliance", "assignment": {"mode": mode}},
    }


def post(client, project, payload, key="key-1"):
    return client.post(
        public_work_items_url(project.workspace.slug, project.id),
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )


def receipt(workspace, *, key, age_days, status=AutomationOperationStatus.SUCCEEDED):
    """
    A receipt of a given age.

    @description ``created_at`` is auto-populated, so the age is applied with an
    update after the fact — ``auto_now_add`` ignores a value passed to
    ``create``, and a test that trusted it would pass against any window.
    """
    operation = AutomationOperation.objects.create(
        workspace=workspace,
        idempotency_key=key,
        request_hash="0" * 64,
        operation_type=AutomationOperationType.CREATE_WORK_ITEM,
        status=status,
        response_snapshot={"body": {"ok": True}, "http_status": 201},
        completed_at=None if status == AutomationOperationStatus.IN_PROGRESS else timezone.now(),
    )
    AutomationOperation.all_objects.filter(pk=operation.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    return operation


@pytest.mark.unit
class TestTheWindow:
    def test_a_receipt_past_the_window_is_deleted(self, db, workspace_with_members, settings):
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        old = receipt(workspace_with_members, key="old", age_days=31)

        delete_orca_automation_operations()

        # all_objects, not objects: a soft delete would leave the row owning the
        # key forever and would not reclaim any of the space this task exists for.
        assert not AutomationOperation.all_objects.filter(pk=old.pk).exists()

    def test_a_receipt_inside_the_window_survives(self, db, workspace_with_members, settings):
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        recent = receipt(workspace_with_members, key="recent", age_days=29)

        delete_orca_automation_operations()

        assert AutomationOperation.all_objects.filter(pk=recent.pk).exists()

    def test_the_window_is_read_from_settings(self, db, workspace_with_members, settings):
        # The point of the setting is that an operator can shorten or lengthen
        # it; a hard-coded 30 would pass the two tests above just as well.
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 7
        middle = receipt(workspace_with_members, key="middle", age_days=10)

        delete_orca_automation_operations()

        assert not AutomationOperation.all_objects.filter(pk=middle.pk).exists()

    def test_a_window_of_zero_expires_everything(self, db, workspace_with_members, settings):
        # 0 reads like "off" and is the opposite: the cutoff becomes now. Same as
        # the upstream windows, and pinned here because an operator reaching for
        # 0 to stop the task would instead expire every receipt in the instance.
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 0
        fresh = receipt(workspace_with_members, key="fresh", age_days=0)

        delete_orca_automation_operations()

        assert not AutomationOperation.all_objects.filter(pk=fresh.pk).exists()

    def test_an_abandoned_in_progress_receipt_is_collected(self, db, workspace_with_members, settings):
        # §6.7 treats an in-progress row older than sixty seconds as abandoned.
        # One older than the window has no live request behind it, and skipping
        # those would leak the only receipts nothing else ever resolves.
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        stuck = receipt(
            workspace_with_members,
            key="stuck",
            age_days=31,
            status=AutomationOperationStatus.IN_PROGRESS,
        )

        delete_orca_automation_operations()

        assert not AutomationOperation.all_objects.filter(pk=stuck.pk).exists()

    def test_receipts_of_other_ages_are_untouched_in_one_run(self, db, workspace_with_members, settings):
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        keep = [receipt(workspace_with_members, key=f"keep-{i}", age_days=i) for i in range(0, 30, 10)]
        drop = [receipt(workspace_with_members, key=f"drop-{i}", age_days=i) for i in (31, 60, 400)]

        delete_orca_automation_operations()

        assert AutomationOperation.all_objects.filter(pk__in=[o.pk for o in keep]).count() == len(keep)
        assert not AutomationOperation.all_objects.filter(pk__in=[o.pk for o in drop]).exists()

    def test_a_receipt_named_by_a_decision_survives_the_window(
        self, workspace_with_members, project, unit, make_issue, settings
    ):
        """R1.A3: deleting the receipt would SET NULL the append-only decision."""
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        named = receipt(workspace_with_members, key="named", age_days=31)
        orphan = receipt(workspace_with_members, key="orphan", age_days=31)
        decision = AssignmentDecision.objects.create(
            issue=make_issue(project),
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            automation_operation=named,
            trigger="public_api",
            effective_mode="manual",
            policy_source="fallback",
            algorithm_version="lb-1",
            outcome="queued",
            candidates_snapshot=[],
        )

        delete_orca_automation_operations()

        named.refresh_from_db()
        decision.refresh_from_db()
        assert AutomationOperation.all_objects.filter(pk=named.pk).exists()
        assert decision.automation_operation_id == named.id
        assert not AutomationOperation.all_objects.filter(pk=orphan.pk).exists()


@pytest.mark.unit
class TestWhatExpiringAKeyCosts:
    """
    The reason a window is safe at all. Each of these would be a reason to
    delete this task rather than tune it.
    """

    def test_the_binding_outlives_the_receipt(self, caller, project, world, settings):
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        created = post(caller, project, body())
        assert created.status_code == 201, created.data
        AutomationOperation.all_objects.update(created_at=timezone.now() - timedelta(days=31))

        delete_orca_automation_operations()

        # The identity map is what stops a redelivered event creating a second
        # work item, and this task must never be the thing that removes it.
        assert ExternalWorkItemBinding.objects.filter(
            workspace=project.workspace, external_source="espo-onboarding", external_id="cliente-1"
        ).exists()
        # R1.A3: a successful create named a decision, so the receipt stays and
        # the decision still points at it. The binding surviving is the older
        # guarantee; the FK surviving is the new one.
        operation = AutomationOperation.all_objects.get()
        decision = AssignmentDecision.objects.get(issue_id=created.data["work_item"]["id"])
        assert decision.automation_operation_id == operation.id

    def test_a_successful_create_keeps_its_key_past_the_window(self, caller, project, world, settings):
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        first = post(caller, project, body())
        issue_id = first.data["work_item"]["id"]
        AutomationOperation.all_objects.update(created_at=timezone.now() - timedelta(days=31))
        delete_orca_automation_operations()

        again = post(caller, project, body())

        # The receipt is still there (R1.A3), so the key is still spent and
        # this is a replay of the original snapshot, not a second create.
        assert again.status_code == 201, again.data
        assert again["Idempotent-Replay"] == "true"
        assert again.data["work_item"]["id"] == issue_id
        assert Issue.objects.filter(project=project).count() == 1
        assert ExternalWorkItemBinding.objects.filter(workspace=project.workspace).count() == 1
        assert AutomationOperation.all_objects.count() == 1

    def test_the_allocation_is_not_re_run_after_the_window(self, caller, project, world, settings):
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        first = post(caller, project, body(mode="least_loaded"))
        issue_id = first.data["work_item"]["id"]
        decision_id = first.data["decision"]["id"]
        assignees_before = set(IssueAssignee.objects.filter(issue_id=issue_id).values_list("assignee_id", flat=True))
        AutomationOperation.all_objects.update(created_at=timezone.now() - timedelta(days=31))
        delete_orca_automation_operations()

        again = post(caller, project, body(mode="least_loaded"))

        # The receipt survived, so this is a replay and cannot re-rank.
        assert again.data["decision"]["id"] == decision_id
        assert AssignmentDecision.objects.filter(issue_id=issue_id).count() == 1
        assert (
            set(IssueAssignee.objects.filter(issue_id=issue_id).values_list("assignee_id", flat=True))
            == assignees_before
        )

    def test_an_unexpired_key_still_replays(self, caller, project, world, settings):
        # The guarantee the table exists for, unchanged by the task existing: a
        # run of the task must not expire a receipt inside the window.
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        first = post(caller, project, body())

        delete_orca_automation_operations()
        replay = post(caller, project, body())

        # A replayed creation answers the *recorded* response, 201 included --
        # the status is not what distinguishes a replay from a fresh call.
        assert replay.status_code == 201, replay.data
        assert replay["Idempotent-Replay"] == "true"
        assert replay.data["operation"]["replay"] is True
        assert replay.data["binding"]["created"] is True
        assert replay.data["work_item"]["id"] == first.data["work_item"]["id"]
        assert AutomationOperation.all_objects.count() == 1

    def test_an_unreferenced_key_is_still_unspent_after_expiry(self, caller, project, world, settings):
        """Unreferenced receipts still expire; that path is how a key becomes unspent."""
        settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS = 30
        orphan = receipt(project.workspace, key="orphan-key", age_days=31)

        delete_orca_automation_operations()

        assert not AutomationOperation.all_objects.filter(pk=orphan.pk).exists()
        after = post(caller, project, body(external_id="other-1"), key="orphan-key")

        assert after.status_code == 201, after.data
        assert after.get("Idempotent-Replay") is None
        assert after.data["operation"]["replay"] is False
        assert AutomationOperation.all_objects.filter(idempotency_key="orphan-key").exists()
