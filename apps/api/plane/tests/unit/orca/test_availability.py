# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Being away, and taking no new work.

The rule these tests hold down is the one that makes the feature safe to ship
switched off: with ``ORCA_AVAILABILITY_ENABLED=0`` every answer is the
permissive one, so an instance that turns it off gets the ranking it had
before the feature existed rather than a third, untested behaviour.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from plane.app.services.orca.availability import (
    accepts_new_work,
    allocation_settings_for,
    is_available,
    open_item_limit,
    unavailable_member_ids,
)
from plane.db.models import (
    MembershipAllocationSettings,
    OrganizationalUnitAssignmentPolicy,
    WorkspaceMemberAvailability,
)


@pytest.fixture
def availability_on(settings):
    settings.ORCA_AVAILABILITY_ENABLED = True


@pytest.fixture
def make_absence(workspace_with_members, workspace_member_of):
    def _make(user, days_ago=1, days_ahead=1, **kwargs):
        now = timezone.now()
        return WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(user),
            workspace=workspace_with_members,
            unavailable_from=now - timedelta(days=days_ago),
            unavailable_until=None if days_ahead is None else now + timedelta(days=days_ahead),
            **kwargs,
        )

    return _make


@pytest.mark.unit
@pytest.mark.django_db
class TestIsAvailable:
    def test_somebody_with_no_intervals_is_available(self, availability_on, workspace_member_of, plain_user):
        assert is_available(workspace_member_of(plain_user).id) is True

    def test_an_interval_around_now_makes_them_unavailable(
        self, availability_on, make_absence, workspace_member_of, plain_user
    ):
        make_absence(plain_user)

        assert is_available(workspace_member_of(plain_user).id) is False

    def test_an_interval_that_has_ended_does_not(self, availability_on, make_absence, workspace_member_of, plain_user):
        make_absence(plain_user, days_ago=10, days_ahead=-5)

        assert is_available(workspace_member_of(plain_user).id) is True

    def test_an_interval_that_has_not_started_does_not(
        self, availability_on, make_absence, workspace_member_of, plain_user
    ):
        make_absence(plain_user, days_ago=-5, days_ahead=10)

        assert is_available(workspace_member_of(plain_user).id) is True

    def test_an_open_ended_interval_never_stops(self, availability_on, make_absence, workspace_member_of, plain_user):
        """`until` null is "gone, we do not know when they are back"."""
        make_absence(plain_user, days_ahead=None)

        member_id = workspace_member_of(plain_user).id
        assert is_available(member_id) is False
        assert is_available(member_id, at=timezone.now() + timedelta(days=3650)) is False

    def test_two_overlapping_intervals_are_not_a_problem(
        self, availability_on, make_absence, workspace_member_of, plain_user
    ):
        """
        Holiday with a medical leave inside it: two separate facts, and the
        question asked of them is "does any of you cover now?".
        """
        make_absence(plain_user, days_ago=5, days_ahead=5, reason="vacation")
        make_absence(plain_user, days_ago=2, days_ahead=1, reason="leave")

        assert is_available(workspace_member_of(plain_user).id) is False

    def test_a_moment_can_be_asked_about(self, availability_on, make_absence, workspace_member_of, plain_user):
        make_absence(plain_user, days_ago=1, days_ahead=2)
        member_id = workspace_member_of(plain_user).id

        assert is_available(member_id, at=timezone.now() + timedelta(days=1)) is False
        assert is_available(member_id, at=timezone.now() + timedelta(days=5)) is True

    def test_with_the_flag_off_everybody_is_available(self, settings, make_absence, workspace_member_of, plain_user):
        settings.ORCA_AVAILABILITY_ENABLED = True
        make_absence(plain_user)
        settings.ORCA_AVAILABILITY_ENABLED = False

        assert is_available(workspace_member_of(plain_user).id) is True


