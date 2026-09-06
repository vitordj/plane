# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Whether somebody is taking work, and how much (RFC §5.2, §6.4, Phase 3).

Two questions that look alike and are not. "Is this person here?" is about the
person, everywhere: a holiday does not apply to one area and not another.
"Will this area's queue put more on them?" is about one membership: somebody
can be perfectly available and still have stopped accepting new work from one
of their three areas.

Both answers are read at decision time, inside the allocation's lock, never
cached: a window that opened while a request was in flight has to count.

With ``ORCA_AVAILABILITY_ENABLED`` off every function here answers the way it
would have before the feature existed — available, accepting, no limit — so
the ranking degrades to Phase 2's behaviour rather than to a broken one. That
is deliberate: a switch whose off position changes the answers is a switch
nobody flips during an incident.
"""

# Django imports
from django.db.models import Q
from django.utils import timezone

# Module imports
from plane.db.models import MembershipAllocationSettings, WorkspaceMemberAvailability

from .feature_flags import orca_availability_enabled


def unavailable_window(workspace_member_id, at=None):
    """
    @description The absence covering this person at this instant, if any.
    @param workspace_member_id: The ``WorkspaceMember`` to ask about.
    @param at: The moment to judge; defaults to now.
    @returns A ``WorkspaceMemberAvailability`` or ``None``. When several
        windows overlap — two systems recording one absence, somebody
        extending their own — the earliest-starting one is returned, because
        that is the one that explains the absence.
    """
    if not orca_availability_enabled():
        return None

    at = at or timezone.now()
    return (
        WorkspaceMemberAvailability.objects.filter(workspace_member_id=workspace_member_id, unavailable_from__lte=at)
        .filter(Q(unavailable_until__isnull=True) | Q(unavailable_until__gt=at))
        .order_by("unavailable_from")
        .first()
    )


def is_available(workspace_member, at=None) -> bool:
    """
    @description Whether this person is taking work at all.
    @param workspace_member: A ``WorkspaceMember`` or its id.
    @param at: The moment to judge; defaults to now.
    @returns ``True`` when no absence covers the instant — and always ``True``
        while the feature is switched off.
    """
    member_id = getattr(workspace_member, "id", workspace_member)
    if member_id is None:
        return True
    return unavailable_window(member_id, at) is None


def unavailable_member_ids(workspace_member_ids, at=None) -> set:
    """
    @description Which of these people are away, in one query rather than one
    per candidate — the ranking asks about a whole roster at once.
    @param workspace_member_ids: Ids to check.
    @param at: The moment to judge; defaults to now.
    @returns A set of the ids that are covered by an absence.
    """
    if not orca_availability_enabled():
        return set()

    ids = [member_id for member_id in workspace_member_ids if member_id is not None]
    if not ids:
        return set()

    at = at or timezone.now()
    return set(
        WorkspaceMemberAvailability.objects.filter(workspace_member_id__in=ids, unavailable_from__lte=at)
        .filter(Q(unavailable_until__isnull=True) | Q(unavailable_until__gt=at))
        .values_list("workspace_member_id", flat=True)
    )


def allocation_settings(membership):
    """
    @description What this membership will accept, or ``None`` when nobody has
    said — which means the defaults: accepting, no limit of their own.
    @param membership: An ``OrganizationalUnitMembership`` or its id.
    @returns A ``MembershipAllocationSettings`` or ``None``.
    """
    membership_id = getattr(membership, "id", membership)
    if membership_id is None:
        return None
    return MembershipAllocationSettings.objects.filter(membership_id=membership_id).first()


def accepts_new_work(membership) -> bool:
    """
    @description Whether this area's ranking may put more on this person.
    @param membership: An ``OrganizationalUnitMembership`` or its id.
    @returns ``True`` unless the membership says otherwise — and always
        ``True`` while the feature is switched off. Note this says nothing
        about work the person already holds: nothing is taken away because
        somebody stopped accepting more.
    """
    if not orca_availability_enabled():
        return True
    settings_row = allocation_settings(membership)
    return True if settings_row is None else bool(settings_row.accepts_new_work)


def settings_by_membership(membership_ids) -> dict:
    """
    @description The allocation settings of a whole roster, in one query.
    @param membership_ids: Membership ids to look up.
    @returns dict membership_id -> ``MembershipAllocationSettings``; missing
        keys mean the defaults. Empty while the feature is off, so the ranking
        applies no per-person rule at all.
    """
    if not orca_availability_enabled():
        return {}

    ids = [membership_id for membership_id in membership_ids if membership_id is not None]
    if not ids:
        return {}
    return {row.membership_id: row for row in MembershipAllocationSettings.objects.filter(membership_id__in=ids)}
