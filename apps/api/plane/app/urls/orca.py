# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path
from plane.app.views import (
    WorkspaceProjectStateSettingsEndpoint,
    ProjectStateViewSet,
    ProjectStatePropertyEndpoint,
    WorkspaceProjectLabelSettingsEndpoint,
    WorkspaceProjectLabelViewSet,
    ProjectLabelPropertyEndpoint,
    ProjectProjectLabelEndpoint,
    OrganizationalUnitViewSet,
    OrganizationalUnitMemberViewSet,
    OrganizationalUnitProjectViewSet,
    OrganizationalUnitEffectiveAccessEndpoint,
    OrganizationalUnitWorkloadEndpoint,
    UserOrganizationalUnitsEndpoint,
    IssueOrganizationalUnitEndpoint,
    IssueOrganizationalUnitAssignEndpoint,
    OrcaConfigEndpoint,
    OrganizationalDirectoryConnectionEndpoint,
    OrganizationalDirectoryResyncEndpoint,
    OrganizationalDirectoryTokenEndpoint,
    OrganizationalDirectoryUnresolvedEndpoint,
    SCIMGroupDetailEndpoint,
    SCIMGroupListEndpoint,
    SCIMResourceTypesEndpoint,
    SCIMSchemasEndpoint,
    SCIMServiceProviderConfigEndpoint,
    SCIMUserDetailEndpoint,
    SCIMUserListEndpoint,
)

