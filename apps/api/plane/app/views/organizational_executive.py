# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Workspace-admin executive aggregates (RFC Fase 5, item 5.1).

Two routes, both under ``/api/orca/`` and both Workspace Admin only (F18):

* ``GET .../executive/`` — the numbers. Counts include items in projects the
  reader cannot open; each area carries ``hidden_count``. Cached five
  minutes per (workspace, period, unit).
* ``GET .../executive/drill-down/`` — the list behind one number. Never
  includes an item whose project the reader is not a ``ProjectMember`` of.

Neither route is registered on an upstream urlconf.
"""

from uuid import UUID

from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.services.orca.executive_metrics import (
    CACHE_TTL_SECONDS,
    DRILL_METRICS,
    attach_reader_fields,
    build_executive_report,
    cache_key_for,
    drill_down_queryset,
    executive_drill_row,
    parse_period,
    reader_project_ids,
)
from plane.db.models import OrganizationalUnit, Workspace
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView
from .organizational_unit import OrganizationalUnitFeatureMixin


def _workspace(slug: str) -> Workspace | None:
    return Workspace.objects.filter(slug=slug).first()


def _unit_for(workspace: Workspace, raw: str | None) -> tuple[OrganizationalUnit | None, Response | None]:
    """
    @description Resolve the optional ``unit`` query parameter. Missing is
    "every area"; a value that is not a UUID or not in this workspace is an
    error the view returns as-is.
    """
    if raw is None or raw == "":
        return None, None
    try:
        unit_id = UUID(str(raw))
    except (TypeError, ValueError):
        return None, orca_error("ORG_INVALID_QUEUE_FILTER")
    unit = OrganizationalUnit.objects.filter(pk=unit_id, workspace=workspace).first()
    if unit is None:
        return None, orca_not_found("ORG_UNIT_NOT_FOUND")
    return unit, None


class OrganizationalExecutiveEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """``GET /api/orca/workspaces/{slug}/executive/`` — aggregates by area and process."""

    use_read_replica = True

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        period = parse_period(request.query_params.get("period"))
        if period is None:
            return orca_error("ORG_INVALID_QUEUE_FILTER")

        workspace = _workspace(slug)
        if workspace is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        unit, error = _unit_for(workspace, request.query_params.get("unit"))
        if error is not None:
            return error

        now = timezone.now()
        key = cache_key_for(workspace.id, period, unit.id if unit else None)
        cached = cache.get(key)
        if cached is None:
            # Cache the expensive aggregates without reader-specific fields.
            # ``hidden_count`` and delayed-step listing depend on who is
            # looking, so they are filled in after the cache hit.
            cached = build_executive_report(
                workspace,
                period=period,
                unit=unit,
                now=now,
                viewer=None,
            )
            cache.set(key, cached, CACHE_TTL_SECONDS)

        payload = attach_reader_fields(workspace, cached, viewer=request.user, now=now, unit=unit)
        return Response(payload, status=status.HTTP_200_OK)


class OrganizationalExecutiveDrillDownEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """
    ``GET /api/orca/workspaces/{slug}/executive/drill-down/``.

    @description The list behind one cell of the executive table. ``unit``
    and ``metric`` are required. Items in projects the reader does not belong
    to are counted in ``hidden_count`` and omitted from ``results``.
    """

    use_read_replica = True

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        period = parse_period(request.query_params.get("period"))
        if period is None:
            return orca_error("ORG_INVALID_QUEUE_FILTER")

        metric = request.query_params.get("metric")
        if metric not in DRILL_METRICS:
            return orca_error("ORG_INVALID_QUEUE_FILTER")

        workspace = _workspace(slug)
        if workspace is None:
            return orca_not_found("ORG_UNIT_NOT_FOUND")

        unit_raw = request.query_params.get("unit")
        if not unit_raw:
            return orca_error("ORG_INVALID_QUEUE_FILTER")
        unit, error = _unit_for(workspace, unit_raw)
        if error is not None:
            return error

        now = timezone.now()
        visible = reader_project_ids(request.user, workspace)
        queryset, hidden = drill_down_queryset(
            unit,
            metric,
            period=period,
            now=now,
            visible_project_ids=visible,
        )

        def on_results(rows):
            return [executive_drill_row(row, now=now) for row in rows]

        response = self.paginate(request=request, queryset=queryset, on_results=on_results)
        response.data["metric"] = metric
        response.data["unit_id"] = str(unit.id)
        response.data["hidden_count"] = hidden
        return response