@pytest.mark.unit
@pytest.mark.django_db
class TestTheBulkForm:
    def test_it_returns_only_the_away_ones(
        self, availability_on, make_absence, workspace_member_of, plain_user, second_user
    ):
        make_absence(plain_user)
        away = workspace_member_of(plain_user).id
        present = workspace_member_of(second_user).id

        assert unavailable_member_ids([away, present]) == {away}

    def test_it_agrees_with_the_single_form(
        self, availability_on, make_absence, workspace_member_of, plain_user, second_user
    ):
        make_absence(plain_user, days_ahead=None)
        make_absence(second_user, days_ago=10, days_ahead=-5)
        ids = [workspace_member_of(plain_user).id, workspace_member_of(second_user).id]

        away = unavailable_member_ids(ids)

        assert {member_id for member_id in ids if not is_available(member_id)} == away

    def test_with_the_flag_off_nobody_is_away(self, settings, make_absence, workspace_member_of, plain_user):
        settings.ORCA_AVAILABILITY_ENABLED = True
        make_absence(plain_user)
        settings.ORCA_AVAILABILITY_ENABLED = False

        assert unavailable_member_ids([workspace_member_of(plain_user).id]) == set()


@pytest.mark.unit
@pytest.mark.django_db
class TestAcceptingNewWork:
    def test_a_membership_with_no_settings_accepts(self, availability_on, unit, add_member, plain_user):
        membership = add_member(unit, plain_user)

        assert accepts_new_work(membership) is True

    def test_switching_it_off_is_read(self, availability_on, unit, add_member, workspace_with_members, plain_user):
        membership = add_member(unit, plain_user)
        MembershipAllocationSettings.objects.create(
            membership=membership, workspace=workspace_with_members, accepts_new_work=False
        )
        membership.refresh_from_db()

        assert accepts_new_work(membership) is False

    def test_with_the_flag_off_everybody_accepts(self, settings, unit, add_member, workspace_with_members, plain_user):
        settings.ORCA_AVAILABILITY_ENABLED = True
        membership = add_member(unit, plain_user)
        MembershipAllocationSettings.objects.create(
            membership=membership, workspace=workspace_with_members, accepts_new_work=False
        )
        membership.refresh_from_db()
        settings.ORCA_AVAILABILITY_ENABLED = False

        assert accepts_new_work(membership) is True

    def test_the_bulk_form_finds_the_rows(
        self, availability_on, unit, add_member, workspace_with_members, plain_user, second_user
    ):
        with_settings = add_member(unit, plain_user)
        without = add_member(unit, second_user)
        MembershipAllocationSettings.objects.create(
            membership=with_settings, workspace=workspace_with_members, max_open_items=3
        )

        found = allocation_settings_for([with_settings.id, without.id])

        assert set(found) == {with_settings.id}
        assert found[with_settings.id].max_open_items == 3


@pytest.mark.unit
@pytest.mark.django_db
class TestTheLimitThatApplies:
    def test_neither_set_means_no_limit(self):
        assert open_item_limit(None, None) is None

    def test_the_tighter_of_the_two_wins(self, workspace_with_members, unit):
        policy = OrganizationalUnitAssignmentPolicy(
            organizational_unit=unit, workspace=workspace_with_members, max_open_items_per_member=5
        )
        personal = MembershipAllocationSettings(max_open_items=3)

        assert open_item_limit(personal, policy) == 3

    def test_a_looser_personal_number_does_not_lift_the_areas_ceiling(self, workspace_with_members, unit):
        """Otherwise anybody could opt out of the area's limit by setting a bigger one."""
        policy = OrganizationalUnitAssignmentPolicy(
            organizational_unit=unit, workspace=workspace_with_members, max_open_items_per_member=5
        )
        personal = MembershipAllocationSettings(max_open_items=50)

        assert open_item_limit(personal, policy) == 5

    def test_only_one_side_set_is_that_side(self, workspace_with_members, unit):
        policy = OrganizationalUnitAssignmentPolicy(
            organizational_unit=unit, workspace=workspace_with_members, max_open_items_per_member=None
        )

        assert open_item_limit(MembershipAllocationSettings(max_open_items=2), policy) == 2
        assert open_item_limit(None, policy) is None


@pytest.mark.unit
@pytest.mark.django_db
class TestTheConfigEndpoint:
    """The interface hides the forms where nothing would read what they save."""

    def test_it_reports_the_feature_as_off_by_default(self, admin_client, workspace_with_members):
        response = admin_client.get(f"/api/orca/workspaces/{workspace_with_members.slug}/config/")

        assert response.data["availability_enabled"] is False

    def test_it_reports_the_feature_as_on_when_it_is(self, admin_client, workspace_with_members, availability_on):
        response = admin_client.get(f"/api/orca/workspaces/{workspace_with_members.slug}/config/")

        assert response.data["availability_enabled"] is True


