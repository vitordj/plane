# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Absences, per-area limits, and what the ranking does about them (items 3.1, 3.2).

Two things are being tested and they are easy to conflate. The helpers answer
"is this person here?" and "will this area put more on them?", which are
different questions about different rows. The ranking then has to apply both
*and* keep saying why it skipped somebody, because a ranking that silently
drops people is one a coordinator cannot argue with.

The switch is tested as hard as the feature: with `ORCA_AVAILABILITY_ENABLED`
off, every answer has to be the one Phase 2 gave. A switch whose off position
changes behaviour is a switch nobody flips during an incident.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from plane.app.services.orca import (
    accepts_new_work,
    allocate,
    is_available,
    rank_candidates,
    resolve_policy,
    unavailable_member_ids,
    unavailable_window,
)
from plane.db.models import (
    AssignmentMode,
    IssueOrganizationalUnit,
    MembershipAllocationSettings,
    OrganizationalUnitAssignmentPolicy,
    UnavailabilityReason,
    WorkspaceMemberAvailability,
)

from .conftest import ROLE_MEMBER


@pytest.fixture
def availability_on(settings):
    """The third switch on; it ships off (RFC §9)."""
    settings.ORCA_ORG_UNITS_ENABLED = True
    settings.ORCA_AVAILABILITY_ENABLED = True
    return settings


@pytest.fixture
def away(workspace_member_of):
    """Record an absence for somebody."""

    def _away(user, *, starts_hours_ago=1, ends_in_hours=None, reason=UnavailabilityReason.VACATION):
        now = timezone.now()
        return WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(user),
            unavailable_from=now - timedelta(hours=starts_hours_ago),
            unavailable_until=None if ends_in_hours is None else now + timedelta(hours=ends_in_hours),
            reason=reason,
        )

    return _away


@pytest.fixture
def staffed(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user):
    """Two members of the area who can hold work in the project."""
    link_project(unit, project, ROLE_MEMBER)
    memberships = {}
    for user in (plain_user, second_user):
        memberships[user] = add_member(unit, user)
        grant_manual_access(project, user)
    return memberships


