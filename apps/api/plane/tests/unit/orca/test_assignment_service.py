# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The assignment service: policy resolution, the lb-2 ranking, and the states.

Every rule here is one an allocation is wrong without, and every one of them
was previously either absent or restated at a call site:

* a mode the policy forbids is refused, never quietly degraded (I7) — a caller
  that asked for ``least_loaded`` and got ``manual`` would believe the item was
  assigned while it sits in a queue;
* eligibility is checked at decision time (I4), because area membership and
  project membership both change between requests;
* every change of executor or state leaves a decision (I5) with the numbers it
  was made on, so "why does this person have this?" is answerable a week later;
* an existing assignee is never removed.

Concurrency has its own file: it needs real transactions and threads.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.app.services.orca import (
    AlreadyClaimed,
    AssignmentModeNotAllowed,
    DecisionStale,
    ExecutorNotEligible,
    InvalidTransition,
    UnitNotCoveringProject,
    allocate,
    claim,
    rank_candidates,
    reassign,
    resolve_policy,
    return_to_queue,
    set_responsibility,
    transfer_unit,
)
from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    AutomationOperation,
    AutomationOperationStatus,
    AutomationOperationType,
    AvailabilityReason,
    AvailabilitySource,
    DecisionOutcome,
    DecisionTrigger,
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    MembershipAllocationSettings,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitMembership,
    PolicySource,
    ProjectMember,
    QueueReason,
    ResponsibilitySource,
    RoutingState,
    StateGroup,
    WorkspaceMemberAvailability,
)

from .conftest import ROLE_GUEST, ROLE_MEMBER


@pytest.fixture
def covered(unit, project, link_project):
    """The area covers the project — the precondition for owning work in it."""
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def staffed(covered, unit, project, add_member, grant_manual_access, plain_user, second_user):
    """Two people who are in the area and can hold work in the project."""
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    return plain_user, second_user


@pytest.fixture
def make_link(unit, project, make_issue):
    def _make(issue=None):
        issue = issue or make_issue(project)
        return IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )

    return _make


def policy_for(unit, workspace, **kwargs):
    return OrganizationalUnitAssignmentPolicy.objects.create(organizational_unit=unit, workspace=workspace, **kwargs)


def covering_window(workspace_member, **kwargs):
    """A leave window that covers now — the case ranking has to notice."""
    now = timezone.now()
    defaults = {
        "workspace_member": workspace_member,
        "workspace": workspace_member.workspace,
        "unavailable_from": now - timedelta(hours=1),
        "unavailable_until": now + timedelta(hours=1),
        "reason": AvailabilityReason.VACATION,
        "source": AvailabilitySource.MANUAL,
    }
    defaults.update(kwargs)
    return WorkspaceMemberAvailability.objects.create(**defaults)


def membership_of(unit, user):
    return OrganizationalUnitMembership.objects.get(
        organizational_unit=unit, workspace_member__member=user, is_active=True
    )