@pytest.mark.unit
@pytest.mark.django_db
class TestTheRankingReadsAvailability:
    """
    lb-2: four named ways out of the ranking, each of them in the snapshot the
    decision keeps. A coordinator asking "why did nobody get this?" gets an
    answer they can act on, and the four answers call for four different acts.
    """

    @pytest.fixture
    def in_area(self, unit, project, link_project, add_member, grant_manual_access):
        link_project(unit, project)

        def _add(user):
            membership = add_member(unit, user)
            grant_manual_access(project, user)
            return membership

        return _add

    @pytest.fixture
    def automatic_area(self, unit, workspace_with_members):
        """An area that allocates automatically, so `least_loaded` is allowed."""
        return OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode="least_loaded",
            allowed_modes=["least_loaded", "manual"],
        )

    def _reasons(self, ranked):
        return {row["user_id"]: row["excluded_reason"] for row in ranked.excluded}

    def test_the_version_says_lb_2(self):
        from plane.app.services.orca.assignment_service import ALGORITHM_VERSION

        assert ALGORITHM_VERSION == "lb-2"

    def test_somebody_away_is_excluded_as_unavailable(
        self, availability_on, unit, project, in_area, make_absence, plain_user, second_user
    ):
        from plane.app.services.orca.assignment_service import rank_candidates

        in_area(plain_user)
        in_area(second_user)
        make_absence(plain_user)

        ranked = rank_candidates(unit, project.id)

        assert self._reasons(ranked) == {str(plain_user.id): "unavailable"}
        assert ranked.best_user_id == str(second_user.id)

    def test_switching_off_new_work_is_excluded_as_opted_out(
        self, availability_on, unit, project, in_area, workspace_with_members, plain_user, second_user
    ):
        from plane.app.services.orca.assignment_service import rank_candidates

        membership = in_area(plain_user)
        in_area(second_user)
        MembershipAllocationSettings.objects.create(
            membership=membership, workspace=workspace_with_members, accepts_new_work=False
        )

        ranked = rank_candidates(unit, project.id)

        assert self._reasons(ranked) == {str(plain_user.id): "opted_out"}

    def test_the_persons_own_ceiling_is_excluded_as_member_limit(
        self,
        availability_on,
        unit,
        project,
        in_area,
        workspace_with_members,
        make_issue,
        plain_user,
        second_user,
    ):
        from plane.app.services.orca import set_responsibility
        from plane.app.services.orca.assignment_service import rank_candidates

        membership = in_area(plain_user)
        in_area(second_user)
        MembershipAllocationSettings.objects.create(
            membership=membership, workspace=workspace_with_members, max_open_items=1
        )
        set_responsibility(make_issue(project), unit, explicit_executor=plain_user, trigger="internal_api")

        ranked = rank_candidates(unit, project.id)

        assert self._reasons(ranked) == {str(plain_user.id): "member_limit"}

    def test_the_areas_ceiling_is_still_excluded_as_policy_limit(
        self, availability_on, unit, project, in_area, workspace_with_members, make_issue, plain_user
    ):
        from plane.app.services.orca import resolve_policy, set_responsibility
        from plane.app.services.orca.assignment_service import rank_candidates

        in_area(plain_user)
        policy = OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode="least_loaded",
            allowed_modes=["least_loaded"],
            max_open_items_per_member=1,
        )
        set_responsibility(make_issue(project), unit, explicit_executor=plain_user, trigger="internal_api")

        ranked = rank_candidates(unit, project.id, resolve_policy(unit, project.id).policy)

        assert policy.max_open_items_per_member == 1
        assert self._reasons(ranked) == {str(plain_user.id): "policy_limit"}

    def test_being_away_is_reported_ahead_of_being_full(
        self,
        availability_on,
        unit,
        project,
        in_area,
        workspace_with_members,
        make_absence,
        make_issue,
        plain_user,
    ):
        """
        Both are true; only one is worth telling somebody. "Away" is the one
        that says when it stops being true.
        """
        from plane.app.services.orca import set_responsibility
        from plane.app.services.orca.assignment_service import rank_candidates

        membership = in_area(plain_user)
        MembershipAllocationSettings.objects.create(
            membership=membership, workspace=workspace_with_members, max_open_items=1, accepts_new_work=False
        )
        make_absence(plain_user)
        set_responsibility(make_issue(project), unit, explicit_executor=plain_user, trigger="internal_api")

        ranked = rank_candidates(unit, project.id)

        assert self._reasons(ranked) == {str(plain_user.id): "unavailable"}

    def test_with_the_flag_off_nothing_is_excluded(
        self, settings, unit, project, in_area, workspace_with_members, make_absence, plain_user
    ):
        """The way back: switch it off and the ranking is exactly lb-1's."""
        from plane.app.services.orca.assignment_service import rank_candidates

        settings.ORCA_AVAILABILITY_ENABLED = True
        membership = in_area(plain_user)
        make_absence(plain_user)
        MembershipAllocationSettings.objects.create(
            membership=membership, workspace=workspace_with_members, accepts_new_work=False, max_open_items=1
        )
        settings.ORCA_AVAILABILITY_ENABLED = False

        ranked = rank_candidates(unit, project.id)

        assert ranked.excluded == []
        assert ranked.best_user_id == str(plain_user.id)

    def test_the_exclusions_travel_in_the_decision_snapshot(
        self,
        availability_on,
        unit,
        project,
        in_area,
        automatic_area,
        make_absence,
        make_issue,
        plain_user,
        second_user,
    ):
        from plane.app.services.orca import set_responsibility

        in_area(plain_user)
        in_area(second_user)
        make_absence(plain_user)
        issue = make_issue(project)

        result = set_responsibility(issue, unit, requested_mode="least_loaded", trigger="internal_api")

        snapshot = {row["user_id"]: row.get("excluded_reason") for row in result.decision.candidates_snapshot}
        assert snapshot[str(plain_user.id)] == "unavailable"
        assert result.decision.algorithm_version == "lb-2"

    def test_an_area_where_everybody_is_away_fails_allocation_rather_than_waiting(
        self, availability_on, unit, project, in_area, automatic_area, make_absence, make_issue, plain_user
    ):
        """
        The distinction the queue is built on: "ran and found nobody" needs a
        person to fix something, "waiting to be claimed" does not.
        """
        from plane.app.services.orca import set_responsibility
        from plane.db.models.organizational_unit import QueueReason, RoutingState

        in_area(plain_user)
        make_absence(plain_user, days_ahead=None)

        result = set_responsibility(make_issue(project), unit, requested_mode="least_loaded", trigger="internal_api")

        assert result.link.routing_state == RoutingState.ALLOCATION_FAILED
        assert result.link.queue_reason == QueueReason.NO_ELIGIBLE_MEMBER


