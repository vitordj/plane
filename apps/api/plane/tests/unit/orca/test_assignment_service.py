# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The service that decides who executes the work an area owns.

Everything that assigns goes through here, so this file is where the rules are
pinned: which policy wins, what the ranking counts, what each mode does with a
work item nobody has taken, and — the part that is easy to lose in a later
refactor — that reassigning never drops the person who was carrying it, and
that nothing here ever writes ProjectMember.
"""

import pytest

from plane.app.services.orca import (
    AssignmentModeNotAllowed,
    DecisionStale,
    ExecutorNotEligible,
    InvalidTransition,
    UnitNotCoveringProject,
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
    IssueAssignee,
    IssueOrganizationalUnit,
    IssueResponsibilityEvent,
    OrganizationalUnitAssignmentPolicy,
    ProjectMember,
)
from plane.db.models.organizational_unit import QueueReason, RoutingState

from .conftest import ROLE_MEMBER


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def unit_project(unit, project, link_project):
    return link_project(unit, project)


@pytest.fixture
def make_policy(workspace_with_members, unit):
    def _make(unit_project=None, default_mode="manual", allowed_modes=None, **kwargs):
        return OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            unit_project=unit_project,
            workspace=workspace_with_members,
            default_mode=default_mode,
            allowed_modes=allowed_modes if allowed_modes is not None else [default_mode],
            **kwargs,
        )

    return _make


@pytest.fixture
def member_of(covered_unit, project, workspace_with_members, add_member):
    """Somebody the area can actually hand work to: in the area and in the project."""

    def _make(user, role=ROLE_MEMBER):
        add_member(covered_unit, user)
        ProjectMember.objects.get_or_create(
            project=project,
            member=user,
            defaults={"workspace": workspace_with_members, "role": role, "is_active": True},
        )
        return user

    return _make


def link_of(issue):
    return IssueOrganizationalUnit.objects.get(issue=issue)


def assignee_ids(issue):
    return set(IssueAssignee.objects.filter(issue=issue, deleted_at__isnull=True).values_list("assignee_id", flat=True))


# --- policy resolution -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
class TestPolicyResolution:
    def test_with_no_policy_the_area_assigns_manually(self, covered_unit, project):
        resolution = resolve_policy(covered_unit, project.id)

        assert resolution.effective_mode == "manual"
        assert resolution.policy_source == "fallback"

    def test_the_area_policy_applies_when_there_is_no_project_one(self, covered_unit, project, make_policy):
        make_policy(default_mode="self_claim")

        resolution = resolve_policy(covered_unit, project.id)

        assert resolution.effective_mode == "self_claim"
        assert resolution.policy_source == "unit"

    def test_the_project_policy_wins_over_the_area_policy(self, covered_unit, project, make_policy, unit_project):
        make_policy(default_mode="manual")
        make_policy(unit_project=unit_project, default_mode="least_loaded", allowed_modes=["least_loaded"])

        resolution = resolve_policy(covered_unit, project.id)

        assert resolution.effective_mode == "least_loaded"
        assert resolution.policy_source == "unit_project"

    def test_a_requested_mode_inside_the_allowed_list_is_used(self, covered_unit, project, make_policy):
        make_policy(default_mode="manual", allowed_modes=["manual", "least_loaded"])

        resolution = resolve_policy(covered_unit, project.id, "least_loaded")

        assert resolution.effective_mode == "least_loaded"
        assert resolution.policy_source == "request"

    def test_i7_a_disallowed_mode_is_refused_not_downgraded(self, covered_unit, project, make_policy):
        """
        The silent-downgrade trap: an automation that asked for automatic
        allocation and got a manual queue would look like it worked.
        """
        make_policy(default_mode="manual", allowed_modes=["manual"])

        with pytest.raises(AssignmentModeNotAllowed):
            resolve_policy(covered_unit, project.id, "least_loaded")


# --- ranking -----------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
class TestRanking:
    def test_somebody_outside_the_project_is_not_a_candidate(self, covered_unit, project, add_member, plain_user):
        add_member(covered_unit, plain_user)

        assert rank_candidates(covered_unit, project.id).elected == []

    def test_the_least_loaded_person_comes_first(
        self, covered_unit, project, member_of, plain_user, second_user, make_issue
    ):
        busy = member_of(plain_user)
        idle = member_of(second_user)
        loaded = make_issue(project, name="already carried")
        set_responsibility(loaded, covered_unit, explicit_executor=busy, trigger="internal_api")

        ranked = rank_candidates(covered_unit, project.id)

        assert ranked.elected[0]["user_id"] == str(idle.id)

    def test_ties_break_deterministically(self, covered_unit, project, member_of, plain_user, second_user):
        member_of(plain_user)
        member_of(second_user)

        first = [row["user_id"] for row in rank_candidates(covered_unit, project.id).elected]
        second = [row["user_id"] for row in rank_candidates(covered_unit, project.id).elected]

        assert first == second
        assert first == sorted(first)

    def test_the_policy_limit_excludes_with_a_reason(
        self, covered_unit, project, member_of, plain_user, make_policy, make_issue
    ):
        person = member_of(plain_user)
        make_policy(default_mode="least_loaded", allowed_modes=["least_loaded"], max_open_items_per_member=1)
        carried = make_issue(project, name="at the limit")
        set_responsibility(carried, covered_unit, explicit_executor=person, trigger="internal_api")
        policy = OrganizationalUnitAssignmentPolicy.objects.get(organizational_unit=covered_unit)

        ranked = rank_candidates(covered_unit, project.id, policy)

        assert ranked.elected == []
        assert ranked.excluded[0]["excluded_reason"] == "policy_limit"


# --- allocation --------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
class TestAllocation:
    def test_manual_leaves_the_work_in_the_queue(self, covered_unit, project, make_issue, member_of, plain_user):
        member_of(plain_user)
        issue = make_issue(project)

        result = set_responsibility(issue, covered_unit, trigger="internal_api")

        assert result.outcome == "queued"
        link = link_of(issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.AWAITING_COORDINATOR
        assert link.primary_executor_id is None
        assert assignee_ids(issue) == set()

    def test_least_loaded_hands_the_work_to_somebody(
        self, covered_unit, project, make_issue, member_of, plain_user, make_policy
    ):
        person = member_of(plain_user)
        make_policy(default_mode="least_loaded", allowed_modes=["least_loaded"])
        issue = make_issue(project)

        result = set_responsibility(issue, covered_unit, trigger="internal_api")

        assert result.executor_id == person.id
        link = link_of(issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == person.id
        # The native assignee is what makes it visible in Plane itself.
        assert assignee_ids(issue) == {person.id}

    def test_least_loaded_with_nobody_eligible_fails_visibly(self, covered_unit, project, make_issue, make_policy):
        """
        Not the same as "waiting for a coordinator": it means the area's
        membership is wrong, and the queue has to say so.
        """
        make_policy(default_mode="least_loaded", allowed_modes=["least_loaded"])
        issue = make_issue(project)

        result = set_responsibility(issue, covered_unit, trigger="internal_api")

        assert result.outcome == "allocation_failed"
        link = link_of(issue)
        assert link.routing_state == RoutingState.ALLOCATION_FAILED
        assert link.queue_reason == QueueReason.NO_ELIGIBLE_MEMBER

    def test_i4_an_explicit_executor_outside_the_area_is_refused(
        self, covered_unit, project, make_issue, outsider_user
    ):
        issue = make_issue(project)

        with pytest.raises(ExecutorNotEligible):
            set_responsibility(issue, covered_unit, explicit_executor=outsider_user, trigger="public_api")

    def test_i2_an_area_that_does_not_cover_the_project_is_refused(self, unit, project, make_issue):
        issue = make_issue(project)

        with pytest.raises(UnitNotCoveringProject):
            set_responsibility(issue, unit, trigger="internal_api")

    def test_collaborators_are_added_without_taking_responsibility(
        self, covered_unit, project, make_issue, member_of, plain_user, second_user
    ):
        executor = member_of(plain_user)
        helper = member_of(second_user)
        issue = make_issue(project)

        set_responsibility(
            issue, covered_unit, explicit_executor=executor, collaborators=[helper], trigger="public_api"
        )

        assert assignee_ids(issue) == {executor.id, helper.id}
        assert link_of(issue).primary_executor_id == executor.id


# --- decisions ---------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
class TestDecisions:
    def test_i5_every_allocation_writes_one_decision(self, covered_unit, project, make_issue, member_of, plain_user):
        person = member_of(plain_user)
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, explicit_executor=person, trigger="internal_api")

        decisions = AssignmentDecision.objects.filter(issue=issue)
        assert decisions.count() == 1
        assert link_of(issue).current_assignment_decision_id == decisions.first().id

    def test_the_snapshot_records_who_was_considered(
        self, covered_unit, project, make_issue, member_of, plain_user, second_user, make_policy
    ):
        member_of(plain_user)
        member_of(second_user)
        make_policy(default_mode="least_loaded", allowed_modes=["least_loaded"])
        issue = make_issue(project)

        result = set_responsibility(issue, covered_unit, trigger="internal_api")

        snapshot = result.decision.candidates_snapshot
        assert {row["user_id"] for row in snapshot} == {str(plain_user.id), str(second_user.id)}

    def test_reassignment_supersedes_and_keeps_the_previous_person(
        self, covered_unit, project, make_issue, member_of, plain_user, second_user, admin_user
    ):
        first_person = member_of(plain_user)
        second_person = member_of(second_user)
        issue = make_issue(project)
        first = set_responsibility(issue, covered_unit, explicit_executor=first_person, trigger="internal_api")

        result = reassign(issue, second_person, actor=admin_user, reason="rebalancing")

        assert result.executor_id == second_person.id
        assert result.decision.supersedes_id == first.decision.id
        assert result.decision.previous_primary_executor_id == first_person.id
        # The previous executor stays on the work item as a collaborator.
        assert assignee_ids(issue) == {first_person.id, second_person.id}

    def test_reassignment_from_a_stale_view_is_refused(
        self, covered_unit, project, make_issue, member_of, plain_user, second_user, admin_user
    ):
        first_person = member_of(plain_user)
        second_person = member_of(second_user)
        issue = make_issue(project)
        first = set_responsibility(issue, covered_unit, explicit_executor=first_person, trigger="internal_api")
        reassign(issue, second_person, actor=admin_user)

        with pytest.raises(DecisionStale):
            reassign(issue, first_person, actor=admin_user, expected_decision_id=first.decision.id)


# --- claim and return --------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
class TestClaimAndReturn:
    def test_an_eligible_member_can_take_queued_work(
        self, covered_unit, project, make_issue, member_of, plain_user, make_policy
    ):
        person = member_of(plain_user)
        make_policy(default_mode="self_claim", allowed_modes=["self_claim"])
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")

        result = claim(issue, person)

        assert result.executor_id == person.id
        assert link_of(issue).routing_state == RoutingState.ASSIGNED

    def test_claiming_work_somebody_already_has_is_refused(
        self, covered_unit, project, make_issue, member_of, plain_user, second_user, make_policy
    ):
        holder = member_of(plain_user)
        latecomer = member_of(second_user)
        make_policy(default_mode="self_claim", allowed_modes=["self_claim"])
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=holder, trigger="internal_api")

        with pytest.raises(Exception) as exc:
            claim(issue, latecomer)

        assert exc.typename == "AlreadyClaimed"

    def test_a_suspended_work_item_cannot_be_claimed(
        self, covered_unit, project, make_issue, member_of, plain_user, make_policy
    ):
        person = member_of(plain_user)
        make_policy(default_mode="self_claim", allowed_modes=["self_claim"])
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")
        link = link_of(issue)
        link.routing_state = RoutingState.SUSPENDED
        link.save(update_fields=["routing_state"])

        with pytest.raises(InvalidTransition):
            claim(issue, person)

    def test_returning_work_keeps_the_native_assignee(
        self, covered_unit, project, make_issue, member_of, plain_user, admin_user
    ):
        person = member_of(plain_user)
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=person, trigger="internal_api")

        return_to_queue(issue, actor=admin_user, reason="handing it back")

        link = link_of(issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None
        # Still on the work item: they keep seeing it until somebody takes over.
        assert assignee_ids(issue) == {person.id}


# --- transfer ----------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
class TestTransfer:
    def test_i6_a_transfer_records_where_it_came_from(
        self, covered_unit, second_unit, project, link_project, make_issue, admin_user
    ):
        link_project(second_unit, project)
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")

        transfer_unit(issue, second_unit, actor=admin_user, reason="wrong area")

        event = IssueResponsibilityEvent.objects.filter(issue=issue).order_by("-created_at").first()
        assert event.from_unit_id == covered_unit.id
        assert event.to_unit_id == second_unit.id
        assert link_of(issue).organizational_unit_id == second_unit.id

    def test_a_destination_that_does_not_cover_the_project_is_refused(
        self, covered_unit, second_unit, project, make_issue, admin_user
    ):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")

        with pytest.raises(UnitNotCoveringProject):
            transfer_unit(issue, second_unit, actor=admin_user)

    def test_taking_responsibility_the_first_time_records_an_event(self, covered_unit, project, make_issue):
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, trigger="internal_api")

        event = IssueResponsibilityEvent.objects.get(issue=issue)
        assert event.from_unit_id is None
        assert event.to_unit_id == covered_unit.id


# --- invariant I10 -----------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_i10_assignment_never_writes_project_membership(
    covered_unit, project, make_issue, member_of, plain_user, second_user, admin_user
):
    """
    Project access comes from the reconcilers. If assigning could grant it,
    being handed a task would be a way into a project.
    """
    first_person = member_of(plain_user)
    second_person = member_of(second_user)
    issue = make_issue(project)
    before = set(ProjectMember.objects.values_list("id", "member_id", "project_id", "role", "is_active"))

    set_responsibility(issue, covered_unit, explicit_executor=first_person, trigger="internal_api")
    reassign(issue, second_person, actor=admin_user)
    return_to_queue(issue, actor=admin_user)

    assert set(ProjectMember.objects.values_list("id", "member_id", "project_id", "role", "is_active")) == before