@pytest.mark.unit
class TestPolicyResolution:
    def test_with_no_policy_at_all_the_answer_is_manual(self, unit, project, covered):
        resolution = resolve_policy(unit, project.id)

        assert resolution.effective_mode == AssignmentMode.MANUAL
        assert resolution.policy_source == PolicySource.FALLBACK
        assert resolution.policy is None

    def test_the_area_policy_governs_when_there_is_no_project_one(self, unit, project, covered, workspace_with_members):
        policy_for(unit, workspace_with_members, default_mode=AssignmentMode.LEAST_LOADED)

        resolution = resolve_policy(unit, project.id)

        assert resolution.effective_mode == AssignmentMode.LEAST_LOADED
        assert resolution.policy_source == PolicySource.UNIT

    def test_the_project_policy_wins_over_the_area_one(self, unit, project, covered, workspace_with_members):
        policy_for(unit, workspace_with_members, default_mode=AssignmentMode.LEAST_LOADED)
        policy_for(unit, workspace_with_members, unit_project=covered, default_mode=AssignmentMode.SELF_CLAIM)

        resolution = resolve_policy(unit, project.id)

        assert resolution.effective_mode == AssignmentMode.SELF_CLAIM
        assert resolution.policy_source == PolicySource.UNIT_PROJECT

    def test_a_requested_mode_inside_the_allowed_list_wins(self, unit, project, covered, workspace_with_members):
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.MANUAL,
            allowed_modes=[AssignmentMode.MANUAL.value, AssignmentMode.LEAST_LOADED.value],
        )

        resolution = resolve_policy(unit, project.id, AssignmentMode.LEAST_LOADED.value)

        assert resolution.effective_mode == AssignmentMode.LEAST_LOADED
        assert resolution.policy_source == PolicySource.REQUEST

    def test_a_requested_mode_outside_the_allowed_list_is_refused(self, unit, project, covered, workspace_with_members):
        """
        Invariant I7. Degrading to the default would tell the caller the item
        was assigned when it is sitting in a queue nobody is watching.
        """
        policy_for(unit, workspace_with_members, allowed_modes=[AssignmentMode.MANUAL.value])

        with pytest.raises(AssignmentModeNotAllowed):
            resolve_policy(unit, project.id, AssignmentMode.LEAST_LOADED.value)

    def test_with_no_policy_any_mode_may_be_requested(self, unit, project, covered):
        """
        An area that has configured nothing forbids nothing. The absent policy
        still decides the *default* — manual, so an unconfigured area never
        hands work out on its own — but a caller that asks for the ranking gets
        it, which is what the assignment route does. I7 governs areas whose
        `allowed_modes` was actually set.
        """
        resolution = resolve_policy(unit, project.id, AssignmentMode.LEAST_LOADED.value)

        assert resolution.effective_mode == AssignmentMode.LEAST_LOADED
        assert resolution.policy_source == PolicySource.REQUEST
        assert resolution.policy is None

    def test_an_unknown_mode_is_refused_even_with_no_policy(self, unit, project, covered):
        with pytest.raises(AssignmentModeNotAllowed):
            resolve_policy(unit, project.id, "whatever")

    def test_the_sla_falls_back_from_project_to_area(self, unit, project, covered, workspace_with_members):
        """A project policy silent about the SLA inherits the area's, not none."""
        policy_for(unit, workspace_with_members, assignment_sla_seconds=3600)
        policy_for(unit, workspace_with_members, unit_project=covered, default_mode=AssignmentMode.MANUAL)

        assert resolve_policy(unit, project.id).sla_seconds == 3600