def my_availability_url(slug):
    return f"/api/orca/workspaces/{slug}/availability/me/"


def member_availability_url(slug, workspace_member_id):
    return f"/api/orca/workspaces/{slug}/members/{workspace_member_id}/availability/"


def allocation_url(slug, unit_id, membership_id):
    return f"/api/orca/workspaces/{slug}/organizational-units/{unit_id}/members/{membership_id}/allocation/"


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAvailabilityEndpoints:
    @pytest.fixture
    def tomorrow(self):
        return (timezone.now() + timedelta(days=1)).isoformat()

    @pytest.fixture
    def next_week(self):
        return (timezone.now() + timedelta(days=8)).isoformat()

    def test_somebody_records_their_own_absence(
        self, availability_on, member_client, workspace_with_members, tomorrow, next_week, plain_user
    ):
        response = member_client.post(
            my_availability_url(workspace_with_members.slug),
            {"unavailable_from": tomorrow, "unavailable_until": next_week, "reason": "vacation"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["reason"] == "vacation"
        assert WorkspaceMemberAvailability.objects.filter(workspace_member__member=plain_user).count() == 1

    def test_the_source_is_always_manual_whatever_the_payload_says(
        self, availability_on, member_client, workspace_with_members, tomorrow
    ):
        """Otherwise a hand-typed row could pose as an import the next sync would own."""
        response = member_client.post(
            my_availability_url(workspace_with_members.slug),
            {"unavailable_from": tomorrow, "source": "hr"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["source"] == "manual"

    def test_an_absence_that_ends_before_it_starts_is_refused(
        self, availability_on, member_client, workspace_with_members, tomorrow, next_week
    ):
        response = member_client.post(
            my_availability_url(workspace_with_members.slug),
            {"unavailable_from": next_week, "unavailable_until": tomorrow},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4930

    def test_an_open_ended_absence_is_allowed(self, availability_on, member_client, workspace_with_members, tomorrow):
        response = member_client.post(
            my_availability_url(workspace_with_members.slug), {"unavailable_from": tomorrow}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["unavailable_until"] is None

    def test_somebody_removes_their_own_absence(
        self, availability_on, member_client, workspace_with_members, make_absence, plain_user
    ):
        row = make_absence(plain_user)

        response = member_client.delete(f"{my_availability_url(workspace_with_members.slug)}{row.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not WorkspaceMemberAvailability.objects.filter(pk=row.id, deleted_at__isnull=True).exists()

    def test_nobody_removes_somebody_elses_through_the_me_route(
        self, availability_on, member_client, workspace_with_members, make_absence, second_user
    ):
        row = make_absence(second_user)

        response = member_client.delete(f"{my_availability_url(workspace_with_members.slug)}{row.id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_coordinator_records_an_absence_for_somebody_in_their_area(
        self,
        availability_on,
        member_client,
        workspace_with_members,
        unit,
        add_member,
        workspace_member_of,
        tomorrow,
        plain_user,
        second_user,
    ):
        """Somebody has to, the day a colleague ends up in hospital."""
        from plane.db.models import OrganizationalUnitCoordinator

        add_member(unit, second_user)
        OrganizationalUnitCoordinator.objects.create(
            organizational_unit=unit,
            workspace_member=workspace_member_of(plain_user),
            workspace=workspace_with_members,
        )

        response = member_client.post(
            member_availability_url(workspace_with_members.slug, workspace_member_of(second_user).id),
            {"unavailable_from": tomorrow, "reason": "leave"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_a_stranger_cannot_record_one_for_somebody(
        self, availability_on, member_client, workspace_with_members, workspace_member_of, tomorrow, second_user
    ):
        response = member_client.post(
            member_availability_url(workspace_with_members.slug, workspace_member_of(second_user).id),
            {"unavailable_from": tomorrow},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_can_always_look(
        self, availability_on, admin_client, workspace_with_members, make_absence, workspace_member_of, plain_user
    ):
        make_absence(plain_user)

        response = admin_client.get(
            member_availability_url(workspace_with_members.slug, workspace_member_of(plain_user).id)
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert len(response.data) == 1

    def test_the_routes_are_gone_while_the_feature_is_off(self, member_client, workspace_with_members):
        response = member_client.get(my_availability_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAllocationEndpoint:
    @pytest.fixture
    def membership(self, unit, add_member, plain_user):
        return add_member(unit, plain_user)

    def test_defaults_are_returned_rather_than_a_404(
        self, availability_on, member_client, workspace_with_members, unit, membership
    ):
        """ "Nobody has touched this" and "no ceiling" are the same state."""
        response = member_client.get(allocation_url(workspace_with_members.slug, unit.id, membership.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["accepts_new_work"] is True
        assert response.data["max_open_items"] is None

    def test_somebody_can_switch_off_new_work_for_themselves(
        self, availability_on, member_client, workspace_with_members, unit, membership
    ):
        response = member_client.put(
            allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"accepts_new_work": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert MembershipAllocationSettings.objects.get(membership=membership).accepts_new_work is False

    def test_somebody_cannot_set_their_own_ceiling(
        self, availability_on, member_client, workspace_with_members, unit, membership
    ):
        """Otherwise anybody could cap themselves at one and still read as available."""
        response = member_client.put(
            allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"max_open_items": 1},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == 4933

    def test_an_admin_sets_the_ceiling(self, availability_on, admin_client, workspace_with_members, unit, membership):
        response = admin_client.put(
            allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"max_open_items": 4},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert MembershipAllocationSettings.objects.get(membership=membership).max_open_items == 4

    def test_a_ceiling_of_zero_is_refused(
        self, availability_on, admin_client, workspace_with_members, unit, membership
    ):
        """Zero means "never anything", which `accepts_new_work` already says, reversibly."""
        response = admin_client.put(
            allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"max_open_items": 0},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_ceiling_can_be_cleared(self, availability_on, admin_client, workspace_with_members, unit, membership):
        response = admin_client.put(
            allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"max_open_items": None},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert MembershipAllocationSettings.objects.get(membership=membership).max_open_items is None

    def test_a_bystander_cannot_touch_somebody_elses_settings(
        self, availability_on, member_client, workspace_with_members, unit, add_member, second_user
    ):
        other = add_member(unit, second_user)

        response = member_client.put(
            allocation_url(workspace_with_members.slug, unit.id, other.id),
            {"accepts_new_work": False},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
