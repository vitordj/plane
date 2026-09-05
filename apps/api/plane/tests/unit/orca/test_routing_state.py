# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Queue state and primary executor on the responsibility link.

Responsibility and assignment used to be one moment: the link existed, and
either somebody was assigned or nobody was. "Waiting to be picked up" and "the
allocator tried and found nobody" were the same thing as far as the database
was concerned, which is precisely the distinction a coordinator's board is
made of.

Two CHECK constraints keep the pair honest, and they are tested against a real
PostgreSQL because a CHECK that only exists in the model is not a constraint.
The third fact — that the executor is also a live ``IssueAssignee`` — is not
expressible as a CHECK and belongs to the service layer (RFC §6.1).
"""

import importlib

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from plane.db.models import (
    IssueAssignee,
    IssueOrganizationalUnit,
    QueueReason,
    RoutingState,
)

from .conftest import ROLE_MEMBER


@pytest.fixture
def link(db, unit, project, link_project, make_issue):
    """A responsibility link on a project the area covers."""
    link_project(unit, project, ROLE_MEMBER)
    issue = make_issue(project)
    return IssueOrganizationalUnit.objects.create(
        issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
    )


@pytest.mark.unit
class TestTheDefaults:
    def test_a_new_link_starts_queued(self, link):
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None

    def test_the_queue_reason_is_free_text_only_within_its_choices(self):
        assert QueueReason.NEW_ITEM in QueueReason.values
        assert set(RoutingState.values) == {"queued", "assigned", "allocation_failed", "suspended"}


@pytest.mark.unit
class TestTheDatabaseConstraints:
    def test_assigned_without_an_executor_is_rejected(self, link):
        """
        The state the queue view would read as "somebody has this" while nobody
        does. Rejected by the database, not only by the service.
        """
        link.routing_state = RoutingState.ASSIGNED

        with pytest.raises(IntegrityError), transaction.atomic():
            link.save()

    def test_an_executor_in_any_other_state_is_rejected(self, link, plain_user):
        """
        A leftover executor keeps being charged for the item in every load
        count, which changes who the allocator picks next.
        """
        link.routing_state = RoutingState.QUEUED
        link.primary_executor = plain_user

        with pytest.raises(IntegrityError), transaction.atomic():
            link.save()

    def test_allocation_failed_may_not_carry_an_executor(self, link, plain_user):
        link.routing_state = RoutingState.ALLOCATION_FAILED
        link.primary_executor = plain_user

        with pytest.raises(IntegrityError), transaction.atomic():
            link.save()

    def test_assigned_with_an_executor_is_accepted(self, link, plain_user):
        link.routing_state = RoutingState.ASSIGNED
        link.primary_executor = plain_user
        link.queue_reason = ""
        link.queued_at = None

        link.save()

        link.refresh_from_db()
        assert link.primary_executor_id == plain_user.id

    def test_a_queued_link_may_carry_a_reason_and_a_timestamp(self, link):
        link.queue_reason = QueueReason.NO_ELIGIBLE_MEMBER
        link.queued_at = timezone.now()

        link.save()

        link.refresh_from_db()
        assert link.queue_reason == QueueReason.NO_ELIGIBLE_MEMBER
        assert link.queued_at is not None


@pytest.mark.unit
class TestTheDataMigration:
    """
    The 0135 backfill, run against the real registry.

    ``django_test_migrations`` is not a dependency here, so the function is
    called directly with the app registry: at HEAD the historical models it
    asks for have the same fields, and what is being tested is the rule it
    applies, not Django's migration machinery.
    """

    @staticmethod
    def backfill():
        from django.apps import apps as registry

        # Module name starts with a digit, so it cannot be imported by name.
        migration = importlib.import_module("plane.db.migrations.0135_orca_issue_routing_state")
        migration.set_initial_routing_state(registry, None)

    def test_an_item_with_an_assignee_becomes_assigned_to_the_earliest_one(
        self, link, plain_user, second_user, project
    ):
        IssueAssignee.objects.create(
            issue=link.issue, assignee=plain_user, project=project, workspace=project.workspace
        )
        IssueAssignee.objects.create(
            issue=link.issue, assignee=second_user, project=project, workspace=project.workspace
        )

        self.backfill()

        link.refresh_from_db()
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == plain_user.id
        assert link.queue_reason == ""
        assert link.queued_at is None

    def test_an_item_with_nobody_on_it_joins_the_queue(self, link):
        self.backfill()

        link.refresh_from_db()
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.NEW_ITEM
        assert link.queued_at is not None

    def test_running_it_twice_does_not_restart_the_wait(self, link):
        """Idempotent: a re-run must not reset how long an item has waited."""
        self.backfill()
        link.refresh_from_db()
        first_queued_at = link.queued_at

        self.backfill()

        link.refresh_from_db()
        assert link.queued_at == first_queued_at

    def test_a_cleared_link_is_left_alone(self, link):
        """Soft-deleted links are history; the backfill does not rewrite them."""
        link.delete()

        self.backfill()

        cleared = IssueOrganizationalUnit.all_objects.get(pk=link.pk)
        assert cleared.routing_state == RoutingState.QUEUED
        assert cleared.queued_at is None