@pytest.mark.unit
class TestRanking:
    def test_the_least_loaded_person_comes_first(self, unit, project, staffed, make_link, make_issue):
        busy, idle = staffed
        for index in range(2):
            link = make_link(make_issue(project, name=f"Busy {index}"))
            link.routing_state = RoutingState.ASSIGNED
            link.primary_executor = busy
            link.save()

        ranked = rank_candidates(unit, project.id)

        assert [candidate.user_id for candidate in ranked.eligible] == [idle.id, busy.id]
        assert ranked.eligible[0].total_open == 0
        assert ranked.eligible[1].total_open == 2

    def test_finished_work_stops_counting(self, unit, project, staffed, make_link, make_issue):
        busy, idle = staffed
        done = make_issue(project, name="Done", state_group=StateGroup.COMPLETED.value)
        link = make_link(done)
        link.routing_state = RoutingState.ASSIGNED
        link.primary_executor = busy
        link.save()

        ranked = rank_candidates(unit, project.id)

        assert {candidate.user_id: candidate.total_open for candidate in ranked.eligible} == {busy.id: 0, idle.id: 0}

    def test_only_the_primary_executor_is_charged(self, unit, project, staffed, make_link, make_issue):
        """
        A collaborator left on an item from an earlier assignment is not the
        person answerable for it. Counting them, as the old engine did, kept
        pushing them down the ranking for work they no longer owned.
        """
        busy, collaborator = staffed
        issue = make_issue(project, name="Shared")
        link = make_link(issue)
        link.routing_state = RoutingState.ASSIGNED
        link.primary_executor = busy
        link.save()
        IssueAssignee.objects.create(issue=issue, assignee=collaborator, project=project, workspace=project.workspace)

        ranked = rank_candidates(unit, project.id)

        assert ranked.eligible[0].user_id == collaborator.id
        assert ranked.eligible[0].total_open == 0

    def test_someone_outside_the_project_is_excluded_with_a_reason(
        self, unit, project, covered, add_member, plain_user
    ):
        add_member(unit, plain_user)

        ranked = rank_candidates(unit, project.id)

        assert ranked.eligible == []
        assert [candidate.excluded_reason for candidate in ranked.excluded] == ["not_a_project_member"]

    def test_a_guest_cannot_hold_work(self, unit, project, covered, add_member, grant_manual_access, plain_user):
        add_member(unit, plain_user)
        grant_manual_access(project, plain_user, ROLE_GUEST)

        ranked = rank_candidates(unit, project.id)

        assert [candidate.excluded_reason for candidate in ranked.excluded] == ["project_role_too_low"]

    def test_the_load_cap_excludes_rather_than_reorders(
        self, unit, project, staffed, make_link, make_issue, workspace_with_members, covered
    ):
        busy, idle = staffed
        link = make_link(make_issue(project, name="One"))
        link.routing_state = RoutingState.ASSIGNED
        link.primary_executor = busy
        link.save()
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
            max_open_items_per_member=1,
        )

        ranked = rank_candidates(unit, project.id, resolve_policy(unit, project.id))

        assert [candidate.user_id for candidate in ranked.eligible] == [idle.id]
        assert [candidate.excluded_reason for candidate in ranked.excluded] == ["policy_limit"]

    def test_the_order_is_deterministic_on_a_tie(self, unit, project, staffed):
        first = rank_candidates(unit, project.id)
        second = rank_candidates(unit, project.id)

        assert [c.user_id for c in first.eligible] == [c.user_id for c in second.eligible]

    def test_excluded_people_are_named_in_the_snapshot(self, unit, project, covered, add_member, plain_user):
        add_member(unit, plain_user)

        snapshot = rank_candidates(unit, project.id).snapshot()

        assert snapshot == [
            {
                "user_id": str(plain_user.id),
                "total_open": 0,
                "unit_open": 0,
                "last_auto_at": None,
                "excluded_reason": "not_a_project_member",
            }
        ]


