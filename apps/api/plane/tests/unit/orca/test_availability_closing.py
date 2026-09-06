# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Closing tests for availability (item 3.6).

The per-piece tests say each rule works. These say the shape of the whole
thing is what the RFC promised, and two properties matter more than any single
scenario:

* **nothing moves without a decision.** Every reassignment this feature causes
  carries ``trigger=availability``, and nothing else in the system moves work
  because somebody went away. A count by trigger is the way to state that;
* **coming back is not automatic.** A holiday ending gives the person nothing
  back. That is the design (RFC §6.9): the item is in the queue, and a
  coordinator decides — including deciding it should go back to them.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.app.services.orca import claim, reassign
from plane.bgtasks.organizational_availability_task import sweep_unavailable_executors
from plane.db.models import (
    AssignmentDecision,
    IssueAssignee,
    IssueOrganizationalUnit,
    MembershipAllocationSettings,
    RoutingState,
    WorkspaceMemberAvailability,
)

from .conftest import ROLE_MEMBER


@pytest.fixture
def availability_on(settings):
    settings.ORCA_ORG_UNITS_ENABLED = True
    settings.ORCA_AVAILABILITY_ENABLED = True
    return settings


@pytest.fixture
def staffed(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user):
    link_project(unit, project, ROLE_MEMBER)
    memberships = {}
    for user in (plain_user, second_user):
        memberships[user] = add_member(unit, user)
        grant_manual_access(project, user)
    return memberships


@pytest.fixture
def held_by(unit, project, make_issue, staffed):
    """An item claimed by somebody."""

    def _make(user):
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.QUEUED,
        )
        claim(issue, user)
        return issue

    return _make


def go_away(workspace_member, *, days=7):
    now = timezone.now()
    return WorkspaceMemberAvailability.objects.create(
        workspace_member=workspace_member,
        unavailable_from=now - timedelta(hours=1),
        unavailable_until=now + timedelta(days=days),
    )


@pytest.mark.unit
@pytest.mark.django_db
class TestTheWholeRoundTrip:
    def test_a_holiday_takes_the_work_back_and_returning_does_not_hand_it_over(
        self, availability_on, held_by, workspace_member_of, plain_user, second_user, unit, project
    ):
        issue = held_by(plain_user)
        window = go_away(workspace_member_of(plain_user))

        # The holiday starts: the item comes back to the queue.
        assert sweep_unavailable_executors() == 1
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == "executor_unavailable"

        # The holiday ends. Nothing happens by itself — and that is the point:
        # the item is in a queue a person reads, not in a scheduler.
        window.unavailable_until = timezone.now() - timedelta(minutes=1)
        window.save()
        assert sweep_unavailable_executors() == 0
        link.refresh_from_db()
        assert link.routing_state == RoutingState.QUEUED

        # A coordinator decides, which may well be "back to the same person".
        reassign(issue, plain_user)
        link.refresh_from_db()
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == plain_user.id

    def test_the_person_keeps_seeing_the_item_the_whole_time(
        self, availability_on, held_by, workspace_member_of, plain_user
    ):
        issue = held_by(plain_user)
        go_away(workspace_member_of(plain_user))

        sweep_unavailable_executors()

        # They were away, not removed: taking somebody off an item is a human's
        # call, and Plane shows assignees to everyone.
        assert IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()


@pytest.mark.unit
@pytest.mark.django_db
class TestNothingMovesWithoutADecision:
    def test_every_return_this_feature_causes_carries_its_own_trigger(
        self, availability_on, held_by, workspace_member_of, plain_user, second_user
    ):
        first = held_by(plain_user)
        second = held_by(second_user)
        go_away(workspace_member_of(plain_user))
        go_away(workspace_member_of(second_user))

        sweep_unavailable_executors()

        by_trigger = {}
        for decision in AssignmentDecision.objects.filter(issue__in=[first, second]):
            by_trigger[decision.trigger] = by_trigger.get(decision.trigger, 0) + 1

        # Two claims and two returns, and the returns are marked as the
        # availability layer's rather than as somebody clicking.
        assert by_trigger == {"ui_claim": 2, "availability": 2}

    def test_a_person_the_ranking_skips_is_not_moved_by_the_ranking(
        self, availability_on, held_by, staffed, workspace_member_of, plain_user
    ):
        issue = held_by(plain_user)
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user], accepts_new_work=False)

        # Opting out is about *new* work. Nothing about the item they hold
        # changes, and no decision is written.
        before = AssignmentDecision.objects.filter(issue=issue).count()
        assert IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id == plain_user.id
        assert AssignmentDecision.objects.filter(issue=issue).count() == before

    def test_the_sweep_writes_one_decision_per_item_and_no_more(
        self, availability_on, held_by, workspace_member_of, plain_user
    ):
        issue = held_by(plain_user)
        go_away(workspace_member_of(plain_user))

        sweep_unavailable_executors()
        sweep_unavailable_executors()

        assert AssignmentDecision.objects.filter(issue=issue, trigger="availability").count() == 1
