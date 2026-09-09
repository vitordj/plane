# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Leave windows and per-membership opt-out (item 3.1).

The tables are the easy half. The helpers are the half that has to stay
permissive while ``ORCA_AVAILABILITY_ENABLED`` is off, so creating a window
cannot change who ``rank_candidates`` picks until an operator turns Phase 3
on. Interval arithmetic is half-open ``[from, until)``; a null ``until`` is
unbounded; overlapping windows are allowed and any one covering ``at`` is
enough.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from plane.app.services.orca.availability import accepts_new_work, is_available
from plane.app.services.orca.feature_flags import availability_enabled
from plane.db.models import (
    AvailabilityReason,
    AvailabilitySource,
    MembershipAllocationSettings,
    WorkspaceMemberAvailability,
)


@pytest.fixture
def member(workspace_member_of, admin_user):
    return workspace_member_of(admin_user)


def window(member, *, start_delta, end_delta=None, **kwargs):
    """
    An availability window relative to now.

    @description ``created_at`` is irrelevant here; the helpers key on
    ``unavailable_from`` / ``unavailable_until``. Deltas are added to
    ``timezone.now()`` once, so a test that freezes nothing still has a
    stable pair of endpoints for the duration of the call.
    """
    now = timezone.now()
    until = None if end_delta is None else now + end_delta
    defaults = {
        "workspace_member": member,
        "workspace": member.workspace,
        "unavailable_from": now + start_delta,
        "unavailable_until": until,
        "reason": AvailabilityReason.VACATION,
        "source": AvailabilitySource.MANUAL,
    }
    defaults.update(kwargs)
    return WorkspaceMemberAvailability.objects.create(**defaults)


