# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Routes of the Orca automation API, under ``/api/v1/orca/``.

Its own module and its own prefix, per the fork's rule on custom endpoints
(FORK.md §1E): nothing is spliced into an upstream URL list, so an upstream
sync never has to merge these lines.

``/orca/`` is the version boundary as much as ``/v1/`` is. A breaking change
here opens ``/api/v2/orca/`` rather than editing what a running integration
already depends on.
"""

from django.urls import path

from plane.api.views.orca import (
    ProcessInstanceEndpoint,
    UnitListEndpoint,
    UnitQueueEndpoint,
    WorkItemAutomationEndpoint,
    WorkItemByExternalEndpoint,
    WorkItemCompleteEndpoint,
    WorkItemReassignEndpoint,
    WorkItemTransferEndpoint,
)

urlpatterns = [
    path(
        "orca/workspaces/<str:slug>/units/",
        UnitListEndpoint.as_view(http_method_names=["get"]),
        name="orca-units",
    ),
    path(
        "orca/workspaces/<str:slug>/units/<str:unit_slug>/queue/",
        UnitQueueEndpoint.as_view(http_method_names=["get"]),
        name="orca-unit-queue",
    ),
    # `source` and `external_id` stay `str`: an external key is whatever the
    # calling system uses, commonly with a colon in it, and the client
    # percent-encodes anything that would otherwise end the path segment.
    path(
        "orca/workspaces/<str:slug>/work-items/by-external/<str:source>/<str:external_id>/",
        WorkItemByExternalEndpoint.as_view(http_method_names=["get"]),
        name="orca-work-item-by-external",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/work-items/",
        WorkItemAutomationEndpoint.as_view(http_method_names=["post"]),
        name="orca-work-items",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/reassign/",
        WorkItemReassignEndpoint.as_view(http_method_names=["post"]),
        name="orca-work-item-reassign",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/transfer/",
        WorkItemTransferEndpoint.as_view(http_method_names=["post"]),
        name="orca-work-item-transfer",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/complete/",
        WorkItemCompleteEndpoint.as_view(http_method_names=["post"]),
        name="orca-work-item-complete",
    ),
    path(
        "orca/workspaces/<str:slug>/process-instances/<str:source>/<str:instance_id>/",
        ProcessInstanceEndpoint.as_view(http_method_names=["get"]),
        name="orca-process-instance",
    ),
]
