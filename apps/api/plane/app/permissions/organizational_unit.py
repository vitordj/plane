# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who may act on an area, and in which capacity (RFC §8.1, item 2.2).

Plane authorizes by workspace role and project role, and both are the wrong
question for an area's queue. "May this person hand this item to somebody
else?" is not answered by their project role — a project Member is any of the
twenty people who work there — but by whether they run this area. So the
organizational layer has a third axis, small and explicit:

* **coordinator** — runs the area's queue: assigns, reassigns, returns,
  transfers, edits the policy's day-to-day fields, reads the decision log.
* **member** — belongs to the area and may claim work from its queue, see it,
  and hand back what they hold.
* **lead** — the membership role. It governs the area's roster, not its queue:
  a lead who is not a coordinator reads the queue as a member does. Kept
  separate on purpose (RFC §5.2), because SCIM writes ``role`` on the
  membership and would otherwise decide who coordinates.

Workspace Admin passes every check here. Not as a courtesy: an admin can add
themselves as a coordinator in one request anyway, so refusing them would only
add a step, and the settings screens they already own need to read these
endpoints.

The decorator mirrors ``allow_permission`` in shape so the views read the same
as the rest of the app, and it resolves the area once and hands it to the view
in ``request.orca_unit`` — every one of these views needs it, and looking it up
twice invites the two lookups to disagree.
"""

# Python imports
from functools import wraps

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.db.models import (
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMembership,
    WorkspaceMember,
)
from plane.db.models.organizational_unit import OrganizationalUnitMemberRole
from plane.utils.orca_error_codes import orca_error, orca_not_found

# The three capacities a person can hold in an area. Strings, not an enum, so
# a view reads ``allow_unit_role(["coordinator", "member"])`` at a glance.
UNIT_ROLE_COORDINATOR = "coordinator"
UNIT_ROLE_LEAD = "lead"
UNIT_ROLE_MEMBER = "member"


def is_workspace_admin(user, slug=None, workspace_id=None) -> bool:
    """
    @description Whether the person is an active Admin of the workspace.
    @param user: The requesting user.
    @param slug: Workspace slug, when that is what the route carries.
    @param workspace_id: Workspace id, when the object was already loaded.
    @returns ``True`` for an active workspace Admin.
    """
    queryset = WorkspaceMember.objects.filter(member=user, role=20, is_active=True)
    if slug is not None:
        queryset = queryset.filter(workspace__slug=slug)
    if workspace_id is not None:
        queryset = queryset.filter(workspace_id=workspace_id)
    return queryset.exists()


def is_unit_coordinator(user, unit) -> bool:
    """
    @description Whether the person runs this area's queue.
    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit``.
    @returns ``True`` when an active coordination links the two.
    """
    if unit is None or user is None or not user.is_authenticated:
        return False
    return OrganizationalUnitCoordinator.objects.filter(
        organizational_unit=unit,
        workspace_member__member=user,
        is_active=True,
        workspace_member__is_active=True,
    ).exists()


def is_unit_member(user, unit) -> bool:
    """
    @description Whether the person belongs to this area, in either membership
    role. Coordination alone does not make somebody a member: a coordinator may
    run an area whose work they never take, and the difference decides who
    ``claim`` may hand an item to (I4).
    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit``.
    @returns ``True`` when an active membership links the two.
    """
    if unit is None or user is None or not user.is_authenticated:
        return False
    return OrganizationalUnitMembership.objects.filter(
        organizational_unit=unit,
        workspace_member__member=user,
        is_active=True,
        workspace_member__is_active=True,
    ).exists()


def is_unit_lead(user, unit) -> bool:
    """@description Whether the person is this area's lead. @returns bool."""
    if unit is None or user is None or not user.is_authenticated:
        return False
    return OrganizationalUnitMembership.objects.filter(
        organizational_unit=unit,
        workspace_member__member=user,
        role=OrganizationalUnitMemberRole.LEAD,
        is_active=True,
        workspace_member__is_active=True,
    ).exists()


