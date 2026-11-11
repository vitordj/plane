# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Upstream's member, invitation and publish endpoints, exercised together with the areas layer.

@description No other Orca test calls an upstream member/invite/publish endpoint,
so a change upstream in one of them is invisible to the rest of the suite even
though the layer writes native ``ProjectMember`` rows that those endpoints read.
These tests pin the seams, against the REAL endpoints and the real reconciler:

* **#10033 (dc93e1699)** — creating a deploy board needs *project* membership,
  not just workspace membership. An area that is linked to a project does not
  make its non-members members of it.
* **#9866 (b2dd757ec)** — accepting an invitation to project Y must give the
  user a membership in Y and leave an inactive row in project X alone. The
  inactive row built here is the one the reconciler produces when someone
  leaves an area.
* **Leaving a project** through upstream's ``members/leave/`` while still in the
  area that sources the access: pinned below, whichever way the code goes.

If one of these fails after an upstream sync it is a finding about the seam,
not a test to weaken.
"""

import pytest

from plane.app.services.orca.org_unit_reconciler import reconcile_access, reconcile_membership
from plane.db.models import DeployBoard, ProjectMember, ProjectMemberInvite

from .conftest import ROLE_GUEST, ROLE_MEMBER


def deploy_board_url(slug, project_id):
    """@description Upstream's publish route for a project. @returns str."""
    return f"/api/workspaces/{slug}/projects/{project_id}/project-deploy-boards/"


def join_url(slug, project_id, invite_id):
    """@description Upstream's accept-invitation route. @returns str."""
    return f"/api/workspaces/{slug}/projects/{project_id}/join/{invite_id}/"


def leave_url(slug, project_id):
    """@description Upstream's leave-project route. @returns str."""
    return f"/api/workspaces/{slug}/projects/{project_id}/members/leave/"


def native_row(project, user):
    """@description The user's ``ProjectMember`` row in a project, or ``None``. @returns ProjectMember | None."""
    return ProjectMember.objects.filter(project=project, member=user).first()


@pytest.mark.unit
class TestPublishNeedsProjectMembership:
    """#10033: a workspace Member outside the project cannot publish it."""

    def test_a_workspace_member_outside_the_project_cannot_publish_it(
        self,
        member_client,
        workspace_with_members,
        project,
        second_project,
        unit,
        link_project,
        grant_manual_access,
        plain_user,
    ):
        # The project is covered by an area the caller does not belong to. They
        # are a member of an unrelated project, which is what made the old
        # workspace-wide check pass and is the case the fix is about.
        link_project(unit, project, ROLE_MEMBER)
        grant_manual_access(second_project, plain_user)
        assert native_row(project, plain_user) is None

        response = member_client.post(
            deploy_board_url(workspace_with_members.slug, project.id), {"is_comments_enabled": True}, format="json"
        )

        assert response.status_code == 403, getattr(response, "data", None)
        assert not DeployBoard.objects.filter(project=project).exists()

    def test_a_member_the_area_gave_access_to_still_can(
        self, member_client, workspace_with_members, project, unit, link_project, add_member, plain_user
    ):
        """Positive control: access inherited from the area is project membership like any other."""
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        reconcile_membership(membership, force_sync=True)
        assert native_row(project, plain_user).is_active is True

        response = member_client.post(
            deploy_board_url(workspace_with_members.slug, project.id), {"is_comments_enabled": True}, format="json"
        )

        assert response.status_code in (200, 201), getattr(response, "data", None)
        assert DeployBoard.objects.filter(project=project).exists()


@pytest.mark.unit
class TestAcceptingAnInvitationAfterLeavingAnArea:
    """#9866: the invitation's project gets the membership, the old project's row stays inactive."""

    @pytest.fixture
    def left_the_area(self, workspace_with_members, second_project, unit, link_project, add_member, plain_user):
        """
        An inactive ``ProjectMember`` row in project X, as the reconciler leaves it.

        @description Joined to the area and reconciled (the row appears, active),
        then removed from the area and reconciled again (the row goes inactive).
        @returns The inactive row.
        """
        link_project(unit, second_project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        reconcile_membership(membership, force_sync=True)
        assert native_row(second_project, plain_user).is_active is True

        membership.is_active = False
        membership.save()
        reconcile_access(workspace_with_members.id)

        row = native_row(second_project, plain_user)
        assert row is not None and row.is_active is False
        return row

    def test_the_invited_project_gets_the_membership_and_the_old_row_stays_inactive(
        self, member_client, workspace_with_members, project, second_project, plain_user, left_the_area
    ):
        invite = ProjectMemberInvite.objects.create(
            project=project,
            workspace=workspace_with_members,
            email=plain_user.email,
            token="invite-token",
            role=ROLE_GUEST,
        )
        assert native_row(project, plain_user) is None

        response = member_client.post(
            join_url(workspace_with_members.slug, project.id, invite.pk),
            {"token": "invite-token", "accepted": True},
            format="json",
        )

        assert response.status_code == 200, getattr(response, "data", None)
        joined = native_row(project, plain_user)
        assert joined is not None and joined.is_active is True
        assert joined.role == ROLE_GUEST
        # The row for the project the user left the area of is untouched.
        left_the_area.refresh_from_db()
        assert left_the_area.is_active is False


@pytest.mark.unit
class TestLeavingAProjectWhileStillInTheArea:
    """
    Upstream's ``members/leave/`` deactivates the native row; the area still sources the access.

    @description What the next reconcile does is pinned here, not endorsed: with
    the person still in the area, ``reconcile_access`` is the source of truth for
    inherited access and **re-activates** the row, so leaving a project by hand
    does not stick for as long as the area keeps naming the person. Whoever
    changes that (an explicit "left on purpose" marker, say) should change this
    test knowingly.
    """

    def test_the_next_reconcile_reactivates_the_row(
        self, member_client, workspace_with_members, project, unit, link_project, add_member, plain_user
    ):
        link_project(unit, project, ROLE_MEMBER)
        membership = add_member(unit, plain_user)
        reconcile_membership(membership, force_sync=True)
        assert native_row(project, plain_user).is_active is True

        response = member_client.post(leave_url(workspace_with_members.slug, project.id))

        assert response.status_code == 204, getattr(response, "data", None)
        assert native_row(project, plain_user).is_active is False

        reconcile_access(workspace_with_members.id)

        assert native_row(project, plain_user).is_active is True