@pytest.mark.unit
class TestTheInterval:
    def test_a_closed_window_covers_its_inside_and_not_its_end(self, member, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        now = timezone.now()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            workspace=member.workspace,
            unavailable_from=now,
            unavailable_until=now + timedelta(hours=2),
        )

        assert is_available(member, at=now) is False
        assert is_available(member, at=now + timedelta(hours=1)) is False
        # Half-open: the instant they become available is ``until``, not after.
        assert is_available(member, at=now + timedelta(hours=2)) is True
        assert is_available(member, at=now - timedelta(seconds=1)) is True

    def test_an_open_ended_window_covers_everything_after_from(self, member, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        now = timezone.now()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            workspace=member.workspace,
            unavailable_from=now,
            unavailable_until=None,
        )

        assert is_available(member, at=now) is False
        assert is_available(member, at=now + timedelta(days=400)) is False
        assert is_available(member, at=now - timedelta(seconds=1)) is True

    def test_overlapping_windows_any_one_covering_is_enough(self, member, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        now = timezone.now()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            workspace=member.workspace,
            unavailable_from=now,
            unavailable_until=now + timedelta(hours=3),
            reason=AvailabilityReason.VACATION,
        )
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            workspace=member.workspace,
            unavailable_from=now + timedelta(hours=2),
            unavailable_until=now + timedelta(hours=5),
            reason=AvailabilityReason.LEAVE,
        )

        assert is_available(member, at=now + timedelta(hours=1)) is False
        assert is_available(member, at=now + timedelta(hours=2, minutes=30)) is False
        assert is_available(member, at=now + timedelta(hours=4)) is False
        assert is_available(member, at=now + timedelta(hours=5)) is True
        assert is_available(member, at=now - timedelta(minutes=1)) is True

    def test_no_window_means_available(self, member, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True

        assert is_available(member) is True

    def test_a_window_that_has_not_started_does_not_cover(self, member, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        window(member, start_delta=timedelta(days=1), end_delta=timedelta(days=8))

        assert is_available(member) is True

    def test_until_must_be_after_from(self, member):
        now = timezone.now()
        with pytest.raises(ValidationError, match="unavailable_until"):
            WorkspaceMemberAvailability.objects.create(
                workspace_member=member,
                workspace=member.workspace,
                unavailable_from=now,
                unavailable_until=now,
            )

    def test_the_check_constraint_rejects_a_backwards_window(self, member):
        # save() raises ValidationError first; the CHECK is what a bulk write
        # or a queryset that skipped save() still cannot get past.
        now = timezone.now()
        row = WorkspaceMemberAvailability(
            workspace_member=member,
            workspace=member.workspace,
            unavailable_from=now,
            unavailable_until=now - timedelta(hours=1),
            reason=AvailabilityReason.OTHER,
            source=AvailabilitySource.MANUAL,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            WorkspaceMemberAvailability.objects.bulk_create([row])

    def test_a_window_cannot_point_at_another_workspace(self, member, other_workspace):
        now = timezone.now()
        with pytest.raises(ValidationError, match="different workspaces"):
            WorkspaceMemberAvailability.objects.create(
                workspace_member=member,
                workspace=other_workspace,
                unavailable_from=now,
                unavailable_until=now + timedelta(hours=1),
            )


@pytest.mark.unit
class TestTheFlagLeavesRankingAlone:
    def test_helpers_are_permissive_while_the_flag_is_off(self, member, unit, add_member, admin_user, settings):
        # Default off is the whole point of shipping the tables in 3.1
        # without changing who rank_candidates picks.
        settings.ORCA_AVAILABILITY_ENABLED = False
        now = timezone.now()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            workspace=member.workspace,
            unavailable_from=now - timedelta(hours=1),
            unavailable_until=now + timedelta(hours=1),
        )
        membership = add_member(unit, admin_user)
        MembershipAllocationSettings.objects.create(membership=membership, accepts_new_work=False)

        assert availability_enabled() is False
        assert is_available(member) is True
        assert accepts_new_work(membership) is True

    def test_turning_the_flag_on_makes_the_same_rows_count(self, member, unit, add_member, admin_user, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        now = timezone.now()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            workspace=member.workspace,
            unavailable_from=now - timedelta(hours=1),
            unavailable_until=now + timedelta(hours=1),
        )
        membership = add_member(unit, admin_user)
        MembershipAllocationSettings.objects.create(membership=membership, accepts_new_work=False)

        assert is_available(member) is False
        assert accepts_new_work(membership) is False


@pytest.mark.unit
class TestAcceptsNewWork:
    def test_a_missing_row_is_the_default_yes(self, unit, add_member, admin_user, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        membership = add_member(unit, admin_user)

        assert accepts_new_work(membership) is True

    def test_false_opts_the_membership_out(self, unit, add_member, admin_user, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        membership = add_member(unit, admin_user)
        MembershipAllocationSettings.objects.create(membership=membership, accepts_new_work=False)

        assert accepts_new_work(membership) is False

    def test_true_is_explicit_consent(self, unit, add_member, admin_user, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        membership = add_member(unit, admin_user)
        MembershipAllocationSettings.objects.create(membership=membership, accepts_new_work=True, max_open_items=3)

        assert accepts_new_work(membership) is True

    def test_two_memberships_do_not_share_a_settings_row(self, unit, second_unit, add_member, admin_user, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True
        here = add_member(unit, admin_user)
        there = add_member(second_unit, admin_user)
        MembershipAllocationSettings.objects.create(membership=here, accepts_new_work=False)

        assert accepts_new_work(here) is False
        assert accepts_new_work(there) is True


@pytest.mark.unit
class TestTheConfigEndpoint:
    def test_availability_is_reported_off_by_default(self, admin_client, workspace_with_members):
        response = admin_client.get(f"/api/orca/workspaces/{workspace_with_members.slug}/config/")

        assert response.status_code == 200
        assert response.data["availability_enabled"] is False
        assert "organizational_units_enabled" in response.data
        assert "public_api_enabled" in response.data

    def test_availability_is_reported_on_when_the_flag_is_on(self, admin_client, workspace_with_members, settings):
        settings.ORCA_AVAILABILITY_ENABLED = True

        response = admin_client.get(f"/api/orca/workspaces/{workspace_with_members.slug}/config/")

        assert response.status_code == 200
        assert response.data["availability_enabled"] is True
