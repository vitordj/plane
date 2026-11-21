# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Regression tests for R2 Frente F1 (access, permissions, SCIM, and idempotency).

Pins R2.01, R2.03, R2.04, R2.18, R2.20 (availability views), R2.21, and R2.22.
"""

import pytest
from rest_framework import status

from plane.app.permissions.organizational_unit import is_unit_coordinator, may_manage_member_availability
from plane.app.services.orca import (
    IdempotencyPayloadMismatch,
    reconcile_access,
    set_responsibility,
    start_operation,
)
from plane.db.models import (
    AutomationOperationType,
    OrganizationalDirectoryGroupMembership,
    ProcessInstanceItem,
    ProcessInstanceReference,
    Project,
    ProjectMember,
    WorkspaceMember,
)

from .conftest import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_MEMBER,
    issue_candidates_url,
    my_availability_url,
    public_process_instance_url,
    public_work_items_url,
    scim_group_url,
    unit_projects_url,
    unit_url,
    units_url,
)

SCHEMA_PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"


@pytest.mark.unit
@pytest.mark.django_db
class TestR201RelinquishClaimResetsInheritedRole:
    """R2.01: leaving a project natively then leaving the unit must not resurrect Admin on rejoin."""

    def test_rejoining_public_project_after_native_leave_then_unit_leave_does_not_restore_admin(
        self,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        plain_user,
        member_client,
    ):
        link_project(unit, project, role=ROLE_ADMIN)
        membership = add_member(unit, plain_user)
        reconcile_access(workspace_with_members.id)

        pm = ProjectMember.objects.get(project=project, member=plain_user)
        assert pm.is_active is True
        assert pm.role == ROLE_ADMIN

        # Step 1: member leaves the project natively -> inactive row still holds role=20.
        pm.is_active = False
        pm.save()

        # Step 2: member leaves the unit -> reconciler runs ACTION_RELINQUISH_CLAIM.
        membership.is_active = False
        membership.save()
        reconcile_access(workspace_with_members.id)

        pm.refresh_from_db()
        assert pm.is_active is False
        assert pm.role == ROLE_MEMBER

        # Step 3: member self-joins the public project via upstream invitations endpoint.
        response = member_client.post(
            f"/api/users/me/workspaces/{workspace_with_members.slug}/projects/invitations/",
            {"project_ids": [str(project.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        pm.refresh_from_db()
        assert pm.is_active is True
        assert pm.role == ROLE_MEMBER

    def test_relinquish_claim_restores_manual_baseline_when_present(
        self,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_member,
        grant_manual_access,
        plain_user,
    ):
        grant_manual_access(project, plain_user, role=ROLE_MEMBER)
        link_project(unit, project, role=ROLE_ADMIN)
        membership = add_member(unit, plain_user)
        reconcile_access(workspace_with_members.id)

        pm = ProjectMember.objects.get(project=project, member=plain_user)
        assert pm.role == ROLE_ADMIN

        pm.is_active = False
        pm.save()
        membership.is_active = False
        membership.save()
        reconcile_access(workspace_with_members.id)

        pm.refresh_from_db()
        assert pm.is_active is False
        assert pm.role == ROLE_MEMBER


@pytest.mark.unit
@pytest.mark.django_db
class TestR203ProcessInstanceVisibility:
    """R2.03: ProcessInstanceEndpoint must not leak steps from projects the caller cannot open."""

    def test_guest_with_api_key_is_refused_for_unjoined_project_process(
        self,
        settings,
        workspace_with_members,
        project,
        make_issue,
        guest_user,
        token_client,
    ):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = True
        issue = make_issue(project, name="Secret step")
        instance = ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="espo-onboarding",
            external_instance_id="client-secret",
            template_name="Onboarding",
            template_version="1.0",
        )
        ProcessInstanceItem.objects.create(
            workspace=workspace_with_members,
            process_instance=instance,
            issue=issue,
            step_key="kyc",
        )

        guest_api = token_client(guest_user)
        response = guest_api.get(
            public_process_instance_url(workspace_with_members.slug, "espo-onboarding", "client-secret")
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_only_sees_steps_from_projects_they_belong_to(
        self,
        settings,
        workspace_with_members,
        project,
        second_project,
        grant_manual_access,
        make_issue,
        plain_user,
        token_client,
    ):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = True
        grant_manual_access(project, plain_user, role=ROLE_MEMBER)
        visible_issue = make_issue(project, name="Visible step")
        hidden_issue = make_issue(second_project, name="Hidden step")
        instance = ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="espo-onboarding",
            external_instance_id="client-mixed",
            template_name="Onboarding",
            template_version="1.0",
        )
        ProcessInstanceItem.objects.create(
            workspace=workspace_with_members,
            process_instance=instance,
            issue=visible_issue,
            step_key="visible",
        )
        ProcessInstanceItem.objects.create(
            workspace=workspace_with_members,
            process_instance=instance,
            issue=hidden_issue,
            step_key="hidden",
        )

        member_api = token_client(plain_user)
        response = member_api.get(
            public_process_instance_url(workspace_with_members.slug, "espo-onboarding", "client-mixed")
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        returned_keys = [row["step_key"] for row in response.data["items"]]
        assert returned_keys == ["visible"]


@pytest.mark.unit
@pytest.mark.django_db
class TestR204ScimReplaceAndPutNullSafety:
    """R2.04: PUT without members and PATCH replace with null/missing value must not wipe the group."""

    def test_put_without_members_keeps_existing_group_members(
        self,
        scim_client,
        workspace_with_members,
        bound_unit,
        make_identity,
        put_in_group,
    ):
        ident = make_identity("plain@plane.so")
        put_in_group(bound_unit, ident)

        response = scim_client.put(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {"schemas": [SCHEMA_GROUP], "displayName": "Renamed Group"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert OrganizationalDirectoryGroupMembership.objects.filter(
            organizational_unit=bound_unit, identity=ident
        ).exists()

    def test_put_with_null_members_keeps_existing_group_members(
        self,
        scim_client,
        workspace_with_members,
        bound_unit,
        make_identity,
        put_in_group,
    ):
        ident = make_identity("plain@plane.so")
        put_in_group(bound_unit, ident)

        response = scim_client.put(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {"schemas": [SCHEMA_GROUP], "displayName": "Renamed Group", "members": None},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert OrganizationalDirectoryGroupMembership.objects.filter(
            organizational_unit=bound_unit, identity=ident
        ).exists()

    @pytest.mark.parametrize(
        "operation",
        [
            {"op": "replace", "path": "members"},
            {"op": "replace", "path": "members", "value": None},
            {"op": "replace", "value": {"members": None}},
        ],
    )
    def test_patch_replace_members_without_value_or_null_is_rejected_without_clearing(
        self,
        scim_client,
        workspace_with_members,
        bound_unit,
        make_identity,
        put_in_group,
        operation,
    ):
        ident = make_identity("plain@plane.so")
        put_in_group(bound_unit, ident)

        response = scim_client.patch(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {"schemas": [SCHEMA_PATCH_OP], "Operations": [operation]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert OrganizationalDirectoryGroupMembership.objects.filter(
            organizational_unit=bound_unit, identity=ident
        ).exists()


@pytest.mark.unit
@pytest.mark.django_db
class TestR218IdempotencyScopeAndOperationType:
    """R2.18: idempotency keys must not replay across different routes or operation types."""

    def test_same_payload_different_operation_type_raises_mismatch(self, workspace_with_members):
        handle = start_operation(
            workspace_with_members,
            None,
            "shared-key-op-type",
            AutomationOperationType.REASSIGN,
            {"reason": "test"},
        )
        handle.complete(response={"ok": True}, http_status=200)

        with pytest.raises(IdempotencyPayloadMismatch):
            start_operation(
                workspace_with_members,
                None,
                "shared-key-op-type",
                AutomationOperationType.TRANSFER_UNIT,
                {"reason": "test"},
            )

    def test_same_payload_and_key_on_different_project_route_returns_409(
        self,
        workspace_with_members,
        project,
        second_project,
        unit,
        link_project,
        grant_manual_access,
        admin_user,
        token_client,
    ):
        grant_manual_access(project, admin_user, role=ROLE_ADMIN)
        grant_manual_access(second_project, admin_user, role=ROLE_ADMIN)
        link_project(unit, project)
        link_project(unit, second_project)
        api = token_client(admin_user)
        body = {
            "external": {"source": "erp", "id": "ext-cross-route-1"},
            "work_item": {"name": "Cross-route item"},
            "responsibility": {"unit": unit.slug},
        }

        first = api.post(
            public_work_items_url(workspace_with_members.slug, project.id),
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY="cross-route-key",
        )
        assert first.status_code == status.HTTP_201_CREATED, first.data

        second = api.post(
            public_work_items_url(workspace_with_members.slug, second_project.id),
            body,
            format="json",
            HTTP_IDEMPOTENCY_KEY="cross-route-key",
        )
        assert second.status_code == status.HTTP_409_CONFLICT
        assert second.data["error_code"] == 4924


@pytest.mark.unit
@pytest.mark.django_db
class TestR220AvailabilityMasterKillSwitch:
    """R2.20: availability routes return 404 when ORCA_ORG_UNITS_ENABLED is off."""

    def test_availability_route_returns_404_when_org_units_disabled(
        self, settings, member_client, workspace_with_members
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = member_client.get(my_availability_url(workspace_with_members.slug))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
@pytest.mark.django_db
class TestR221CandidatesAndUnitProjectsVisibility:
    """R2.21: candidates endpoint requires coordinator; unit project lists hide secret projects."""

    def test_plain_member_is_refused_on_issue_candidates(
        self,
        settings,
        member_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        make_issue,
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        link_project(unit, project)
        issue = make_issue(project)
        set_responsibility(issue, unit)

        response = member_client.get(issue_candidates_url(workspace_with_members.slug, project.id, issue.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == 4932

    def test_coordinator_can_read_issue_candidates(
        self,
        settings,
        member_client,
        workspace_with_members,
        unit,
        project,
        link_project,
        add_coordinator,
        plain_user,
        make_issue,
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        link_project(unit, project)
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        set_responsibility(issue, unit)

        response = member_client.get(issue_candidates_url(workspace_with_members.slug, project.id, issue.id))
        assert response.status_code == status.HTTP_200_OK, response.data

    def test_guest_and_non_member_cannot_see_secret_project_on_unit_routes(
        self,
        workspace_with_members,
        admin_user,
        unit,
        link_project,
        member_client,
        guest_client,
        admin_client,
    ):
        secret = Project.objects.create(
            workspace=workspace_with_members,
            name="Secret Ops",
            identifier="SEC",
            network=0,
            created_by=admin_user,
        )
        link_project(unit, secret)

        # Guest sees nothing on projects list or unit detail project_ids.
        guest_projects = guest_client.get(unit_projects_url(workspace_with_members.slug, unit.id))
        assert guest_projects.status_code == status.HTTP_200_OK
        assert guest_projects.data == []

        guest_detail = guest_client.get(unit_url(workspace_with_members.slug, unit.id))
        assert guest_detail.status_code == status.HTTP_200_OK
        assert guest_detail.data["project_ids"] == []
        assert guest_detail.data["project_count"] == 0

        # Non-member workspace Member also does not see secret project.
        member_projects = member_client.get(unit_projects_url(workspace_with_members.slug, unit.id))
        assert member_projects.status_code == status.HTTP_200_OK
        assert member_projects.data == []

        member_list = member_client.get(units_url(workspace_with_members.slug))
        assert member_list.status_code == status.HTTP_200_OK
        row = next(item for item in member_list.data if str(item["id"]) == str(unit.id))
        assert row["project_ids"] == []

        # Admin still sees the secret project.
        admin_projects = admin_client.get(unit_projects_url(workspace_with_members.slug, unit.id))
        assert admin_projects.status_code == status.HTTP_200_OK
        assert len(admin_projects.data) == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestR222CoordinatorAuthorityRevocation:
    """R2.22: coordinator standing ends on Guest demotion or unit deactivation."""

    def test_coordinator_demoted_to_guest_loses_authority(
        self, workspace_with_members, unit, add_coordinator, add_member, plain_user, second_user
    ):
        add_coordinator(unit, plain_user)
        add_member(unit, second_user)
        target_wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=second_user)

        assert is_unit_coordinator(plain_user, unit) is True
        assert may_manage_member_availability(plain_user, target_wm) is True

        coord_wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        coord_wm.role = ROLE_GUEST
        coord_wm.save()

        assert is_unit_coordinator(plain_user, unit) is False
        assert may_manage_member_availability(plain_user, target_wm) is False

    def test_coordinator_on_inactive_unit_loses_authority(
        self, workspace_with_members, unit, add_coordinator, add_member, plain_user, second_user
    ):
        add_coordinator(unit, plain_user)
        add_member(unit, second_user)
        target_wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=second_user)

        assert is_unit_coordinator(plain_user, unit) is True
        assert may_manage_member_availability(plain_user, target_wm) is True

        unit.is_active = False
        unit.save()

        assert is_unit_coordinator(plain_user, unit) is False
        assert may_manage_member_availability(plain_user, target_wm) is False
