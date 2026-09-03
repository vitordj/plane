# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Whether the ranking may hand somebody more work.

Three separate questions, deliberately not merged into one "is this person
free" flag:

- **Away** (``is_available``) is about the person and the whole workspace: on
  holiday is on holiday everywhere.
- **Taking new work** (``accepts_new_work``) is about one membership: somebody
  can be at capacity for one area and still take the occasional thing from
  another.
- **At their ceiling** (``open_item_limit``) is a number, and the tighter of
  the person's own limit and the area's policy wins — a limit that some other
  setting can loosen is not a limit.

None of them takes work away. They keep automatic allocation from adding to
it; a coordinator can still hand somebody a work item by name, because
sometimes that is exactly right and the system should not be the one arguing.

With ``ORCA_AVAILABILITY_ENABLED=0`` every answer here is the permissive one,
so the ranking behaves exactly as it did before this existed.
"""

# Django imports
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

# Module imports
from plane.db.models import MembershipAllocationSettings, WorkspaceMemberAvailability


def availability_enabled() -> bool:
    """@description Whether this instance reads availability at all."""
    return bool(getattr(settings, "ORCA_AVAILABILITY_ENABLED", False))


def is_available(workspace_member_id, at=None) -> bool:
    """
    Say whether somebody is available at a moment.

    @description Any interval covering the moment makes them unavailable —
    overlapping rows are a legitimate way to record "away all week, and the
    medical leave inside it is a different thing", so this asks whether *any*
    of them covers, never tries to reconcile them into one.
    @param workspace_member_id: The ``WorkspaceMember`` id.
    @param at: The moment to ask about; defaults to now.
    @returns: ``True`` when nothing says they are away.
    """
    if not availability_enabled():
        return True

    moment = at or timezone.now()
    return not WorkspaceMemberAvailability.objects.filter(
        Q(unavailable_until__isnull=True) | Q(unavailable_until__gt=moment),
        workspace_member_id=workspace_member_id,
        unavailable_from__lte=moment,
        deleted_at__isnull=True,
    ).exists()


def unavailable_member_ids(workspace_member_ids, at=None) -> set:
    """
    The away ones, in one query.

    @description The list form of ``is_available``, for the ranking: asking
    per candidate would put one query per person on the hot path of every
    automatic allocation.
    @param workspace_member_ids: The ``WorkspaceMember`` ids to check.
    @param at: The moment to ask about; defaults to now.
    @returns: The subset that is away, as a set of ids.
    """
    if not availability_enabled() or not workspace_member_ids:
        return set()

    moment = at or timezone.now()
    return set(
        WorkspaceMemberAvailability.objects.filter(
            Q(unavailable_until__isnull=True) | Q(unavailable_until__gt=moment),
            workspace_member_id__in=list(workspace_member_ids),
            unavailable_from__lte=moment,
            deleted_at__isnull=True,
        ).values_list("workspace_member_id", flat=True)
    )


def accepts_new_work(membership) -> bool:
    """
    @description Whether this membership is taking new work from its area. No
    settings row means yes: the feature is opt-out, so somebody who has never
    touched it behaves exactly as before.
    @param membership: An ``OrganizationalUnitMembership``.
    @returns: ``True`` unless they switched it off.
    """
    if not availability_enabled() or membership is None:
        return True

    row = getattr(membership, "allocation_settings", None)
    return True if row is None else bool(row.accepts_new_work)


def allocation_settings_for(membership_ids) -> dict:
    """
    @description Per-membership settings in one query, for the ranking.
    @param membership_ids: ``OrganizationalUnitMembership`` ids.
    @returns: ``{membership_id: MembershipAllocationSettings}``, missing rows
        simply absent — the caller treats absence as the default.
    """
    if not availability_enabled() or not membership_ids:
        return {}

    return {
        row.membership_id: row
        for row in MembershipAllocationSettings.objects.filter(
            membership_id__in=list(membership_ids), deleted_at__isnull=True
        )
    }


def open_item_limit(member_settings, policy) -> int | None:
    """
    The ceiling that actually applies to one person in one area.

    @description The tighter of the two wins. A person who set themselves a
    limit of three does not get five because the area allows five, and an area
    that caps at five does not have to be edited every time somebody sets a
    looser personal number.
    @param member_settings: Their ``MembershipAllocationSettings``, or None.
    @param policy: The resolved ``OrganizationalUnitAssignmentPolicy``, or None.
    @returns: The limit, or ``None`` when neither sets one.
    """
    personal = getattr(member_settings, "max_open_items", None) if member_settings else None
    from_policy = getattr(policy, "max_open_items_per_member", None) if policy else None

    limits = [limit for limit in (personal, from_policy) if limit is not None]
    return min(limits) if limits else None
