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
    OrcaBuildInfoEndpoint,
    OrcaConfigEndpoint,
    OrganizationalUnitPolicyEndpoint,
    OrganizationalUnitPolicyWriteEndpoint,
    OrganizationalUnitQueueEndpoint,
    OrganizationalUnitDecisionsEndpoint,
    OrganizationalUnitCoordinatorViewSet,
    IssueOrganizationalUnitCandidatesEndpoint,
    IssueOrganizationalUnitClaimEndpoint,
    IssueOrganizationalUnitReassignEndpoint,
    IssueOrganizationalUnitReturnEndpoint,
    IssueOrganizationalUnitSuspendEndpoint,
    IssueOrganizationalUnitTransferEndpoint,
    OrcaMemberAvailabilityEndpoint,
    OrcaMembershipAllocationEndpoint,
    OrcaMyAvailabilityEndpoint,
    OrganizationalDirectoryConnectionEndpoint,
    OrganizationalDirectoryResyncEndpoint,
    OrganizationalDirectoryTokenEndpoint,
    OrganizationalDirectoryUnresolvedEndpoint,
    UserLanguagePreferenceEndpoint,
    SCIMGroupDetailEndpoint,
    SCIMGroupListEndpoint,
    SCIMResourceTypesEndpoint,
    SCIMSchemasEndpoint,
    SCIMServiceProviderConfigEndpoint,
    SCIMUserDetailEndpoint,
    SCIMUserListEndpoint,
)

urlpatterns = [
    # Which commit this container was built from. Instance admins only, and
    # outside the organizational kill switch: it has to answer precisely when
    # something looks wrong, a misconfigured switch included.
    path(
        "orca/build-info/",
        OrcaBuildInfoEndpoint.as_view(),
        name="orca-build-info",
    ),
    # Which Orca features this instance has switched on. Not gated by the
    # organizational-units flag: the UI asks this endpoint whether to render
    # the layer at all, so the switch must not be able to hide it.
    path(
        "orca/workspaces/<str:slug>/config/",
        OrcaConfigEndpoint.as_view(),
        name="orca-config",
    ),
    # The assignment policy in force for an area, and for one of its
    # projects. Resolved, not stored: what the interface needs to know is what
    # would happen, which is the project policy over the area policy over the
    # fallback.
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/policy/",
        OrganizationalUnitPolicyEndpoint.as_view(),
        name="organizational-unit-policy",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/projects/<uuid:project_id>/policy/",
        OrganizationalUnitPolicyEndpoint.as_view(),
        name="organizational-unit-project-policy",
    ),
    # Writing a policy is a separate view from reading one, and deliberately so:
    # the read answers with the *resolved* policy — project over area over
    # fallback — and the write saves a single stored row. One view answering
    # both would have to return something different from what it accepted.
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/policy/write/",
        OrganizationalUnitPolicyWriteEndpoint.as_view(),
        name="organizational-unit-policy-write",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/projects/<uuid:project_id>/policy/write/",
        OrganizationalUnitPolicyWriteEndpoint.as_view(),
        name="organizational-unit-project-policy-write",
    ),
    # The coordinator's surfaces: what the area has waiting, and why it went
    # where it went.
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/queue/",
        OrganizationalUnitQueueEndpoint.as_view(),
        name="organizational-unit-queue",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/decisions/",
        OrganizationalUnitDecisionsEndpoint.as_view(),
        name="organizational-unit-decisions",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/coordinators/",
        OrganizationalUnitCoordinatorViewSet.as_view({"get": "list", "post": "create"}),
        name="organizational-unit-coordinators",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/coordinators/<uuid:pk>/",
        OrganizationalUnitCoordinatorViewSet.as_view({"delete": "destroy"}),
        name="organizational-unit-coordinator",
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
    # The five things a person does to one item of an area's queue. Each one
    # goes through the same service the automation API calls, so a claim from
    # the interface and a reassignment from a robot leave the same record.
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit/claim/",
        IssueOrganizationalUnitClaimEndpoint.as_view(),
        name="issue-organizational-unit-claim",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit/reassign/",
        IssueOrganizationalUnitReassignEndpoint.as_view(),
        name="issue-organizational-unit-reassign",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit/return/",
        IssueOrganizationalUnitReturnEndpoint.as_view(),
        name="issue-organizational-unit-return",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit/suspend/",
        IssueOrganizationalUnitSuspendEndpoint.as_view(),
        name="issue-organizational-unit-suspend",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit/transfer/",
        IssueOrganizationalUnitTransferEndpoint.as_view(),
        name="issue-organizational-unit-transfer",
    ),
    path(
        "orca/workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/organizational-unit/candidates/",
        IssueOrganizationalUnitCandidatesEndpoint.as_view(),
        name="issue-organizational-unit-candidates",
    ),
    # Absences, and what one area may put on one person (Phase 3). Recording a
    # holiday is not an administrative act, so ``availability/me/`` is open to
    # anybody for themselves; the other two check the area's own roles.
    path(
        "orca/workspaces/<str:slug>/availability/me/",
        OrcaMyAvailabilityEndpoint.as_view(),
        name="orca-my-availability",
    ),
    path(
        "orca/workspaces/<str:slug>/members/<uuid:workspace_member_id>/availability/",
        OrcaMemberAvailabilityEndpoint.as_view(),
        name="orca-member-availability",
    ),
    path(
        "orca/workspaces/<str:slug>/organizational-units/<uuid:unit_id>/members/<uuid:pk>/allocation/",
        OrcaMembershipAllocationEndpoint.as_view(),
        name="organizational-unit-member-allocation",
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
    # The signed-in person's own language preference: whether they follow the
    # organization's default, and the way back to following it.
    path(
        "orca/users/me/language-preference/",
        UserLanguagePreferenceEndpoint.as_view(),
        name="orca-user-language-preference",
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
