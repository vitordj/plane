# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The queue state of the work an area owns.

"Assigned" and "has a primary executor" are the same statement, so the
database says so with two CHECK constraints rather than trusting every future
writer to remember. These tests are what stop somebody removing them later
because "the service already handles it".
"""

import importlib

import pytest
from django.db import IntegrityError, transaction

from plane.db.models import IssueAssignee, IssueOrganizationalUnit
from plane.db.models.organizational_unit import QueueReason, RoutingState

# The backfill lives in the migration, which is where it belongs; the judgement
# it makes is worth a test, so it is written as a function that takes its model
# classes rather than reaching for apps.get_model itself.
migration_0135 = importlib.import_module("plane.db.migrations.0135_orca_issue_routing_state")


@pytest.fixture
def linked_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
class TestTheStateConstraints:
    def test_a_new_link_starts_queued_with_no_executor(self, workspace_with_members, linked_unit, project, make_issue):
        issue = make_issue(project)

        link = IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=linked_unit, project=project, workspace=workspace_with_members
        )

        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None

    def test_assigned_without_an_executor_is_refused(self, workspace_with_members, linked_unit, project, make_issue):
        issue = make_issue(project)

        with pytest.raises(IntegrityError), transaction.atomic():
            IssueOrganizationalUnit.objects.create(
                issue=issue,
                organizational_unit=linked_unit,
                project=project,
                workspace=workspace_with_members,
                routing_state=RoutingState.ASSIGNED,
            )

    def test_an_executor_without_assigned_is_refused(
        self, workspace_with_members, linked_unit, project, make_issue, plain_user
    ):
        issue = make_issue(project)

        with pytest.raises(IntegrityError), transaction.atomic():
            IssueOrganizationalUnit.objects.create(
                issue=issue,
                organizational_unit=linked_unit,
                project=project,
                workspace=workspace_with_members,
                routing_state=RoutingState.QUEUED,
                queue_reason=QueueReason.NEW_ITEM,
                primary_executor=plain_user,
            )

    def test_assigned_with_an_executor_is_allowed(
        self, workspace_with_members, linked_unit, project, make_issue, plain_user
    ):
        issue = make_issue(project)

        link = IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=linked_unit,
            project=project,
            workspace=workspace_with_members,
            routing_state=RoutingState.ASSIGNED,
            primary_executor=plain_user,
        )

        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == plain_user.id


@pytest.mark.unit
@pytest.mark.django_db
class TestTheDataMigrationRule:
    """
    The 0135 data migration in isolation: it is the only part of the migration
    that makes a judgement, so the judgement is tested rather than the SQL.
    """

    def test_a_work_item_with_an_assignee_becomes_assigned(
        self, workspace_with_members, linked_unit, project, make_issue, plain_user, second_user
    ):
        issue = make_issue(project)
        link = IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=linked_unit, project=project, workspace=workspace_with_members
        )
        older = IssueAssignee.objects.create(
            issue=issue, assignee=second_user, project=project, workspace=workspace_with_members
        )
        IssueAssignee.objects.create(
            issue=issue, assignee=plain_user, project=project, workspace=workspace_with_members
        )

        migration_0135.backfill_routing_state(
            IssueOrganizationalUnit, IssueAssignee, queryset=IssueOrganizationalUnit.objects.filter(pk=link.pk)
        )

        link.refresh_from_db()
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == older.assignee_id

    def test_a_work_item_with_nobody_on_it_is_queued_as_new(
        self, workspace_with_members, linked_unit, project, make_issue
    ):
        issue = make_issue(project)
        link = IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=linked_unit, project=project, workspace=workspace_with_members
        )

        migration_0135.backfill_routing_state(
            IssueOrganizationalUnit, IssueAssignee, queryset=IssueOrganizationalUnit.objects.filter(pk=link.pk)
        )

        link.refresh_from_db()
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.NEW_ITEM
        assert link.queued_at is not None

    def test_running_it_twice_changes_nothing(
        self, workspace_with_members, linked_unit, project, make_issue, plain_user
    ):
        issue = make_issue(project)
        link = IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=linked_unit, project=project, workspace=workspace_with_members
        )
        IssueAssignee.objects.create(
            issue=issue, assignee=plain_user, project=project, workspace=workspace_with_members
        )
        links = IssueOrganizationalUnit.objects.filter(pk=link.pk)

        migration_0135.backfill_routing_state(IssueOrganizationalUnit, IssueAssignee, queryset=links)
        second_pass = migration_0135.backfill_routing_state(IssueOrganizationalUnit, IssueAssignee, queryset=links)

        assert second_pass == 0
        link.refresh_from_db()
        assert link.primary_executor_id == plain_user.id