@pytest.mark.unit
class TestAvailabilityRanking:
    """Item 3.2: leave, opt-out and caps drop people from ``lb-2``, named on the snapshot."""

    def test_an_unavailable_person_is_excluded_with_their_load(
        self, unit, project, staffed, make_link, make_issue, workspace_member_of, settings
    ):
        """
        Load is per executor. Leaving the ranking does not move their open
        items onto anyone else — the snapshot still shows the numbers they
        were carrying, so "why was this person skipped?" is answerable.
        """
        settings.ORCA_AVAILABILITY_ENABLED = True
        busy, idle = staffed
        for index in range(2):
            link = make_link(make_issue(project, name=f"Busy {index}"))
            link.routing_state = RoutingState.ASSIGNED
            link.primary_executor = busy
            link.save()
        covering_window(workspace_member_of(busy))

        ranked = rank_candidates(unit, project.id)

        assert [candidate.user_id for candidate in ranked.eligible] == [idle.id]
        assert ranked.eligible[0].total_open == 0
        skipped = next(candidate for candidate in ranked.excluded if candidate.user_id == busy.id)
        assert skipped.excluded_reason == "unavailable"
        assert skipped.total_open == 2
        assert any(row.get("excluded_reason") == "unavailable" for row in ranked.snapshot())

    def test_opting_out_excludes_only_that_area(
        self, unit, second_unit, project, staffed, add_member, link_project, settings
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        busy, idle = staffed
        MembershipAllocationSettings.objects.create(membership=membership_of(unit, busy), accepts_new_work=False)
        link_project(second_unit, project)
        add_member(second_unit, busy)
        add_member(second_unit, idle)

        here = rank_candidates(unit, project.id)
        there = rank_candidates(second_unit, project.id)

        assert [candidate.user_id for candidate in here.eligible] == [idle.id]
        assert [candidate.excluded_reason for candidate in here.excluded] == ["opted_out"]
        assert {candidate.user_id for candidate in there.eligible} == {busy.id, idle.id}

    def test_the_personal_cap_excludes_before_the_policy_cap(
        self, unit, project, staffed, make_link, make_issue, workspace_with_members, settings
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        busy, idle = staffed
        link = make_link(make_issue(project, name="One"))
        link.routing_state = RoutingState.ASSIGNED
        link.primary_executor = busy
        link.save()
        MembershipAllocationSettings.objects.create(membership=membership_of(unit, busy), max_open_items=1)
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
            max_open_items_per_member=5,
        )

        ranked = rank_candidates(unit, project.id, resolve_policy(unit, project.id))

        assert [candidate.user_id for candidate in ranked.eligible] == [idle.id]
        assert [candidate.excluded_reason for candidate in ranked.excluded] == ["member_limit"]

    def test_the_policy_cap_is_named_policy_limit(
        self, unit, project, staffed, make_link, make_issue, workspace_with_members, settings
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        busy, idle = staffed
        link = make_link(make_issue(project, name="One"))
        link.routing_state = RoutingState.ASSIGNED
        link.primary_executor = busy
        link.save()
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
            max_open_items_per_member=1,
        )

        ranked = rank_candidates(unit, project.id, resolve_policy(unit, project.id))

        assert [candidate.user_id for candidate in ranked.eligible] == [idle.id]
        assert [candidate.excluded_reason for candidate in ranked.excluded] == ["policy_limit"]

    def test_windows_and_caps_do_not_exclude_while_the_flag_is_off(
        self, unit, project, staffed, make_link, make_issue, workspace_member_of, settings
    ):
        settings.ORCA_AVAILABILITY_ENABLED = False
        busy, idle = staffed
        link = make_link(make_issue(project, name="One"))
        link.routing_state = RoutingState.ASSIGNED
        link.primary_executor = busy
        link.save()
        covering_window(workspace_member_of(busy))
        MembershipAllocationSettings.objects.create(
            membership=membership_of(unit, busy), accepts_new_work=False, max_open_items=1
        )

        ranked = rank_candidates(unit, project.id)

        assert {candidate.user_id for candidate in ranked.eligible} == {busy.id, idle.id}
        assert ranked.excluded == []

    def test_least_loaded_skips_someone_on_leave_and_stamps_lb2(
        self, unit, project, staffed, make_link, make_issue, workspace_with_members, workspace_member_of, settings
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        busy, idle = staffed
        covering_window(workspace_member_of(busy))
        issue = make_issue(project)
        make_link(issue)

        result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.ASSIGNED
        assert result.chosen_user_id == idle.id
        assert result.decision.algorithm_version == "lb-2"
        assert any(row.get("excluded_reason") == "unavailable" for row in result.decision.candidates_snapshot)


@pytest.mark.unit
class TestAllocate:
    def test_manual_leaves_the_item_waiting_for_a_coordinator(self, unit, project, staffed, make_link, make_issue):
        issue = make_issue(project)
        make_link(issue)

        result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.QUEUED
        assert result.link.routing_state == RoutingState.QUEUED
        assert result.link.queue_reason == QueueReason.AWAITING_COORDINATOR
        assert result.link.queued_at is not None
        assert not IssueAssignee.objects.filter(issue=issue).exists()

    def test_self_claim_waits_for_a_claim(self, unit, project, staffed, make_link, make_issue, workspace_with_members):
        policy_for(unit, workspace_with_members, default_mode=AssignmentMode.SELF_CLAIM)
        issue = make_issue(project)
        make_link(issue)

        result = allocate(issue, unit)

        assert result.link.queue_reason == QueueReason.AWAITING_CLAIM

    def test_least_loaded_assigns_and_records_the_numbers(
        self, unit, project, staffed, make_link, make_issue, workspace_with_members
    ):
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        issue = make_issue(project)
        make_link(issue)

        result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.ASSIGNED
        assert result.link.routing_state == RoutingState.ASSIGNED
        assert result.link.primary_executor_id == result.chosen_user_id
        assert IssueAssignee.objects.filter(issue=issue, assignee_id=result.chosen_user_id).exists()
        assert result.decision.candidates_snapshot  # the ranking is on the record
        assert result.decision.algorithm_version == "lb-2"
        assert result.decision.policy_version == 1

    def test_least_loaded_with_nobody_eligible_fails_loudly(
        self, unit, project, covered, make_link, make_issue, workspace_with_members
    ):
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        issue = make_issue(project)
        make_link(issue)

        result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.ALLOCATION_FAILED
        assert result.link.routing_state == RoutingState.ALLOCATION_FAILED
        assert result.link.queue_reason == QueueReason.NO_ELIGIBLE_MEMBER

    def test_an_explicit_executor_skips_the_policy(self, unit, project, staffed, make_link, make_issue):
        chosen, _ = staffed
        issue = make_issue(project)
        make_link(issue)

        result = allocate(issue, unit, explicit_executor=chosen)

        assert result.chosen_user_id == chosen.id
        assert result.decision.effective_mode == AssignmentMode.EXPLICIT
        assert result.decision.policy_source == PolicySource.REQUEST

    def test_an_explicit_executor_outside_the_area_is_refused(
        self, unit, project, covered, make_link, make_issue, grant_manual_access, plain_user
    ):
        grant_manual_access(project, plain_user)
        issue = make_issue(project)
        make_link(issue)

        with pytest.raises(ExecutorNotEligible):
            allocate(issue, unit, explicit_executor=plain_user)

    def test_an_explicit_executor_outside_the_project_is_refused(
        self, unit, project, covered, make_link, make_issue, add_member, plain_user
    ):
        add_member(unit, plain_user)
        issue = make_issue(project)
        make_link(issue)

        with pytest.raises(ExecutorNotEligible):
            allocate(issue, unit, explicit_executor=plain_user)

    def test_an_area_that_does_not_cover_the_project_is_refused(self, unit, project, make_issue):
        issue = make_issue(project)

        with pytest.raises(UnitNotCoveringProject):
            allocate(issue, unit)

    def test_the_sla_deadline_is_written_when_the_item_queues(
        self, unit, project, staffed, make_link, make_issue, workspace_with_members
    ):
        policy_for(unit, workspace_with_members, assignment_sla_seconds=1800)
        issue = make_issue(project)
        make_link(issue)

        result = allocate(issue, unit)

        assert result.link.assignment_due_at is not None

    def test_every_allocation_leaves_a_decision(self, unit, project, staffed, make_link, make_issue):
        issue = make_issue(project)
        make_link(issue)

        allocate(issue, unit)
        allocate(issue, unit)

        assert AssignmentDecision.objects.filter(issue=issue).count() == 2

    def test_the_second_decision_supersedes_the_first(self, unit, project, staffed, make_link, make_issue):
        issue = make_issue(project)
        make_link(issue)

        first = allocate(issue, unit).decision
        second = allocate(issue, unit).decision

        assert second.supersedes_id == first.id


@pytest.mark.unit
class TestClaim:
    @pytest.fixture
    def queued(self, unit, project, staffed, make_link, make_issue, workspace_with_members):
        policy_for(
            unit,
            workspace_with_members,
            default_mode=AssignmentMode.SELF_CLAIM,
            allowed_modes=[AssignmentMode.SELF_CLAIM.value],
        )
        issue = make_issue(project)
        make_link(issue)
        allocate(issue, unit)
        return issue

    def test_a_member_takes_a_queued_item(self, queued, staffed):
        taker, _ = staffed

        result = claim(queued, taker)

        assert result.link.routing_state == RoutingState.ASSIGNED
        assert result.link.primary_executor_id == taker.id
        assert result.decision.trigger == "ui_claim"

    def test_an_item_somebody_already_took_is_refused(self, queued, staffed):
        first, second = staffed
        claim(queued, first)

        with pytest.raises(AlreadyClaimed) as raised:
            claim(queued, second)

        assert raised.value.payload["primary_executor_id"] == str(first.id)

    def test_claiming_is_refused_when_the_policy_does_not_allow_it(
        self, unit, project, staffed, make_link, make_issue, workspace_with_members
    ):
        policy_for(unit, workspace_with_members, allowed_modes=[AssignmentMode.MANUAL.value])
        issue = make_issue(project)
        make_link(issue)
        allocate(issue, unit)
        taker, _ = staffed

        with pytest.raises(AssignmentModeNotAllowed):
            claim(issue, taker)

    def test_someone_outside_the_area_cannot_claim(self, queued, project, grant_manual_access, guest_user):
        grant_manual_access(project, guest_user)

        with pytest.raises(ExecutorNotEligible):
            claim(queued, guest_user)


@pytest.mark.unit
class TestReassignAndReturn:
    @pytest.fixture
    def assigned(self, unit, project, staffed, make_link, make_issue):
        issue = make_issue(project)
        make_link(issue)
        first, _ = staffed
        allocate(issue, unit, explicit_executor=first)
        return issue

    def test_reassignment_moves_the_executor_and_keeps_the_collaborator(self, assigned, staffed):
        previous, incoming = staffed

        result = reassign(assigned, incoming, reason="on holiday")

        assert result.link.primary_executor_id == incoming.id
        assert result.decision.previous_primary_executor_id == previous.id
        # Plane shows assignees to everyone; quietly detaching somebody is a
        # human's call, not the allocator's.
        assert IssueAssignee.objects.filter(issue=assigned, assignee=previous).exists()

    def test_a_stale_reassignment_is_refused(self, assigned, staffed):
        """
        Two coordinators reassigning at once: the second must not silently
        overwrite the first.
        """
        _, incoming = staffed

        with pytest.raises(DecisionStale):
            reassign(assigned, incoming, expected_decision_id="00000000-0000-0000-0000-000000000000")

    def test_a_fresh_reassignment_with_the_right_decision_goes_through(self, assigned, staffed):
        _, incoming = staffed
        link = IssueOrganizationalUnit.objects.get(issue=assigned)

        result = reassign(assigned, incoming, expected_decision_id=link.current_assignment_decision_id)

        assert result.link.primary_executor_id == incoming.id

    def test_returning_to_the_queue_clears_the_executor(self, assigned, staffed):
        previous, _ = staffed

        result = return_to_queue(assigned, reason="wrong area")

        assert result.link.routing_state == RoutingState.QUEUED
        assert result.link.primary_executor_id is None
        assert result.link.queue_reason == QueueReason.MANUALLY_RETURNED
        assert result.decision.previous_primary_executor_id == previous.id
        assert IssueAssignee.objects.filter(issue=assigned, assignee=previous).exists()

    def test_a_queued_item_cannot_be_returned_again(self, unit, project, staffed, make_link, make_issue):
        issue = make_issue(project)
        make_link(issue)
        allocate(issue, unit)

        with pytest.raises(InvalidTransition):
            return_to_queue(issue)


@pytest.mark.unit
class TestResponsibilityAndTransfer:
    def test_marking_an_area_creates_the_link_and_the_event(self, unit, project, staffed, make_issue):
        issue = make_issue(project)

        result = set_responsibility(issue, unit)

        assert result.link.organizational_unit_id == unit.id
        event = IssueResponsibilityEvent.objects.get(issue=issue)
        assert event.from_unit_id is None
        assert event.to_unit_id == unit.id

    def test_marking_an_area_that_does_not_cover_the_project_is_refused(self, unit, project, make_issue):
        issue = make_issue(project)

        with pytest.raises(UnitNotCoveringProject):
            set_responsibility(issue, unit)

    def test_a_transfer_records_both_areas(
        self, unit, second_unit, project, staffed, make_issue, link_project, make_link
    ):
        link_project(second_unit, project, ROLE_MEMBER)
        issue = make_issue(project)
        make_link(issue)

        transfer = transfer_unit(issue, second_unit)

        assert transfer.event.from_unit_id == unit.id
        assert transfer.event.to_unit_id == second_unit.id
        assert IssueOrganizationalUnit.objects.get(issue=issue).organizational_unit_id == second_unit.id

    def test_a_transfer_returns_the_item_when_the_executor_is_not_in_the_new_area(
        self, unit, second_unit, project, staffed, make_issue, link_project, make_link
    ):
        link_project(second_unit, project, ROLE_MEMBER)
        issue = make_issue(project)
        make_link(issue)
        first, _ = staffed
        allocate(issue, unit, explicit_executor=first)

        transfer = transfer_unit(issue, second_unit)

        assert transfer.allocation.link.routing_state == RoutingState.QUEUED
        assert transfer.allocation.link.primary_executor_id is None
        # The person stays visible on the item until a human decides otherwise.
        assert IssueAssignee.objects.filter(issue=issue, assignee=first).exists()

    def test_a_transfer_to_an_area_that_does_not_cover_the_project_is_refused(
        self, unit, second_unit, project, staffed, make_issue, make_link
    ):
        issue = make_issue(project)
        make_link(issue)

        with pytest.raises(UnitNotCoveringProject):
            transfer_unit(issue, second_unit)

    def test_transferring_to_the_same_area_is_refused(self, unit, project, staffed, make_issue, make_link):
        issue = make_issue(project)
        make_link(issue)

        with pytest.raises(InvalidTransition):
            transfer_unit(issue, unit)


@pytest.mark.unit
def test_the_service_never_writes_project_member(unit, project, staffed, make_issue):
    """
    Invariant I10, checked as behaviour rather than by reading the source: an
    allocator that could grant project access would be an allocator that can
    widen who sees a project.
    """
    before = set(ProjectMember.objects.values_list("id", flat=True))
    issue = make_issue(project)

    set_responsibility(issue, unit)

    assert set(ProjectMember.objects.values_list("id", flat=True)) == before


@pytest.fixture
def operation(workspace_with_members):
    """A receipt for a public-API call, to hang decisions off (item 1.4)."""
    return AutomationOperation.objects.create(
        workspace=workspace_with_members,
        idempotency_key="key-for-the-service-tests",
        request_hash="0" * 64,
        operation_type=AutomationOperationType.CREATE_WORK_ITEM,
        status=AutomationOperationStatus.IN_PROGRESS,
    )


@pytest.mark.unit
class TestSpeakingForThePublicApi:
    """
    What item 1.4 needed from this service, and why it is parameters rather
    than a second implementation.

    The public API allocates under exactly the rules the interface does — the
    coverage check, eligibility at decision time, the advisory lock, the
    decision record. What differs is only what the audit trail should say
    afterwards: which caller, and under which call. So the service grew three
    parameters, each defaulting to the value it always wrote, and the endpoints
    pass them. A parallel implementation would have been a second copy of the
    rules, which is the thing D0.5 exists to prevent.
    """

    def test_the_trigger_reaches_the_decision_and_the_event(self, unit, project, staffed, make_issue):
        issue = make_issue(project)

        result = set_responsibility(
            issue, unit, source=ResponsibilitySource.PUBLIC_API, trigger=DecisionTrigger.PUBLIC_API
        )

        assert result.decision.trigger == DecisionTrigger.PUBLIC_API
        event = IssueResponsibilityEvent.objects.get(issue=issue)
        assert event.source == ResponsibilitySource.PUBLIC_API

    def test_existing_callers_still_write_the_internal_trigger(self, unit, project, staffed, make_issue):
        # The defaults are the point: no D0 caller passes a trigger, and none
        # of them may start writing a different one.
        issue = make_issue(project)

        result = set_responsibility(issue, unit)

        assert result.decision.trigger == DecisionTrigger.INTERNAL_API
        assert IssueResponsibilityEvent.objects.get(issue=issue).source == ResponsibilitySource.INTERNAL_API

    def test_the_operation_is_recorded_on_an_allocation(self, unit, project, staffed, make_issue, operation):
        issue = make_issue(project)

        result = set_responsibility(issue, unit, trigger=DecisionTrigger.PUBLIC_API, automation_operation=operation)

        assert result.decision.automation_operation_id == operation.id
        assert operation.assignment_decisions.count() == 1

    def test_the_operation_is_recorded_on_a_reassignment(self, unit, project, staffed, make_issue, operation):
        first, second = staffed
        issue = make_issue(project)
        set_responsibility(issue, unit, explicit_executor=first)

        result = reassign(issue, second, trigger=DecisionTrigger.PUBLIC_API, automation_operation=operation)

        assert result.decision.trigger == DecisionTrigger.PUBLIC_API
        assert result.decision.automation_operation_id == operation.id

    def test_the_operation_is_recorded_on_a_transfer(
        self, unit, second_unit, project, staffed, link_project, add_member, grant_manual_access, make_issue, operation
    ):
        link_project(second_unit, project, ROLE_MEMBER)
        first, _ = staffed
        issue = make_issue(project)
        set_responsibility(issue, unit)

        transfer = transfer_unit(issue, second_unit, trigger=DecisionTrigger.PUBLIC_API, automation_operation=operation)

        assert transfer.allocation is not None
        assert transfer.allocation.decision.automation_operation_id == operation.id

    def test_a_revoked_token_does_not_take_the_decisions_with_it(
        self, unit, project, staffed, make_issue, operation, workspace_with_members
    ):
        # The receipt is SET_NULL on the decision for the same reason it is
        # SET_NULL on the token: withdrawing a credential must not erase the
        # record of what it did.
        issue = make_issue(project)
        result = set_responsibility(issue, unit, automation_operation=operation)

        operation.delete()

        result.decision.refresh_from_db()
        assert result.decision.automation_operation_id is None
        assert AssignmentDecision.objects.filter(pk=result.decision.pk).exists()


@pytest.mark.unit
class TestCollaborators:
    """
    People attached to the item who are not answerable for it (RFC §7.2,
    ``assignment.mode=explicit``). They are ordinary Plane assignees; what
    makes them collaborators is that no decision names them.
    """

    def test_a_collaborator_is_attached_without_becoming_the_executor(
        self, unit, project, staffed, make_issue, second_user, plain_user
    ):
        issue = make_issue(project)

        result = set_responsibility(issue, unit, explicit_executor=plain_user, collaborators=[second_user])

        assert result.chosen_user_id == plain_user.id
        assert result.link.primary_executor_id == plain_user.id
        assert IssueAssignee.objects.filter(issue=issue, assignee=second_user).exists()
        # The decision names the executor and nobody else: "who is answerable"
        # has exactly one answer (I3).
        assert result.decision.chosen_assignee_id == plain_user.id

    def test_a_collaborator_outside_the_area_is_refused(
        self, unit, project, staffed, make_issue, guest_user, plain_user
    ):
        issue = make_issue(project)

        with pytest.raises(ExecutorNotEligible):
            set_responsibility(issue, unit, explicit_executor=plain_user, collaborators=[guest_user])

        # Refused before anything was written: the eligibility check runs
        # inside the same transaction as the allocation.
        assert not IssueAssignee.objects.filter(issue=issue).exists()
        assert not AssignmentDecision.objects.filter(issue=issue).exists()

    def test_collaborators_survive_a_transfer_to_another_area(
        self,
        unit,
        second_unit,
        project,
        staffed,
        link_project,
        add_member,
        grant_manual_access,
        make_issue,
        second_user,
    ):
        link_project(second_unit, project, ROLE_MEMBER)
        add_member(second_unit, second_user)
        first, _ = staffed
        issue = make_issue(project)
        set_responsibility(issue, unit, explicit_executor=first)

        transfer_unit(issue, second_unit, collaborators=[second_user])

        assert IssueAssignee.objects.filter(issue=issue, assignee=second_user).exists()


@pytest.mark.unit
class TestReturningToTheQueueOptimistically:
    """
    ``expected_decision_id`` on ``return_to_queue``: the same If-Match
    ``reassign`` already had, added because the public API offers
    ``{"return_to_queue": true}`` on the same route as a reassignment and both
    halves must be equally safe to retry.
    """

    def test_a_matching_decision_id_is_accepted(self, unit, project, staffed, make_issue, plain_user):
        issue = make_issue(project)
        assigned = set_responsibility(issue, unit, explicit_executor=plain_user)

        result = return_to_queue(issue, expected_decision_id=assigned.decision.id)

        assert result.outcome == DecisionOutcome.QUEUED

    def test_a_stale_decision_id_is_refused(self, unit, project, staffed, make_issue):
        first, second = staffed
        issue = make_issue(project)
        stale = set_responsibility(issue, unit, explicit_executor=first)
        reassign(issue, second)

        with pytest.raises(DecisionStale):
            return_to_queue(issue, expected_decision_id=stale.decision.id)

        # Still assigned: the refusal happened under the row lock, before the
        # state moved.
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
