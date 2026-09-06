# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The two reads an integration needs: which areas exist, and what one has waiting.

``units`` is the map. A client reads it once, learns which areas cover which
projects and how each hands work out, and can then send work without guessing
at slugs or discovering a forbidden mode by having a request refused.

``queue`` is the area's backlog seen from outside — the same rows and the same
order the coordinator's inbox will show (item 2.2), because "what is waiting"
should not depend on which door you came through.

Who may look differs between the two, and deliberately. Any member of the
workspace may see that an area exists: it is organizational structure, not
work. The queue is work — real titles of real items — so it is shown only to
people who are in the area, plus workspace admins. Coordinators join that list
in Phase 2, when the role exists.
"""

# Django imports
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.api.serializers.orca import queue_row, unit_payload
from plane.app.services.orca import ALL_STATES, queue_queryset
from plane.db.models import (
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    RoutingState,
    Workspace,
    WorkspaceMember,
)
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import OrcaPublicBaseAPIView

# Workspace roles, as elsewhere in the layer.
ROLE_ADMIN = 20

# What the ``routing_state`` filter accepts beyond a real state.
QUEUE_STATE_CHOICES = {*RoutingState.values, ALL_STATES}


class OrcaWorkspaceReadEndpoint(OrcaPublicBaseAPIView):
    """Shared workspace resolution and membership check for the read routes."""

    use_read_replica = True

    def workspace_for(self, request, slug):
        """
        @description The workspace, if the token's user is an active member of
        it. Anything else is 403 — including a workspace that does not exist,
        because saying "no such workspace" to a non-member is itself an answer
        about somebody else's tenant.
        @returns ``(workspace, member)`` or ``(None, None)``.
        """
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return None, None
        member = WorkspaceMember.objects.filter(workspace=workspace, member=request.user, is_active=True).first()
        if member is None:
            return None, None
        return workspace, member

    def forbidden(self):
        return Response({"error": "You don't have the required permissions."}, status=status.HTTP_403_FORBIDDEN)


class UnitListEndpoint(OrcaWorkspaceReadEndpoint):
    """
    ``GET /api/v1/orca/workspaces/{slug}/units/``

    @description Every active area, the projects it covers, and the assignment
    policy in force on each (RFC §7.2).
    """

    def get(self, request, slug):
        workspace, member = self.workspace_for(request, slug)
        if workspace is None:
            return self.forbidden()

        units = OrganizationalUnit.objects.filter(workspace=workspace, is_active=True).order_by("name")
        # Every unit's projects in one query rather than one per unit: a
        # workspace with forty areas would otherwise cost forty round trips to
        # render one page.
        links = (
            OrganizationalUnitProject.objects.filter(organizational_unit__in=units, project__archived_at__isnull=True)
            .select_related("project")
            .order_by("project__identifier")
        )
        by_unit = {}
        for link in links:
            by_unit.setdefault(link.organizational_unit_id, []).append(link)

        return self.paginate(
            request=request,
            queryset=units,
            on_results=lambda rows: [unit_payload(unit, by_unit.get(unit.id, [])) for unit in rows],
        )


class UnitQueueEndpoint(OrcaWorkspaceReadEndpoint):
    """
    ``GET /api/v1/orca/workspaces/{slug}/units/{unit_slug}/queue/``

    @description What the area has waiting, overdue first and oldest first.
    Filters: ``routing_state`` (a state, or ``all``), ``overdue=true|false``,
    ``project``.
    """

    def get(self, request, slug, unit_slug):
        workspace, member = self.workspace_for(request, slug)
        if workspace is None:
            return self.forbidden()

        unit = OrganizationalUnit.objects.filter(workspace=workspace, slug=unit_slug, is_active=True).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        if not self._may_see_queue(unit, member):
            return self.forbidden()

        routing_state = request.query_params.get("routing_state")
        if routing_state and routing_state not in QUEUE_STATE_CHOICES:
            return orca_error("ORG_INVALID_ROUTING_TRANSITION")

        overdue = _tri_state(request.query_params.get("overdue"))
        now = timezone.now()
        queryset = queue_queryset(
            unit,
            routing_state=routing_state,
            overdue=overdue,
            project_id=request.query_params.get("project"),
            now=now,
        )

        return self.paginate(
            request=request,
            queryset=queryset,
            on_results=lambda rows: [queue_row(row, now=now) for row in rows],
        )

    def _may_see_queue(self, unit, member):
        """
        @description Members of the area see its queue; so do workspace admins,
        who can already see every item in it through the interface. Everybody
        else does not — the rows carry the titles of real work.
        @returns bool.
        """
        if member.role == ROLE_ADMIN:
            return True
        return OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit, workspace_member=member, is_active=True
        ).exists()


def _tri_state(value):
    """
    @description Read a query parameter that means yes, no, or "did not ask".
    @returns ``True``, ``False`` or ``None``. Anything unrecognized is ``None``
        rather than an error: a filter the server does not understand should
        widen the result, never narrow it to something the caller did not mean.
    """
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes"):
        return True
    if lowered in ("0", "false", "no"):
        return False
    return None


__all__ = ["UnitListEndpoint", "UnitQueueEndpoint"]