def unit_roles_of(user, unit) -> set:
    """
    @description Every capacity this person holds in this area, in one pass.
    @param user: The requesting user.
    @param unit: The ``OrganizationalUnit``.
    @returns A subset of ``{"coordinator", "lead", "member"}``. A lead is also
        a member, because leading is a membership role — the roster's, not the
        queue's.
    """
    roles = set()
    if unit is None or user is None or not user.is_authenticated:
        return roles
    if is_unit_coordinator(user, unit):
        roles.add(UNIT_ROLE_COORDINATOR)
    membership = (
        OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit,
            workspace_member__member=user,
            is_active=True,
            workspace_member__is_active=True,
        )
        .values_list("role", flat=True)
        .first()
    )
    if membership is not None:
        roles.add(UNIT_ROLE_MEMBER)
        if membership == OrganizationalUnitMemberRole.LEAD:
            roles.add(UNIT_ROLE_LEAD)
    return roles


def unit_of_issue(issue_id):
    """
    @description The area responsible for a work item, or ``None``. The queue's
    permission checks hang off this: which area an item belongs to is what says
    whose coordinator may move it.
    @param issue_id: The work item.
    @returns An ``OrganizationalUnit`` or ``None``.
    """
    link = IssueOrganizationalUnit.objects.filter(issue_id=issue_id).select_related("organizational_unit").first()
    return link.organizational_unit if link else None


def unit_capabilities(user, unit) -> dict:
    """
    @description What this person may do with one item of this area's queue,
    for the interface to render buttons from rather than re-deriving the rules
    in TypeScript. ``can_claim`` says the person could take work — whether a
    *particular* item is claimable also depends on its state and the area's
    policy, which the service decides.
    @param user: The requesting user.
    @param unit: The area.
    @returns ``{"can_claim": bool, "can_assign": bool, "can_return": bool}``.
    """
    roles = unit_roles_of(user, unit)
    admin = is_workspace_admin(user, workspace_id=unit.workspace_id) if unit is not None else False
    coordinates = UNIT_ROLE_COORDINATOR in roles or admin
    return {
        "can_claim": UNIT_ROLE_MEMBER in roles,
        "can_assign": coordinates,
        # Returning what somebody else holds is a coordinator's call; returning
        # your own is always yours, and the view knows who the executor is.
        "can_return": coordinates,
    }


def allow_unit_role(allowed_roles, unit_kwarg="unit_id"):
    """
    @description Gate a view on the caller's capacity in the area named by the
    route, in the spirit of ``allow_permission``. Workspace Admin always
    passes. The area is resolved once and left in ``request.orca_unit``.
    @param allowed_roles: Any of ``"coordinator"``, ``"lead"``, ``"member"``.
    @param unit_kwarg: Name of the URL kwarg carrying the area's id.
    @returns The view's response, 404 when the area is not in this workspace,
        or 403 with ``ORG_UNIT_PERMISSION_DENIED``.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(instance, request, *args, **kwargs):
            slug = kwargs.get("slug")
            unit = OrganizationalUnit.objects.filter(workspace__slug=slug, pk=kwargs.get(unit_kwarg)).first()
            if unit is None:
                return orca_not_found("ORG_UNIT_NOT_FOUND")

            # Belonging to the workspace is the floor: an area's roster cannot
            # let somebody who was removed from the workspace keep reading it.
            if not WorkspaceMember.objects.filter(
                member=request.user, workspace_id=unit.workspace_id, is_active=True
            ).exists():
                return orca_error("ORG_UNIT_PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)

            request.orca_unit = unit
            if is_workspace_admin(request.user, workspace_id=unit.workspace_id):
                return view_func(instance, request, *args, **kwargs)
            if unit_roles_of(request.user, unit) & set(allowed_roles):
                return view_func(instance, request, *args, **kwargs)
            return orca_error("ORG_UNIT_PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)

        return _wrapped_view

    return decorator


def permission_denied() -> Response:
    """@description The 403 the issue-scoped views answer with. @returns Response."""
    return orca_error("ORG_UNIT_PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)
