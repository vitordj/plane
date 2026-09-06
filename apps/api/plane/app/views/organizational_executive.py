# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The areas of a workspace, counted, for whoever runs the place (item 5.1, 5.3).

Two routes and one rule between them.

``executive/`` answers with **workspace-wide counts**. A director asking "how
much is Compliance sitting on?" is asking about Compliance, not about the part
of Compliance that happens to be in projects they joined — a count that
silently omitted three projects would be worse than no count, because nobody
can tell it is wrong by looking at it.

``executive/drilldown/`` answers with **rows**, and rows are titles of real
work. Here Plane's own project membership is the authority (F18): items in
projects the reader does not belong to are counted and not shown, and the
answer says how many were withheld. "Seven items in projects you cannot see" is
an honest sentence; quietly returning thirteen of twenty is not.

Both are workspace-Admin only. Not because the numbers are secret — an area's
own coordinator sees more detail than this about their queue — but because a
cross-area comparison is a management artifact, and F23 put it behind the role
that already governs the workspace. The capability that would widen it
(``executive_viewer``) is open decision A4.
"""

# Django imports
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import QueueItemSerializer
from plane.app.services.orca import (
    DEFAULT_PERIOD,
    PERIODS,
    drilldown_queryset,
    executive_metrics,
    period_start,
    readable_projects,
)
from plane.db.models import OrganizationalUnit, Workspace
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView
from .organizational_unit import OrganizationalUnitFeatureMixin

# How many rows one drill-down returns. The drill-down exists to answer "which
# ones?", and past a couple of hundred the honest answer is the area's own
# queue with a filter, not a longer page.
DRILLDOWN_LIMIT = 200


class OrcaExecutiveEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``GET /api/orca/workspaces/{slug}/executive/``

    @description Every active area of the workspace with the nine indicators of
    item 5.1, plus the processes running through them. Parameters: ``period``
    (``7d``, ``30d``, ``90d``) and ``unit`` for one area.

    Cached for five minutes per ``(workspace, period, unit)``; the answer
    carries ``generated_at`` so a reader can tell how old it is. ``refresh=1``
    reads through the cache, which is what somebody chasing a number they
    dispute needs.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return orca_not_found("ORG_DIRECTORY_WORKSPACE_NOT_FOUND")

        period = request.query_params.get("period") or DEFAULT_PERIOD
        if period not in PERIODS:
            return orca_error("ORG_POLICY_INVALID_VALUE")

        unit_id = request.query_params.get("unit") or None
        if unit_id and not OrganizationalUnit.objects.filter(workspace=workspace, id=unit_id).exists():
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        payload = executive_metrics(
            workspace,
            period=period,
            unit_id=unit_id,
            use_cache=request.query_params.get("refresh") not in ("1", "true", "yes"),
        )
        return Response(payload, status=status.HTTP_200_OK)


class OrcaExecutiveDrilldownEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``GET /api/orca/workspaces/{slug}/executive/drilldown/``

    @description The rows behind one number: ``unit``, ``metric`` and the same
    ``period`` the number was read under. Uses the same predicates the
    aggregate used, so the two cannot drift apart without a test noticing.

    Rows are filtered to projects the reader belongs to, and ``hidden`` says how
    many were withheld. A percentile has no drill-down — a percentile of a
    population is not a list, and offering to open one would mean inventing it.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return orca_not_found("ORG_DIRECTORY_WORKSPACE_NOT_FOUND")

        unit = OrganizationalUnit.objects.filter(workspace=workspace, id=request.query_params.get("unit")).first()
        if unit is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        period = request.query_params.get("period") or DEFAULT_PERIOD
        if period not in PERIODS:
            return orca_error("ORG_POLICY_INVALID_VALUE")

        now = timezone.now()
        queryset = drilldown_queryset(
            unit, request.query_params.get("metric"), now=now, since=period_start(period, now)
        )
        if queryset is None:
            return orca_error("ORG_POLICY_INVALID_VALUE")

        visible_projects = readable_projects(request.user, workspace)
        total = queryset.count()
        rows = list(
            queryset.filter(project_id__in=visible_projects)
            .select_related("issue__project", "issue__state")
            .order_by("-queued_at", "-created_at")[:DRILLDOWN_LIMIT]
        )

        return Response(
            {
                "total": total,
                # What the reader is not being shown, and why the two numbers
                # on their screen differ. Silence here is the bug this field
                # exists to prevent.
                "hidden": max(total - queryset.filter(project_id__in=visible_projects).count(), 0),
                "items": QueueItemSerializer(rows, many=True, context={"now": now}).data,
            },
            status=status.HTTP_200_OK,
        )
