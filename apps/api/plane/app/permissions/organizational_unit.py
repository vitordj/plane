# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who may act on an area's work, in one place.

Before this module the answer was written twice and differently: the public
queue endpoint had a private ``_may_see_queue`` that knew about members and
workspace admins, and nothing at all knew about coordinators, because the role
did not exist. Phase 2 adds a second surface over the same rows, and two
surfaces disagreeing about who may look is exactly the kind of divergence that
ships as a leak — so the meaning lives here and both call it.

Three ties give a person standing on an area, and they answer different
questions:

* **workspace Admin** — may do anything, everywhere, and always passes. Not a
  courtesy: an admin can already read and reassign every item through the
  native interface, so refusing them here would only mean the coordinator's
  screen shows less than the screen next to it.
* **coordinator** — answers for the area's work: hands it out, takes it back,
  moves it between areas. Does *not* have to belong to the area (RFC §5.2).
* **member** — does the area's work: sees the queue, and may claim from it
  where the policy allows self-claim.

Being a unit *lead* grants nothing here on purpose. Leading an area is about
the area's people, not about its work item routing; the fork's rule is that
authority over work comes from coordination, which is granted explicitly.

The decorators mirror ``allow_permission`` deliberately — same shape, same
403 body, same "declare it above the handler" reading — so that a person
reviewing an Orca view does not have to learn a second permission dialect.
"""

# Python imports
from functools import wraps

# Django imports
from django.db.models import Q

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.db.models import (
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMembership,
    ProjectMember,
    WorkspaceMember,
)
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import ROLE

# The two ties ``allow_unit_role`` understands. Strings rather than an enum so a
# route reads as prose: ``allow_unit_role([UNIT_COORDINATOR])``.
UNIT_COORDINATOR = "coordinator"
UNIT_MEMBER = "member"


def permission_denied():
    """The body ``allow_permission`` answers with, so both dialects read alike."""
    return Response({"error": "You don't have the required permissions."}, status=status.HTTP_403_FORBIDDEN)


def is_workspace_admin(user, workspace_id) -> bool:
    """
    @description Whether the person is an active Admin of the workspace.
    @param user: The requesting user.
    @param workspace_id: Workspace the area belongs to.
    @returns bool.
    """
    if user is None or getattr(user, "is_anonymous", False):
        return False
    return WorkspaceMember.objects.filter(
        member=user, workspace_id=workspace_id, role=ROLE.ADMIN.value, is_active=True
    ).exists()


def is_unit_coordinator(user, unit) -> bool:
    """
    @description Whether the person actively coordinates this area. The
    workspace membership must be active too: somebody removed from the
    workspace stops coordinating it, and leaving that to the coordinator row
    alone would keep a departed person's authority alive.
    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit``.
    @returns bool.
    """
    if unit is None or user is None or getattr(user, "is_anonymous", False):
        return False
    return OrganizationalUnitCoordinator.objects.filter(
        organizational_unit=unit,
        workspace_member__member=user,
        workspace_member__is_active=True,
        is_active=True,
    ).exists()


def is_unit_member(user, unit) -> bool:
    """
    @description Whether the person actively belongs to this area, in any unit
    role. Same active-workspace-membership rule as above.
    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit``.
    @returns bool.
    """
    if unit is None or user is None or getattr(user, "is_anonymous", False):
        return False
    return OrganizationalUnitMembership.objects.filter(
        organizational_unit=unit,
        workspace_member__member=user,
        workspace_member__is_active=True,
        is_active=True,
    ).exists()


def may_see_queue(user, unit) -> bool:
    """
    @description Who may read an area's queue: the people in it, the person
    answering for it, and workspace admins. Nobody else — the rows carry the
    titles of real work, which is not organizational structure.

    One definition for both APIs (the public ``/api/v1/orca/`` read and the
    internal inbox), because "what is waiting" must not depend on which door
    the caller came through.
    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit``.
    @returns bool.
    """
    if unit is None:
        return False
    if is_workspace_admin(user, unit.workspace_id):
        return True
    return is_unit_member(user, unit) or is_unit_coordinator(user, unit)


def viewer_standing(user, unit) -> dict:
    """
    @description The three facts the interface needs to decide what to render,
    resolved once per page rather than once per row.
    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit``.
    @returns ``{"is_admin": bool, "is_coordinator": bool, "is_member": bool}``.
    """
    return {
        "is_admin": is_workspace_admin(user, unit.workspace_id),
        "is_coordinator": is_unit_coordinator(user, unit),
        "is_member": is_unit_member(user, unit),
    }


def readable_project_ids(user, unit):
    """
    @description Which of an area's projects this caller may be shown work from,
    or ``None`` when the caller may be shown all of them (review finding R1.A7).

    Being a member of an area is not the same as being able to open its
    projects, and the layer itself is what pulls those apart: archiving a
    project drops it from ``_active_sources``, so the reconciler deactivates the
    ``ProjectMember`` rows it had granted, and ``unit_covers_project`` stops
    counting it as covered. Both halves agree that the project is out of reach
    while the queue, filtered only by area and state, kept handing out its work
    item titles and its executors' email addresses.

    ``None`` rather than a list of every id for a workspace admin, so the caller
    can skip the filter entirely instead of building a set the size of the
    tenant.

    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit`` whose queue is being read.
    @returns A set of project ids, or ``None`` for no restriction.
    """
    if is_workspace_admin(user, unit.workspace_id):
        return None
    memberships = ProjectMember.objects.filter(member=user, is_active=True, project__workspace_id=unit.workspace_id)
    return set(memberships.values_list("project_id", flat=True))


def unit_for_issue(issue_id, *, slug=None, project_id=None):
    """
    @description The area responsible for a work item, or ``None`` when no area
    owns it. Scoped by slug and project when the caller has them, so a work item
    id from another tenant cannot be used to read this one's routing.
    @param issue_id: The work item.
    @param slug: Workspace slug from the route.
    @param project_id: Project id from the route.
    @returns The ``OrganizationalUnit``, or ``None``.
    """
    link = link_for_issue(issue_id, slug=slug, project_id=project_id)
    return link.organizational_unit if link else None


def link_for_issue(issue_id, *, slug=None, project_id=None):
    """
    @description The ``IssueOrganizationalUnit`` row behind ``unit_for_issue``,
    for the callers that need the routing state as well as the area.
    @returns The link with its area selected, or ``None``.
    """
    filters = Q(issue_id=issue_id)
    if slug is not None:
        filters &= Q(workspace__slug=slug)
    if project_id is not None:
        filters &= Q(project_id=project_id)
    return IssueOrganizationalUnit.objects.filter(filters).select_related("organizational_unit").first()


def _has_standing(user, unit, roles) -> bool:
    """Whether one of the accepted ties holds. Workspace Admin always passes."""
    if is_workspace_admin(user, unit.workspace_id):
        return True
    if UNIT_COORDINATOR in roles and is_unit_coordinator(user, unit):
        return True
    if UNIT_MEMBER in roles and is_unit_member(user, unit):
        return True
    return False


def allow_unit_role(roles, unit_kwarg="unit_id", error_code=None):
    """
    Guard a route addressed by area id.

    @description Resolves the area from the route, refuses when the caller
    holds none of ``roles`` over it, and hands the resolved area to the view on
    ``request.organizational_unit`` so the check does not cost a second query.
    A workspace Admin always passes.

    @param roles: Accepted ties, from ``UNIT_COORDINATOR``/``UNIT_MEMBER``.
    @param unit_kwarg: Name of the URL kwarg carrying the area id.
    @param error_code: Orca code for the 403. ``None`` answers with the same
        plain body ``allow_permission`` uses — right where being refused says
        nothing more specific than "not you".
    @returns The decorator.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(instance, request, *args, **kwargs):
            unit = OrganizationalUnit.objects.filter(
                pk=kwargs.get(unit_kwarg), workspace__slug=kwargs.get("slug")
            ).first()
            if unit is None:
                return orca_not_found("ORG_UNIT_NOT_FOUND")
            if not _has_standing(request.user, unit, roles):
                return orca_error(error_code, status.HTTP_403_FORBIDDEN) if error_code else permission_denied()
            request.organizational_unit = unit
            return view_func(instance, request, *args, **kwargs)

        return _wrapped_view

    return decorator


