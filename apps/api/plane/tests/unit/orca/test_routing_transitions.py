# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The state machine of RFC §6.2, walked one row at a time.

The service tests check each operation on the state it usually runs in. This
file checks the table itself: every transition the RFC allows happens, and
every one it leaves out is refused rather than quietly performed. A missing
edge that silently works is how an item ends up assigned to somebody nobody
chose, and no single-operation test would catch it.

Refusals carry their own HTTP status: 409 when somebody got there first (the
caller has to re-read), 400 when the move makes no sense from this state.
"""

import pytest

from plane.app.services.orca import (
    AlreadyClaimed,
    InvalidTransition,
    allocate,
    claim,
    reassign,
    return_to_queue,
)
from plane.db.models import (
    AssignmentMode,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    QueueReason,
    RoutingState,
)

from .conftest import ROLE_MEMBER


@pytest.fixture
def covered(unit, project, link_project):
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def staffed(covered, unit, project, add_member, grant_manual_access, plain_user, second_user):
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    return plain_user, second_user


@pytest.fixture
def linked(unit, project, make_issue):
    """A work item the area owns, in the state a fresh link starts in."""

    def _make():
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )
        return issue

    return _make


def policy(unit, workspace, mode, allowed=None):
    return OrganizationalUnitAssignmentPolicy.objects.create(
        organizational_unit=unit,
        workspace=workspace,
        default_mode=mode,
        allowed_modes=allowed or [mode.value],
    )


def state_of(issue):
    return IssueOrganizationalUnit.objects.get(issue=issue)


def put_in(issue, routing_state, *, queue_reason=""):
    """Force a link into a state the service has no operation for yet."""
    link = state_of(issue)
    link.routing_state = routing_state
    link.queue_reason = queue_reason
    link.primary_executor = None
    link.save()
    return link


@pytest.mark.unit
class TestTheTransitionsTheTableAllows:
    def test_nothing_to_queued_when_the_policy_is_manual(self, unit, project, staffed, linked, workspace_with_members):
        policy(unit, workspace_with_members, AssignmentMode.MANUAL)
        issue = linked()

        allocate(issue, unit)

        link = state_of(issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.AWAITING_COORDINATOR

    def test_nothing_to_assigned_when_the_ranking_finds_somebody(
        self, unit, project, staffed, linked, workspace_with_members
    ):
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED)
        issue = linked()

        allocate(issue, unit)

        assert state_of(issue).routing_state == RoutingState.ASSIGNED

    def test_nothing_to_allocation_failed_when_it_does_not(
        self, unit, project, covered, linked, workspace_with_members
    ):
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED)
        issue = linked()

        allocate(issue, unit)

        link = state_of(issue)
        assert link.routing_state == RoutingState.ALLOCATION_FAILED
        assert link.queue_reason == QueueReason.NO_ELIGIBLE_MEMBER

    def test_queued_to_assigned_by_a_claim(self, unit, project, staffed, linked, workspace_with_members):
        policy(unit, workspace_with_members, AssignmentMode.SELF_CLAIM)
        issue = linked()
        allocate(issue, unit)
        taker, _ = staffed

        claim(issue, taker)

        assert state_of(issue).primary_executor_id == taker.id

    def test_queued_to_assigned_by_a_coordinator(self, unit, project, staffed, linked, workspace_with_members):
        """The other half of that row: somebody is given the item rather than
        taking it, and the policy has nothing to say about it."""
        policy(unit, workspace_with_members, AssignmentMode.MANUAL)
        issue = linked()
        allocate(issue, unit)
        _, chosen = staffed

        reassign(issue, chosen)

        link = state_of(issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == chosen.id

    def test_allocation_failed_to_assigned_by_a_claim(self, unit, project, staffed, linked, workspace_with_members):
        """A failed allocation is not a dead end: the item is still in the
        queue, and somebody who becomes eligible can take it."""
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED, allowed=["least_loaded", "self_claim"])
        issue = linked()
        put_in(issue, RoutingState.ALLOCATION_FAILED, queue_reason=QueueReason.NO_ELIGIBLE_MEMBER)
        taker, _ = staffed

        claim(issue, taker)

        assert state_of(issue).routing_state == RoutingState.ASSIGNED

    def test_allocation_failed_to_assigned_by_running_it_again(
        self, unit, project, staffed, linked, workspace_with_members
    ):
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED)
        issue = linked()
        put_in(issue, RoutingState.ALLOCATION_FAILED, queue_reason=QueueReason.NO_ELIGIBLE_MEMBER)

        allocate(issue, unit)

        assert state_of(issue).routing_state == RoutingState.ASSIGNED

    def test_assigned_to_queued_by_returning_it(self, unit, project, staffed, linked, workspace_with_members):
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED)
        issue = linked()
        allocate(issue, unit)

        return_to_queue(issue)

        link = state_of(issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None
        assert link.queue_reason == QueueReason.MANUALLY_RETURNED

    def test_assigned_to_assigned_by_reassigning(self, unit, project, staffed, linked, workspace_with_members):
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED)
        issue = linked()
        allocate(issue, unit)
        first, second = staffed
        chosen = state_of(issue).primary_executor_id
        other = second if chosen == first.id else first

        reassign(issue, other)

        link = state_of(issue)
        assert link.primary_executor_id == other.id
        assert link.current_assignment_decision.supersedes_id is not None

    def test_suspended_to_queued_by_resuming(self, unit, project, staffed, linked, workspace_with_members):
        """Suspension itself is Phase 2 — the coordinator UI does not exist —
        but the way back is already the ordinary return, and the state exists
        in the database, so a row that gets there must not be stuck."""
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED)
        issue = linked()
        allocate(issue, unit)
        # The CHECK forbids an executor outside `assigned`, so suspending an
        # item releases the person as well — which is what suspension means.
        put_in(issue, RoutingState.SUSPENDED)

        return_to_queue(issue, queue_reason=QueueReason.MANUALLY_RETURNED)

        assert state_of(issue).routing_state == RoutingState.QUEUED


@pytest.mark.unit
class TestTheTransitionsTheTableLeavesOut:
    def test_claiming_an_assigned_item_is_a_conflict(self, unit, project, staffed, linked, workspace_with_members):
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED, allowed=["least_loaded", "self_claim"])
        issue = linked()
        allocate(issue, unit)
        first, second = staffed
        chosen = state_of(issue).primary_executor_id
        other = second if chosen == first.id else first

        with pytest.raises(AlreadyClaimed) as raised:
            claim(issue, other)

        assert raised.value.http_status == 409

    def test_claiming_a_suspended_item_is_refused(self, unit, project, staffed, linked, workspace_with_members):
        policy(unit, workspace_with_members, AssignmentMode.SELF_CLAIM)
        issue = linked()
        allocate(issue, unit)
        put_in(issue, RoutingState.SUSPENDED)
        taker, _ = staffed

        with pytest.raises(InvalidTransition) as raised:
            claim(issue, taker)

        assert raised.value.http_status == 400

    def test_returning_a_queued_item_is_refused(self, unit, project, staffed, linked, workspace_with_members):
        policy(unit, workspace_with_members, AssignmentMode.MANUAL)
        issue = linked()
        allocate(issue, unit)

        with pytest.raises(InvalidTransition):
            return_to_queue(issue)

    def test_returning_a_failed_allocation_is_refused(self, unit, project, covered, linked, workspace_with_members):
        """It is already in the queue. Returning it again would restart the
        wait and hide how long it has actually been sitting there."""
        policy(unit, workspace_with_members, AssignmentMode.LEAST_LOADED)
        issue = linked()
        allocate(issue, unit)

        with pytest.raises(InvalidTransition):
            return_to_queue(issue)

    def test_an_item_with_no_area_has_no_transitions_at_all(self, unit, project, staffed, make_issue):
        issue = make_issue(project)

        with pytest.raises(InvalidTransition):
            claim(issue, staffed[0])