urlpatterns = [
    # Which Orca features this instance has switched on. Not gated by the
    # organizational-units flag: the UI asks this endpoint whether to render
    # the layer at all, so the switch must not be able to hide it.
    path(
        "orca/workspaces/<str:slug>/config/",
        OrcaConfigEndpoint.as_view(),
        name="orca-config",
    ),
    # Workspace Project State Settings
    path(
        "orca/workspaces/<str:slug>/project-states/settings/",
        WorkspaceProjectStateSettingsEndpoint.as_view(),
        name="workspace-project-state-settings",
    ),
    # Workspace Project States CRUD
    path(
        "orca/workspaces/<str:slug>/project-states/",
        ProjectStateViewSet.as_view({"get": "list", "post": "create"}),
        name="workspace-project-states",
    ),
    path(
        "orca/workspaces/<str:slug>/project-states/<uuid:pk>/",
        ProjectStateViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="workspace-project-state",
    ),
    # Project-level Project State Properties
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/project-state/",
        ProjectStatePropertyEndpoint.as_view(),
        name="project-project-state-property",
    ),
    # Workspace Project Label Settings
    path(
        "orca/workspaces/<str:slug>/project-labels/settings/",
        WorkspaceProjectLabelSettingsEndpoint.as_view(),
        name="workspace-project-label-settings",
    ),
    # Workspace Project Labels CRUD
    path(
        "orca/workspaces/<str:slug>/project-labels/",
        WorkspaceProjectLabelViewSet.as_view({"get": "list", "post": "create"}),
        name="workspace-project-labels",
    ),
    path(
        "orca/workspaces/<str:slug>/project-labels/<uuid:pk>/",
        WorkspaceProjectLabelViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="workspace-project-label",
    ),
    # Project-level Project Label Properties
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/project-labels/",
        ProjectProjectLabelEndpoint.as_view(),
        name="project-project-labels",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/project-label/",
        ProjectLabelPropertyEndpoint.as_view(),
        name="project-project-label-property",
    ),
    # Organizational units — the fork's organizational layer (see FORK.md).
    # Mutations are workspace-admin only; reads are open to workspace members.
    path(
        "orca/workspaces/<str:slug>/organizational-units/me/",
        UserOrganizationalUnitsEndpoint.as_view(),
        name="user-organizational-units",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/",
        OrganizationalUnitViewSet.as_view({"get": "list", "post": "create"}),
        name="organizational-units",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:pk>/",
        OrganizationalUnitViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="organizational-unit",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/members/",
        OrganizationalUnitMemberViewSet.as_view({"get": "list", "post": "create"}),
        name="organizational-unit-members",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/members/<uuid:pk>/",
        OrganizationalUnitMemberViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="organizational-unit-member",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/projects/",
        OrganizationalUnitProjectViewSet.as_view({"get": "list", "post": "create"}),
        name="organizational-unit-projects",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/projects/<uuid:pk>/",
        OrganizationalUnitProjectViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="organizational-unit-project",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/effective-access/",
        OrganizationalUnitEffectiveAccessEndpoint.as_view(),
        name="organizational-unit-effective-access",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/workload/",
        OrganizationalUnitWorkloadEndpoint.as_view(),
        name="organizational-unit-workload",
    ),
    # Work item ownership by organizational unit, and unit-based assignment.
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit/",
        IssueOrganizationalUnitEndpoint.as_view(),
        name="issue-organizational-unit",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit-assign/",
        IssueOrganizationalUnitAssignEndpoint.as_view(),
        name="issue-organizational-unit-assign",
    ),
    # Directory connection administration. Workspace-admin only: issuing a SCIM
    # token hands a machine the power to grant project access.
    path(
        "orca/workspaces/<str:slug>/directory/",
        OrganizationalDirectoryConnectionEndpoint.as_view(),
        name="organizational-directory",
    ),
    path(
        "orca/workspaces/<str:slug>/directory/token/",
        OrganizationalDirectoryTokenEndpoint.as_view(),
        name="organizational-directory-token",
    ),
    path(
        "orca/workspaces/<str:slug>/directory/resync/",
        OrganizationalDirectoryResyncEndpoint.as_view(),
        name="organizational-directory-resync",
    ),
    path(
        "orca/workspaces/<str:slug>/directory/unresolved/",
        OrganizationalDirectoryUnresolvedEndpoint.as_view(),
        name="organizational-directory-unresolved",
    ),
    # SCIM 2.0 provisioning service. The paths are spelled exactly as RFC 7644
    # defines them — capitalized and without a trailing slash — because Entra
    # appends them verbatim to the tenant URL and would follow an APPEND_SLASH
    # redirect with a dropped request body. The slashed spellings are
    # registered alongside so a manual curl or a validator behaves the same.
    path(
        "orca/scim/v2/workspaces/<str:slug>/ServiceProviderConfig",
        SCIMServiceProviderConfigEndpoint.as_view(),
        name="scim-service-provider-config",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/ServiceProviderConfig/",
        SCIMServiceProviderConfigEndpoint.as_view(),
        name="scim-service-provider-config-slash",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/ResourceTypes",
        SCIMResourceTypesEndpoint.as_view(),
        name="scim-resource-types",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/ResourceTypes/",
        SCIMResourceTypesEndpoint.as_view(),
        name="scim-resource-types-slash",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Schemas",
        SCIMSchemasEndpoint.as_view(),
        name="scim-schemas",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Schemas/",
        SCIMSchemasEndpoint.as_view(),
        name="scim-schemas-slash",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Users",
        SCIMUserListEndpoint.as_view(),
        name="scim-users",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Users/",
        SCIMUserListEndpoint.as_view(),
        name="scim-users-slash",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Users/<uuid:identity_id>",
        SCIMUserDetailEndpoint.as_view(),
        name="scim-user",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Users/<uuid:identity_id>/",
        SCIMUserDetailEndpoint.as_view(),
        name="scim-user-slash",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Groups",
        SCIMGroupListEndpoint.as_view(),
        name="scim-groups",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Groups/",
        SCIMGroupListEndpoint.as_view(),
        name="scim-groups-slash",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Groups/<uuid:unit_id>",
        SCIMGroupDetailEndpoint.as_view(),
        name="scim-group",
    ),
    path(
        "orca/scim/v2/workspaces/<str:slug>/Groups/<uuid:unit_id>/",
        SCIMGroupDetailEndpoint.as_view(),
        name="scim-group-slash",
    ),
]