def allow_issue_unit_role(roles, error_code=None):
    """
    Guard a route addressed by work item, where the area is whichever one owns it.

    @description Answers 404 ``ORG_WORK_ITEM_HAS_NO_UNIT`` when no area is
    responsible — not 403. An item outside the organizational layer has no
    coordinator to refuse the caller on behalf of, and reporting it as
    forbidden would tell the caller an area exists.

    Hands the resolved link to the view on ``request.organizational_unit_link``.

    @param roles: Accepted ties, from ``UNIT_COORDINATOR``/``UNIT_MEMBER``.
    @param error_code: Orca code for the 403, as above.
    @returns The decorator.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(instance, request, *args, **kwargs):
            link = link_for_issue(
                kwargs.get("issue_id"),
                slug=kwargs.get("slug"),
                project_id=kwargs.get("project_id"),
            )
            if link is None:
                return orca_not_found("ORG_WORK_ITEM_HAS_NO_UNIT")
            if not _has_standing(request.user, link.organizational_unit, roles):
                return orca_error(error_code, status.HTTP_403_FORBIDDEN) if error_code else permission_denied()
            request.organizational_unit_link = link
            return view_func(instance, request, *args, **kwargs)

        return _wrapped_view

    return decorator


__all__ = [
    "UNIT_COORDINATOR",
    "UNIT_MEMBER",
    "allow_issue_unit_role",
    "allow_unit_role",
    "is_unit_coordinator",
    "is_unit_member",
    "is_workspace_admin",
    "link_for_issue",
    "may_see_queue",
    "permission_denied",
    "readable_project_ids",
    "unit_for_issue",
    "viewer_standing",
]