@pytest.mark.unit
@pytest.mark.django_db
class TestTheWindow:
    def test_an_open_window_covers_now(self, availability_on, away, plain_user, workspace_member_of):
        away(plain_user)

        assert is_available(workspace_member_of(plain_user)) is False

    def test_a_closed_window_that_has_not_started_does_not(self, availability_on, workspace_member_of, plain_user):
        now = timezone.now()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user),
            unavailable_from=now + timedelta(days=2),
            unavailable_until=now + timedelta(days=9),
        )

        assert is_available(workspace_member_of(plain_user)) is True

    def test_a_window_that_has_ended_does_not(self, availability_on, workspace_member_of, plain_user):
        now = timezone.now()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user),
            unavailable_from=now - timedelta(days=9),
            unavailable_until=now - timedelta(days=2),
        )

        assert is_available(workspace_member_of(plain_user)) is True

    def test_an_indefinite_window_never_ends_by_itself(self, availability_on, away, workspace_member_of, plain_user):
        away(plain_user, ends_in_hours=None)

        assert is_available(workspace_member_of(plain_user), timezone.now() + timedelta(days=365)) is False

    def test_the_boundary_belongs_to_the_absence_at_its_start_and_not_at_its_end(
        self, availability_on, workspace_member_of, plain_user
    ):
        start = timezone.now()
        end = start + timedelta(days=7)
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=start, unavailable_until=end
        )
        member = workspace_member_of(plain_user)

        # Half-open on purpose: two back-to-back absences must not both cover
        # the instant where one ends and the next begins.
        assert is_available(member, start) is False
        assert is_available(member, end) is True

    def test_two_overlapping_windows_are_allowed_and_the_earlier_one_explains_it(
        self, availability_on, workspace_member_of, plain_user
    ):
        now = timezone.now()
        member = workspace_member_of(plain_user)
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            unavailable_from=now - timedelta(days=2),
            unavailable_until=now + timedelta(days=2),
            reason=UnavailabilityReason.VACATION,
        )
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            unavailable_from=now - timedelta(hours=1),
            unavailable_until=now + timedelta(days=9),
            reason=UnavailabilityReason.LEAVE,
        )

        assert unavailable_window(member.id).reason == UnavailabilityReason.VACATION

    def test_a_window_that_ends_before_it_starts_is_refused_by_the_database(
        self, availability_on, workspace_member_of, plain_user
    ):
        now = timezone.now()
        with pytest.raises(IntegrityError), transaction.atomic():
            WorkspaceMemberAvailability.objects.create(
                workspace_member=workspace_member_of(plain_user),
                unavailable_from=now,
                unavailable_until=now - timedelta(hours=1),
            )

    def test_the_workspace_is_taken_from_the_member(self, availability_on, workspace_member_of, plain_user):
        window = WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now()
        )

        assert window.workspace_id == workspace_member_of(plain_user).workspace_id

    def test_a_roster_is_answered_in_one_read(
        self, availability_on, away, workspace_member_of, plain_user, second_user
    ):
        away(plain_user)
        ids = [workspace_member_of(plain_user).id, workspace_member_of(second_user).id]

        assert unavailable_member_ids(ids) == {workspace_member_of(plain_user).id}

    def test_the_switch_off_says_everybody_is_here(self, settings, away, workspace_member_of, plain_user):
        away(plain_user)
        settings.ORCA_AVAILABILITY_ENABLED = False

        assert is_available(workspace_member_of(plain_user)) is True
        assert unavailable_window(workspace_member_of(plain_user).id) is None
        assert unavailable_member_ids([workspace_member_of(plain_user).id]) == set()


@pytest.mark.unit
@pytest.mark.django_db
class TestWhatOneAreaMayPutOnSomebody:
    def test_the_default_is_accepting_with_no_limit_of_their_own(self, availability_on, staffed, plain_user):
        assert accepts_new_work(staffed[plain_user]) is True

    def test_opting_out_is_per_membership(self, availability_on, staffed, unit, second_unit, add_member, plain_user):
        # Somebody in two areas who stops taking work from one is still taking
        # work from the other; a per-person flag would say the wrong thing.
        other = add_member(second_unit, plain_user)
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user], accepts_new_work=False)

        assert accepts_new_work(staffed[plain_user]) is False
        assert accepts_new_work(other) is True

    def test_the_switch_off_says_everybody_accepts(self, settings, staffed, plain_user):
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user], accepts_new_work=False)
        settings.ORCA_AVAILABILITY_ENABLED = False

        assert accepts_new_work(staffed[plain_user]) is True

    def test_one_membership_has_at_most_one_settings_row(self, availability_on, staffed, plain_user):
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user])

        with pytest.raises(IntegrityError), transaction.atomic():
            MembershipAllocationSettings.objects.create(membership=staffed[plain_user])


