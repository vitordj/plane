# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who may act on an area's queue.

Plane's own roles answer "what may this person do in this project", which is
the wrong question here: an area spans projects, and the person who runs its
queue is not necessarily an admin of anything. So there are two extra roles,
both scoped to one area — its coordinator and its members — and a decorator
that reads them the way ``allow_permission`` reads native ones.

Workspace Admins always pass. Not because they are trusted with the work, but
because somebody has to be able to unstick an area whose coordinator has left
the company, and the alternative is a queue nobody can reach.
"""

# Python imports
from functools import wraps

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.db.models import (
    OrganizationalUnit,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMembership,
    WorkspaceMember,
)

ROLE_COORDINATOR = "coordinator"
ROLE_UNIT_MEMBER = "member"

WORKSPACE_ADMIN = 20
# A workspace Guest holds no area role, whatever the area's own rows say. The
# assignment engine already refuses to hand them work (a Guest cannot be an
# assignable project member), so letting one read or move an area's queue
# would give them a say over work they can never be given.
WORKSPACE_MEMBER = 15


def is_unit_coordinator(user, unit) -> bool:
    """@description Whether this person runs that area's queue."""
    if user is None or unit is None or user.is_anonymous:
        return False
    return OrganizationalUnitCoordinator.objects.filter(
        organizational_unit=unit,
        is_active=True,
        workspace_member__member=user,
        workspace_member__is_active=True,
        workspace_member__role__gte=WORKSPACE_MEMBER,
    ).exists()


def is_unit_member(user, unit) -> bool:
    """@description Whether this person belongs to that area."""
    if user is None or unit is None or user.is_anonymous:
        return False
    return OrganizationalUnitMembership.objects.filter(
        organizational_unit=unit,
        is_active=True,
        workspace_member__member=user,
        workspace_member__is_active=True,
        workspace_member__role__gte=WORKSPACE_MEMBER,
    ).exists()


def is_workspace_admin(user, workspace_id) -> bool:
    """@description Whether this person administers the workspace the area is in."""
    if user is None or user.is_anonymous:
        return False
    return WorkspaceMember.objects.filter(
        member=user, workspace_id=workspace_id, role=WORKSPACE_ADMIN, is_active=True
    ).exists()


def unit_roles_of(user, unit) -> set:
    """
    @description Every area-scoped role this person holds here.
    @returns: A set of ``coordinator`` / ``member``, possibly empty.
    """
    roles = set()
    if is_unit_coordinator(user, unit):
        roles.add(ROLE_COORDINATOR)
    if is_unit_member(user, unit):
        roles.add(ROLE_UNIT_MEMBER)
    return roles


def allow_unit_role(allowed_roles, unit_kwarg="unit_id"):
    """
    Restrict a view to people who hold a role in the area it acts on.

    @description Reads the area from the URL, resolves the caller's roles in
    it, and lets a workspace Admin through regardless. A caller with no role
    gets 403 with the standard body — never a hint about who does have one.
    @param allowed_roles: ``coordinator``, ``member``, or both.
    @param unit_kwarg: Which URL argument names the area.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(instance, request, *args, **kwargs):
            unit_id = kwargs.get(unit_kwarg)
            unit = OrganizationalUnit.objects.filter(pk=unit_id, workspace__slug=kwargs.get("slug")).first()
            if unit is None:
                return Response({"error": "Organizational unit not found"}, status=status.HTTP_404_NOT_FOUND)

            if is_workspace_admin(request.user, unit.workspace_id) or (
                unit_roles_of(request.user, unit) & set(allowed_roles)
            ):
                return view_func(instance, request, *args, **kwargs)

            return Response(
                {"error": "You don't have the required permissions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return _wrapped_view

    return decorator
