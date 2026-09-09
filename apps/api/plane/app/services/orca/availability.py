# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Whether a person can take new work right now.

Two questions, and they are not the same one: ``is_available`` is about the
workspace member (leave, vacation, an open-ended window), ``accepts_new_work``
is about one membership (opting out of one area while still working in
another). Ranking (item 3.2) is what combines them with load caps; this
module only answers the two questions.

Both helpers return the permissive answer when ``ORCA_AVAILABILITY_ENABLED``
is off, so flipping the flag cannot change who ``rank_candidates`` picks
until the phase is actually switched on.
"""

from django.db.models import Q
from django.utils import timezone

from plane.db.models import MembershipAllocationSettings, WorkspaceMemberAvailability

from .feature_flags import availability_enabled


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
    if not availability_enabled():
        return True
    instant = timezone.now() if at is None else at
    member_id = getattr(workspace_member, "pk", workspace_member)
    covering = WorkspaceMemberAvailability.objects.filter(
        workspace_member_id=member_id,
        unavailable_from__lte=instant,
    ).filter(Q(unavailable_until__isnull=True) | Q(unavailable_until__gt=instant))
    return not covering.exists()


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
    if not availability_enabled():
        return True
    membership_id = getattr(membership, "pk", membership)
    settings_row = MembershipAllocationSettings.objects.filter(membership_id=membership_id).first()
    if settings_row is None:
        return True
    return bool(settings_row.accepts_new_work)
