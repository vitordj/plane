# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
HTTP contract for item 3.3: absences and per-membership load knobs.

The ranking (3.2) already reads the tables. These routes are how a person
writes them, and how a coordinator writes them for somebody who cannot.
Every route is 404 while ``ORCA_AVAILABILITY_ENABLED`` is off.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from plane.db.models import (
    AvailabilitySource,
    MembershipAllocationSettings,
    WorkspaceMemberAvailability,
)
from plane.utils.orca_error_codes import ORCA_ERROR_CODES

from .conftest import (
    issue_candidates_url,
    member_availability_url,
    members_url,
    membership_allocation_url,
    my_availability_url,
)


@pytest.fixture
def availability_on(settings):
    settings.ORCA_AVAILABILITY_ENABLED = True


@pytest.fixture
def tomorrow():
    return (timezone.now() + timedelta(days=1)).isoformat()


@pytest.fixture
def next_week():
    return (timezone.now() + timedelta(days=8)).isoformat()


@pytest.fixture
def make_absence(workspace_with_members, workspace_member_of):
    def _make(user, **kwargs):
        now = timezone.now()
        defaults = {
            "workspace_member": workspace_member_of(user),
            "workspace": workspace_with_members,
            "unavailable_from": now,
            "unavailable_until": now + timedelta(days=7),
            "reason": "vacation",
            "source": AvailabilitySource.MANUAL,
        }
        defaults.update(kwargs)
        return WorkspaceMemberAvailability.objects.create(**defaults)

    return _make


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAvailabilityEndpoints:
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
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_AVAILABILITY_INVALID_INTERVAL"]

    def test_a_missing_start_is_refused(self, availability_on, member_client, workspace_with_members):
        response = member_client.post(my_availability_url(workspace_with_members.slug), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_AVAILABILITY_FROM_REQUIRED"]

    def test_an_open_ended_absence_is_allowed(self, availability_on, member_client, workspace_with_members, tomorrow):
        response = member_client.post(
            my_availability_url(workspace_with_members.slug), {"unavailable_from": tomorrow}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response.data["unavailable_until"] is None

    def test_somebody_lists_their_own_absences(
        self, availability_on, member_client, workspace_with_members, make_absence, plain_user
    ):
        make_absence(plain_user)

        response = member_client.get(my_availability_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_somebody_removes_their_own_absence(
        self, availability_on, member_client, workspace_with_members, make_absence, plain_user
    ):
        row = make_absence(plain_user)

        response = member_client.delete(my_availability_url(workspace_with_members.slug, row.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not WorkspaceMemberAvailability.objects.filter(pk=row.id, deleted_at__isnull=True).exists()

    def test_nobody_removes_somebody_elses_through_the_me_route(
        self, availability_on, member_client, workspace_with_members, make_absence, second_user
    ):
        row = make_absence(second_user)

        response = member_client.delete(my_availability_url(workspace_with_members.slug, row.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_a_coordinator_records_an_absence_for_somebody_in_their_area(
        self,
        availability_on,
        member_client,
        workspace_with_members,
        unit,
        add_member,
        add_coordinator,
        workspace_member_of,
        tomorrow,
        plain_user,
        second_user,
    ):
        """Somebody has to, the day a colleague ends up in hospital."""
        add_member(unit, second_user)
        add_coordinator(unit, plain_user)

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
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_NOT_AVAILABILITY_MANAGER"]

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
        """'Nobody has touched this' and 'no ceiling' are the same state."""
        response = member_client.get(membership_allocation_url(workspace_with_members.slug, unit.id, membership.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["accepts_new_work"] is True
        assert response.data["max_open_items"] is None

    def test_somebody_can_switch_off_new_work_for_themselves(
        self, availability_on, member_client, workspace_with_members, unit, membership
    ):
        response = member_client.put(
            membership_allocation_url(workspace_with_members.slug, unit.id, membership.id),
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
            membership_allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"max_open_items": 1},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_ALLOCATION_LIMIT_FORBIDDEN"]

    def test_an_admin_sets_the_ceiling(self, availability_on, admin_client, workspace_with_members, unit, membership):
        response = admin_client.put(
            membership_allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"max_open_items": 4},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert MembershipAllocationSettings.objects.get(membership=membership).max_open_items == 4

    def test_a_ceiling_of_zero_is_refused(self, availability_on, admin_client, workspace_with_members, unit, membership):
        response = admin_client.put(
            membership_allocation_url(workspace_with_members.slug, unit.id, membership.id),
            {"max_open_items": 0},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_ceiling_can_be_cleared(self, availability_on, admin_client, workspace_with_members, unit, membership):
        response = admin_client.put(
            membership_allocation_url(workspace_with_members.slug, unit.id, membership.id),
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
            membership_allocation_url(workspace_with_members.slug, unit.id, other.id),
            {"accepts_new_work": False},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.unit
@pytest.mark.django_db
class TestTheMembersListCarriesAvailability:
    def test_leave_and_opt_out_ride_on_the_roster(
        self, admin_client, workspace_with_members, unit, add_member, make_absence, plain_user
    ):
        membership = add_member(unit, plain_user)
        make_absence(plain_user)
        MembershipAllocationSettings.objects.create(membership=membership, accepts_new_work=False, max_open_items=2)

        response = admin_client.get(members_url(workspace_with_members.slug, unit.id))

        assert response.status_code == status.HTTP_200_OK
        row = next(item for item in response.data if item["id"] == str(membership.id))
        assert row["accepts_new_work"] is False
        assert row["max_open_items"] == 2
        assert row["is_available"] is False
        assert row["unavailable_until"] is not None


@pytest.mark.unit
@pytest.mark.django_db
class TestTheCandidatesEndpoint:
    def test_it_ranks_people_the_area_could_hand_the_item_to(
        self,
        availability_on,
        admin_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        grant_manual_access,
        make_issue,
        plain_user,
        second_user,
    ):
        from plane.app.services.orca import set_responsibility

        link_project(unit, project)
        add_member(unit, plain_user)
        add_member(unit, second_user)
        grant_manual_access(project, plain_user)
        grant_manual_access(project, second_user)
        issue = make_issue(project)
        set_responsibility(issue, unit)

        response = admin_client.get(issue_candidates_url(workspace_with_members.slug, project.id, issue.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        ids = {row["user_id"] for row in response.data["candidates"]}
        assert str(plain_user.id) in ids
        assert str(second_user.id) in ids
        assert "display_name" in response.data["candidates"][0]
