# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Public automation routes, under ``/api/v1/orca/``.

The prefix is the version: an incompatible change opens ``/api/v2/orca/``
rather than altering what an integration already depends on.
"""

# Django imports
from django.urls import path

# Module imports
from plane.api.views.orca.units import OrcaUnitListEndpoint, OrcaUnitQueueEndpoint
from plane.api.views.orca.work_items import (
    OrcaWorkItemByExternalEndpoint,
    OrcaWorkItemListCreateEndpoint,
    OrcaWorkItemReassignEndpoint,
    OrcaWorkItemTransferEndpoint,
)

urlpatterns = [
    path(
        "orca/workspaces/<str:slug>/units/",
        OrcaUnitListEndpoint.as_view(),
        name="orca-public-units",
    ),
    path(
        "orca/workspaces/<str:slug>/units/<str:unit_slug>/queue/",
        OrcaUnitQueueEndpoint.as_view(),
        name="orca-public-unit-queue",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/work-items/",
        OrcaWorkItemListCreateEndpoint.as_view(),
        name="orca-public-work-items",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/reassign/",
        OrcaWorkItemReassignEndpoint.as_view(),
        name="orca-public-work-item-reassign",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/transfer/",
        OrcaWorkItemTransferEndpoint.as_view(),
        name="orca-public-work-item-transfer",
    ),
    path(
        "orca/workspaces/<str:slug>/work-items/by-external/<str:source>/<str:external_id>/",
        OrcaWorkItemByExternalEndpoint.as_view(),
        name="orca-public-work-item-by-external",
    ),
]
