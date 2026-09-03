# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The areas of a workspace, from above.

Workspace Admin only (RFC F18). Not because the numbers are secret — most of
them are visible one queue at a time to anybody in the area — but because the
aggregate is a different thing from the parts: "which area is drowning" is a
management question, and giving it to everyone by default invites the reading
where a high number is somebody's fault rather than a signal.

Every number is defined in ``services/orca/executive_metrics.py`` and written
down in ``docs/orca-executive-metrics.md`` with the query that reproduces it.
A page whose numbers cannot be checked is a page people argue with.
"""

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.services.orca.executive_metrics import PERIODS, executive_summary
from plane.db.models import Workspace
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView
from .organizational_unit import OrganizationalUnitFeatureMixin


class OrganizationalExecutiveEndpoint(OrganizationalUnitFeatureMixin, BaseAPIView):
    """Aggregates by area and by process, for the people who allocate people."""

    use_read_replica = True

    @allow_permission([ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return orca_not_found("ORG_DIRECTORY_WORKSPACE_NOT_FOUND")

        period = request.query_params.get("period", "30d")
        if period not in PERIODS:
            # Refused rather than defaulted: a typo that silently reported a
            # different window is worse than an error, because the number
            # still looks right.
            return orca_error("ORG_INVALID_EXECUTIVE_PERIOD")

        summary = executive_summary(
            workspace,
            period=period,
            unit_id=request.query_params.get("unit") or None,
        )
        return Response(summary, status=status.HTTP_200_OK)
