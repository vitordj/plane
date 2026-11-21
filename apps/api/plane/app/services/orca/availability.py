# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Whether a person can take new work right now.

Two questions, and they are not the same one: ``is_available`` is about the
workspace member (leave, vacation, an open-ended window), ``accepts_new_work``
is about one membership (opting out of one area while still working in
another). Ranking (item 3.2) combines them with load caps; this module
answers the two questions, including in bulk so ``rank_candidates`` does
not issue one query per person.

Both helpers return the permissive answer when ``ORCA_AVAILABILITY_ENABLED``
is off, so flipping the flag cannot change who ``rank_candidates`` picks
until the phase is actually switched on. The bulk lookups return empty
collections in that case for the same reason.
"""

from django.db.models import Q
from django.utils import timezone

from plane.db.models import MembershipAllocationSettings, WorkspaceMemberAvailability

from .feature_flags import availability_enabled


def all_covering_windows_for(workspace_member_ids, at=None) -> dict:
    """
    All covering unavailability windows per workspace member at ``at``.

    @description Not gated by ``ORCA_AVAILABILITY_ENABLED``. Returned in
    deterministic order so callers that must judge every active absence
    (such as R2.07's intentional-override check) do not depend on whichever
    single overlapping window was picked for display.
    @param workspace_member_ids: WorkspaceMember primary keys to check.
    @param at: Instant to evaluate; defaults to now.
    @returns: ``workspace_member_id -> list[WorkspaceMemberAvailability]``.
    """
    ids = list(workspace_member_ids)
    if not ids:
        return {}
    instant = timezone.now() if at is None else at
    windows = (
        WorkspaceMemberAvailability.objects.filter(
            workspace_member_id__in=ids,
            unavailable_from__lte=instant,
        )
        .filter(Q(unavailable_until__isnull=True) | Q(unavailable_until__gt=instant))
        .order_by("unavailable_from", "created_at", "id")
    )
    grouped: dict = {}
    for window in windows:
        grouped.setdefault(window.workspace_member_id, []).append(window)
    return grouped


def covering_windows_for(workspace_member_ids, at=None) -> dict:
    """
    Covering unavailability window per workspace member at ``at``.

    @description Not gated by ``ORCA_AVAILABILITY_ENABLED``: the availability
    API and the UI (item 3.3) read the rows as they are, even while ranking
    still ignores them. When several windows overlap, an unbounded one wins;
    otherwise the one with the latest ``unavailable_until``. Ties break
    deterministically by latest ``created_at`` then ``id``.

    @param workspace_member_ids: WorkspaceMember primary keys to check.
    @param at: Instant to evaluate; defaults to now.
    @returns: ``workspace_member_id -> WorkspaceMemberAvailability``.
    """
    chosen = {}
    for member_id, windows in all_covering_windows_for(workspace_member_ids, at=at).items():
        winner = windows[0]
        for window in windows[1:]:
            if window.unavailable_until is None:
                if winner.unavailable_until is not None or (window.created_at, str(window.id)) > (
                    winner.created_at,
                    str(winner.id),
                ):
                    winner = window
            elif winner.unavailable_until is not None:
                if (window.unavailable_until, window.created_at, str(window.id)) > (
                    winner.unavailable_until,
                    winner.created_at,
                    str(winner.id),
                ):
                    winner = window
        chosen[member_id] = winner
    return chosen


def unavailable_workspace_member_ids(workspace_member_ids, at=None) -> set:
    """
    Workspace-member ids that have a covering availability window at ``at``.

    @description Same half-open interval as ``is_available``. Empty when the
    flag is off or the id list is empty, so ranking can call this
    unconditionally without changing who it picks until Phase 3 is on.

    @param workspace_member_ids: WorkspaceMember primary keys to check.
    @param at: Instant to evaluate; defaults to now.
    @returns: The subset of ``workspace_member_ids`` that are unavailable.
    """
    if not availability_enabled():
        return set()
    return set(covering_windows_for(workspace_member_ids, at=at))


def allocation_settings_map(membership_ids) -> dict:
    """
    Per-membership allocation knobs, keyed by membership id, ignoring the flag.

    @description The availability API and the members list (item 3.3) need
    the rows even while ranking still treats a missing map as "accepts work".
    A missing row is not in the map: callers treat that as the defaults.

    @param membership_ids: OrganizationalUnitMembership primary keys.
    @returns: ``membership_id -> MembershipAllocationSettings``.
    """
    ids = list(membership_ids)
    if not ids:
        return {}
    return {row.membership_id: row for row in MembershipAllocationSettings.objects.filter(membership_id__in=ids)}


def allocation_settings_for(membership_ids) -> dict:
    """
    Per-membership allocation knobs, keyed by membership id.

    @description Empty when the flag is off or the id list is empty. A
    missing row is not in the map: callers treat that as the defaults
    (accepts work, no personal cap).

    @param membership_ids: OrganizationalUnitMembership primary keys.
    @returns: ``membership_id -> MembershipAllocationSettings``.
    """
    if not availability_enabled():
        return {}
    return allocation_settings_map(membership_ids)


def is_available(workspace_member, at=None) -> bool:
    """
    Whether ``workspace_member`` can be given new work at ``at``.

    @description A window covers ``at`` when ``unavailable_from <= at`` and
    either ``unavailable_until`` is null or ``at < unavailable_until``
    (half-open). Any one covering window is enough. With the flag off this
    is always ``True``, including when windows exist: the rows stay, they
    just do not affect ranking until the operator turns the phase on.

    @param workspace_member: A ``WorkspaceMember`` instance or its primary key.
    @param at: Instant to evaluate; defaults to now.
    @returns: ``True`` when no covering window exists, or the flag is off.
    """
    member_id = getattr(workspace_member, "pk", workspace_member)
    return member_id not in unavailable_workspace_member_ids([member_id], at=at)


def accepts_new_work(membership) -> bool:
    """
    Whether this membership is willing to take more work from its area.

    @description A missing ``MembershipAllocationSettings`` row is the
    default: they accept work. ``accepts_new_work=False`` is the opt-out.
    The personal ``max_open_items`` cap is not applied here — ranking
    (item 3.2) is what compares it to current load. With the flag off this
    is always ``True``, same reason as ``is_available``.

    @param membership: An ``OrganizationalUnitMembership`` instance or its
        primary key.
    @returns: ``True`` when the membership accepts work, or the flag is off.
    """
    membership_id = getattr(membership, "pk", membership)
    settings_row = allocation_settings_for([membership_id]).get(membership_id)
    if settings_row is None:
        return True
    return bool(settings_row.accepts_new_work)