@pytest.mark.unit
@pytest.mark.django_db
class TestTheRankingUnderLb2:
    def test_somebody_away_leaves_the_ranking_with_a_reason(
        self, availability_on, unit, project, staffed, away, plain_user, second_user
    ):
        away(plain_user)

        ranked = rank_candidates(unit, project.id)

        assert [candidate.user_id for candidate in ranked.eligible] == [second_user.id]
        assert [(candidate.user_id, candidate.excluded_reason) for candidate in ranked.excluded] == [
            (plain_user.id, "unavailable")
        ]

    def test_somebody_who_stopped_accepting_work_leaves_it_too(
        self, availability_on, unit, project, staffed, plain_user, second_user
    ):
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user], accepts_new_work=False)

        ranked = rank_candidates(unit, project.id)

        assert [candidate.user_id for candidate in ranked.eligible] == [second_user.id]
        assert [candidate.excluded_reason for candidate in ranked.excluded] == ["opted_out"]

    def test_a_personal_ceiling_and_the_areas_are_told_apart(
        self, availability_on, unit, project, staffed, make_issue, workspace_with_members, plain_user, second_user
    ):
        # One open item each, a personal ceiling of one on the first person and
        # an area ceiling of two: they are skipped for different reasons, and
        # the snapshot has to say which.
        for user in (plain_user, second_user):
            issue = make_issue(project)
            IssueOrganizationalUnit.objects.create(
                issue=issue,
                organizational_unit=unit,
                project=project,
                workspace=workspace_with_members,
                routing_state="assigned",
                primary_executor=user,
            )
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user], max_open_items=1)
        policy = OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
            max_open_items_per_member=1,
        )

        ranked = rank_candidates(unit, project.id, resolve_policy(unit, project.id))

        assert ranked.eligible == []
        reasons = {candidate.user_id: candidate.excluded_reason for candidate in ranked.excluded}
        assert reasons[plain_user.id] == "member_limit"
        assert reasons[second_user.id] == "policy_limit"
        assert policy.max_open_items_per_member == 1

    def test_the_personal_ceiling_wins_when_it_is_stricter(
        self, availability_on, unit, project, staffed, make_issue, workspace_with_members, plain_user
    ):
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            routing_state="assigned",
            primary_executor=plain_user,
        )
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user], max_open_items=1)

        ranked = rank_candidates(unit, project.id)

        # No policy at all, so only the person's own limit applies.
        assert [candidate.excluded_reason for candidate in ranked.excluded] == ["member_limit"]

    def test_the_allocation_records_the_exclusion_in_its_snapshot(
        self, availability_on, unit, project, staffed, make_issue, away, plain_user, second_user
    ):
        away(plain_user)
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )

        result = allocate(issue, unit, requested_mode=AssignmentMode.LEAST_LOADED)

        assert result.chosen_user_id == second_user.id
        assert result.decision.algorithm_version == "lb-2"
        snapshot = {row["user_id"]: row.get("excluded_reason") for row in result.decision.candidates_snapshot}
        assert snapshot[str(plain_user.id)] == "unavailable"

    def test_work_already_held_by_somebody_away_is_not_taken_from_them_here(
        self, availability_on, unit, project, staffed, make_issue, away, workspace_with_members, plain_user
    ):
        issue = make_issue(project)
        link = IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            routing_state="assigned",
            primary_executor=plain_user,
        )
        away(plain_user)

        rank_candidates(unit, project.id)

        # Returning it is the sweep's job (item 3.4), and it needs a decision;
        # the ranking is a read and must not move anybody's work.
        link.refresh_from_db()
        assert link.primary_executor_id == plain_user.id

    def test_the_switch_off_ranks_exactly_as_phase_two_did(
        self, settings, unit, project, staffed, away, plain_user, second_user
    ):
        away(plain_user)
        MembershipAllocationSettings.objects.create(membership=staffed[second_user], accepts_new_work=False)
        settings.ORCA_AVAILABILITY_ENABLED = False

        ranked = rank_candidates(unit, project.id)

        assert {candidate.user_id for candidate in ranked.eligible} == {plain_user.id, second_user.id}
        assert ranked.excluded == []


@pytest.mark.unit
@pytest.mark.django_db
class TestTheReasonVocabulary:
    """The reasons are a contract: the interface has a sentence for each."""

    def test_a_validation_error_is_raised_for_an_unknown_reason_choice(
        self, availability_on, workspace_member_of, plain_user
    ):
        window = WorkspaceMemberAvailability(
            workspace_member=workspace_member_of(plain_user),
            unavailable_from=timezone.now(),
            reason="sabbatical",
        )

        with pytest.raises(ValidationError):
            window.full_clean()
