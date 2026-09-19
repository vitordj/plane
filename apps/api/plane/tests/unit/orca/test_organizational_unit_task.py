# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the asynchronous reconciliation path.

Two responsibilities are tested separately, because they fail differently:

1. **The decision to enqueue.** Small fan-outs must stay inline so the API
   response reflects final state; large ones must be handed to Celery only
   after the transaction commits, never before.
2. **The task itself.** It runs against a real database, long after the
   request that queued it, so it has to re-derive access from the ids it was
   given rather than replay a decision made at enqueue time.
"""

import uuid
from unittest import mock

import pytest
from django.db import transaction

from plane.app.services.orca.org_unit_reconciler import dispatch_reconciliation
from plane.bgtasks.organizational_unit_task import reconcile_organizational_access
from plane.db.models import OrganizationalUnitMembership, Project, ProjectMember

from .conftest import ROLE_MEMBER

TASK_PATH = "plane.bgtasks.organizational_unit_task.reconcile_organizational_access.delay"


@pytest.mark.unit
class TestReconciliationDispatch:
    def test_a_small_fan_out_runs_inline_and_queues_nothing(
        self, settings, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        settings.ORCA_ORG_SYNC_MAX_EDGES = 100
        link_project(unit, project)
        membership = add_member(unit, plain_user)

        with mock.patch(TASK_PATH) as queued:
            changes = dispatch_reconciliation(
                workspace_with_members.id,
                member_ids=[membership.workspace_member_id],
                project_ids=[project.id],
            )

        assert queued.call_count == 0
        assert changes is not None
        assert ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_a_large_fan_out_is_queued_instead_of_run_inline(
        self,
        settings,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        django_capture_on_commit_callbacks,
    ):
        """Above the threshold the request must not pay for the rewrite."""
        settings.ORCA_ORG_SYNC_MAX_EDGES = 0
        link_project(unit, project)
        membership = add_member(unit, plain_user)

        with mock.patch(TASK_PATH) as queued:
            with django_capture_on_commit_callbacks(execute=True):
                result = dispatch_reconciliation(
                    workspace_with_members.id,
                    member_ids=[membership.workspace_member_id],
                    project_ids=[project.id],
                )

        assert result is None
        assert queued.call_count == 1
        assert not ProjectMember.objects.filter(project=project, member=plain_user).exists()

    def test_the_task_is_only_sent_after_the_transaction_commits(
        self,
        settings,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        django_capture_on_commit_callbacks,
    ):
        """Queuing before commit would let a worker read state that never landed."""
        settings.ORCA_ORG_SYNC_MAX_EDGES = 0
        link_project(unit, project)
        membership = add_member(unit, plain_user)

        with mock.patch(TASK_PATH) as queued:
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                dispatch_reconciliation(
                    workspace_with_members.id,
                    member_ids=[membership.workspace_member_id],
                    project_ids=[project.id],
                )
                assert queued.call_count == 0, "task was sent before commit"

        assert len(callbacks) == 1
        assert queued.call_count == 1

    def test_nothing_is_queued_when_the_transaction_rolls_back(
        self,
        settings,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        django_capture_on_commit_callbacks,
    ):
        settings.ORCA_ORG_SYNC_MAX_EDGES = 0
        link_project(unit, project)
        membership = add_member(unit, plain_user)

        with mock.patch(TASK_PATH) as queued:
            with django_capture_on_commit_callbacks(execute=True):
                try:
                    with transaction.atomic():
                        dispatch_reconciliation(
                            workspace_with_members.id,
                            member_ids=[membership.workspace_member_id],
                            project_ids=[project.id],
                        )
                        raise RuntimeError("request failed after dispatch")
                except RuntimeError:
                    pass

        assert queued.call_count == 0

    def test_the_queued_payload_carries_scope_ids_not_a_decision(
        self,
        settings,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        django_capture_on_commit_callbacks,
    ):
        """
        The worker must recompute; anything resembling a pre-baked role in the
        payload would go stale between enqueue and execution.
        """
        settings.ORCA_ORG_SYNC_MAX_EDGES = 0
        link_project(unit, project)
        membership = add_member(unit, plain_user)

        with mock.patch(TASK_PATH) as queued:
            with django_capture_on_commit_callbacks(execute=True):
                dispatch_reconciliation(
                    workspace_with_members.id,
                    member_ids=[membership.workspace_member_id],
                    project_ids=[project.id],
                )

        args, kwargs = queued.call_args
        assert args == (
            str(workspace_with_members.id),
            [str(membership.workspace_member_id)],
            [str(project.id)],
        )
        assert kwargs == {}


@pytest.mark.unit
class TestReconciliationTask:
    def test_the_task_materializes_project_members(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)

        reconcile_organizational_access(
            str(workspace_with_members.id), [str(membership.workspace_member_id)], [str(project.id)]
        )

        project_member = ProjectMember.objects.get(project=project, member=plain_user)
        assert project_member.role == ROLE_MEMBER
        assert project_member.is_active is True

    def test_running_the_task_twice_changes_nothing(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """A redelivered message must not compound its effect."""
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        args = (str(workspace_with_members.id), [str(membership.workspace_member_id)], [str(project.id)])

        reconcile_organizational_access(*args)
        first = ProjectMember.objects.get(project=project, member=plain_user)

        reconcile_organizational_access(*args)

        assert ProjectMember.objects.filter(project=project, member=plain_user).count() == 1
        second = ProjectMember.objects.get(project=project, member=plain_user)
        assert second.id == first.id
        assert second.role == first.role

    def test_the_task_reflects_state_changed_after_it_was_queued(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        """
        The source is removed between enqueue and execution: the worker must
        act on the world it finds, not the one that queued it.
        """
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        args = (str(workspace_with_members.id), [str(membership.workspace_member_id)], [str(project.id)])

        OrganizationalUnitMembership.objects.filter(pk=membership.pk).update(is_active=False)

        reconcile_organizational_access(*args)

        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_the_task_withdraws_access_when_the_unit_was_deactivated(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        args = (str(workspace_with_members.id), [str(membership.workspace_member_id)], [str(project.id)])
        reconcile_organizational_access(*args)
        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is True

        unit.is_active = False
        unit.save()

        reconcile_organizational_access(*args)

        assert ProjectMember.objects.get(project=project, member=plain_user).is_active is False

    def test_the_task_survives_a_source_deleted_before_it_ran(
        self, workspace_with_members, unit, project, link_project, add_member, plain_user
    ):
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        args = (str(workspace_with_members.id), [str(membership.workspace_member_id)], [str(project.id)])
        unit.delete()

        reconcile_organizational_access(*args)

        assert not ProjectMember.objects.filter(project=project, member=plain_user, is_active=True).exists()

    def test_an_unknown_scope_is_a_no_op(self, db, workspace_with_members):
        reconcile_organizational_access(str(uuid.uuid4()), [str(uuid.uuid4())], [str(uuid.uuid4())])

        assert ProjectMember.objects.count() == 0

    def test_a_large_project_list_is_reconciled_in_batches(
        self, monkeypatch, workspace_with_members, admin_user, unit, link_project, add_member, plain_user
    ):
        """Every project must be covered even when the work spans batches."""
        monkeypatch.setattr("plane.bgtasks.organizational_unit_task.PROJECT_BATCH_SIZE", 2)
        projects = [
            Project.objects.create(
                name=f"Project {index}",
                identifier=f"PRJ{index}",
                workspace=workspace_with_members,
                created_by=admin_user,
            )
            for index in range(5)
        ]
        for created in projects:
            link_project(unit, created, ROLE_MEMBER)
        membership = add_member(unit, plain_user)

        reconcile_organizational_access(
            str(workspace_with_members.id),
            [str(membership.workspace_member_id)],
            [str(created.id) for created in projects],
        )

        assert ProjectMember.objects.filter(project__in=projects, member=plain_user, is_active=True).count() == 5

    def test_without_a_project_scope_the_whole_workspace_is_reconciled(
        self, workspace_with_members, unit, project, second_project, link_project, add_member, plain_user
    ):
        link_project(unit, project, ROLE_MEMBER)
        link_project(unit, second_project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)

        reconcile_organizational_access(str(workspace_with_members.id), [str(membership.workspace_member_id)], None)

        assert ProjectMember.objects.filter(member=plain_user, is_active=True).count() == 2

    def test_a_failing_reconcile_is_retried(self, workspace_with_members, unit, project, link_project):
        """
        Failures must go back on the queue rather than be swallowed.

        Dispatched through ``apply`` rather than called directly: Celery only
        honours ``self.retry`` when the task runs with a real request bound,
        which is exactly how a worker executes it.
        """
        from celery.exceptions import Retry

        link_project(unit, project, ROLE_MEMBER)

        def explode(*args, **kwargs):
            raise RuntimeError("database unavailable")

        with mock.patch("plane.app.services.orca.reconcile_access", side_effect=explode):
            with pytest.raises(Retry) as retried:
                reconcile_organizational_access.apply(args=[str(workspace_with_members.id), None, [str(project.id)]])

        assert isinstance(retried.value.exc, RuntimeError)
