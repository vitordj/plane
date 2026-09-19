# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for the Orca workspace-level project label layer.

The fork lets a workspace define labels centrally and push them down into the
projects that opt in. Three moving parts, none of them tested before:

* ``/project-labels/`` — CRUD on workspace-level labels (``Label`` rows with no
  project), gated on ``WorkSpaceAdminPermission`` (which, despite the name,
  admits members — see ``TestWorkspaceLabelCrud``);
* ``propagate_workspace_label_change`` — every write to one of those mirrors
  into the ``Label`` rows of each *enabled* project;
* ``sync_workspace_labels_to_project`` — switching a project on back-fills it
  with everything the workspace already has.

Propagation is a fan-out of ``update_or_create`` and soft deletes across other
projects' rows, so what matters is exactly which projects it reaches and which
rows it leaves alone. That is a database question.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    Label,
    ProjectLabelProperty,
    ProjectMember,
    ProjectProjectLabel,
    WorkspaceProjectLabelSettings,
)

from .conftest import ROLE_ADMIN

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    """``ProjectBasePermission`` gates the project-scoped endpoints on a project role."""
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def enabled_project(project):
    """A project opted in to the workspace label layer."""
    ProjectLabelProperty.objects.create(project=project, is_enabled=True)
    return project


@pytest.fixture
def opted_out_project(second_project):
    """A project that has a property row but is switched off."""
    ProjectLabelProperty.objects.create(project=second_project, is_enabled=False)
    return second_project


@pytest.fixture
def make_workspace_label(workspace_with_members):
    def _make(name, color="#111111", description=""):
        return Label.objects.create(
            name=name, color=color, description=description, workspace=workspace_with_members, project=None
        )

    return _make


def settings_url(slug):
    return f"/api/orca/workspaces/{slug}/project-labels/settings/"


def labels_url(slug):
    return f"/api/orca/workspaces/{slug}/project-labels/"


def label_url(slug, label_id):
    return f"{labels_url(slug)}{label_id}/"


def project_labels_url(slug, project_id):
    return f"/api/orca/workspaces/{slug}/projects/{project_id}/project-labels/"


def project_label_property_url(slug, project_id):
    return f"/api/orca/workspaces/{slug}/projects/{project_id}/project-label/"


def mirrored(project, name):
    """The project-level label mirroring a workspace label, if one is live."""
    return Label.objects.filter(project=project, name=name, deleted_at__isnull=True).first()


# --- the workspace switch ----------------------------------------------------


@pytest.mark.contract
class TestWorkspaceLabelSettings:
    def test_get_creates_the_row_switched_off(self, admin_client, workspace_with_members):
        assert not WorkspaceProjectLabelSettings.objects.filter(workspace=workspace_with_members).exists()

        response = admin_client.get(settings_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["is_enabled"] is False
        assert WorkspaceProjectLabelSettings.objects.filter(workspace=workspace_with_members).count() == 1

    def test_get_is_idempotent(self, admin_client, workspace_with_members):
        admin_client.get(settings_url(workspace_with_members.slug))
        admin_client.get(settings_url(workspace_with_members.slug))

        assert WorkspaceProjectLabelSettings.objects.filter(workspace=workspace_with_members).count() == 1

    def test_patch_turns_it_on(self, admin_client, workspace_with_members):
        response = admin_client.patch(settings_url(workspace_with_members.slug), {"is_enabled": True}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["is_enabled"] is True
        assert WorkspaceProjectLabelSettings.objects.get(workspace=workspace_with_members).is_enabled is True

    def test_a_member_may_read_it_but_not_change_it(self, member_client, workspace_with_members):
        """
        The fork does mean admin-only here. Upstream's ``WorkSpaceAdminPermission``
        admits Admin *and* Member despite its name, which let a plain member flip
        a workspace-wide switch that rewrites the labels of every opted-in
        project. Writes now go through ``WorkspaceAdminOnlyPermission``; reads
        keep the broader rule so the settings screen still renders for members.
        """
        assert member_client.get(settings_url(workspace_with_members.slug)).status_code == status.HTTP_200_OK
        assert (
            member_client.patch(
                settings_url(workspace_with_members.slug), {"is_enabled": True}, format="json"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert not WorkspaceProjectLabelSettings.objects.filter(
            workspace=workspace_with_members, is_enabled=True
        ).exists()

    def test_a_guest_cannot_read_or_change_it(self, guest_client, workspace_with_members):
        assert guest_client.get(settings_url(workspace_with_members.slug)).status_code == status.HTTP_403_FORBIDDEN
        assert (
            guest_client.patch(
                settings_url(workspace_with_members.slug), {"is_enabled": True}, format="json"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )


# --- CRUD on the workspace labels themselves ---------------------------------


@pytest.mark.contract
class TestWorkspaceLabelCrud:
    def test_create_stores_a_workspace_level_label(self, admin_client, workspace_with_members):
        response = admin_client.post(
            labels_url(workspace_with_members.slug), {"name": "Compliance", "color": "#FF0000"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        label = Label.objects.get(id=response.data["id"])
        assert label.project_id is None, "a workspace label must not belong to a project"
        assert label.workspace_id == workspace_with_members.id

    def test_list_shows_only_workspace_labels(
        self, admin_client, workspace_with_members, project, make_workspace_label
    ):
        make_workspace_label("Compliance")
        Label.objects.create(name="Local only", color="#00FF00", workspace=workspace_with_members, project=project)

        response = admin_client.get(labels_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert {row["name"] for row in response.data} == {"Compliance"}

    def test_list_is_scoped_to_its_own_workspace(
        self, admin_client, workspace_with_members, other_workspace, make_workspace_label
    ):
        make_workspace_label("Compliance")
        Label.objects.create(name="Foreign", color="#00FF00", workspace=other_workspace, project=None)

        response = admin_client.get(labels_url(workspace_with_members.slug))

        assert {row["name"] for row in response.data} == {"Compliance"}

    def test_a_member_can_read_but_not_write(self, member_client, workspace_with_members, make_workspace_label):
        """
        Creating a workspace label fans it out into every opted-in project, so
        it is an Admin decision. A member keeps read access — the label list is
        needed to work — but the write is refused.
        """
        make_workspace_label("Compliance")

        assert member_client.get(labels_url(workspace_with_members.slug)).status_code == status.HTTP_200_OK
        assert (
            member_client.post(
                labels_url(workspace_with_members.slug), {"name": "Also fine"}, format="json"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert not Label.objects.filter(workspace=workspace_with_members, name="Also fine").exists()

    def test_a_guest_can_read_but_not_write(self, guest_client, workspace_with_members, make_workspace_label):
        make_workspace_label("Compliance")

        assert guest_client.get(labels_url(workspace_with_members.slug)).status_code == status.HTTP_200_OK
        assert (
            guest_client.post(labels_url(workspace_with_members.slug), {"name": "Nope"}, format="json").status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_delete_removes_the_workspace_label(self, admin_client, workspace_with_members, make_workspace_label):
        label = make_workspace_label("Compliance")

        response = admin_client.delete(label_url(workspace_with_members.slug, label.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Label.objects.filter(id=label.id).exists()


# --- propagation into the projects that opted in -----------------------------


@pytest.mark.contract
class TestPropagationToProjects:
    def test_create_mirrors_into_enabled_projects_only(
        self, admin_client, workspace_with_members, enabled_project, opted_out_project
    ):
        response = admin_client.post(
            labels_url(workspace_with_members.slug), {"name": "Compliance", "color": "#FF0000"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        mirror = mirrored(enabled_project, "Compliance")
        assert mirror is not None, "an opted-in project should have received the label"
        assert mirror.color == "#FF0000"
        assert mirrored(opted_out_project, "Compliance") is None, "an opted-out project must be left alone"

    def test_a_project_with_no_property_row_is_left_alone(self, admin_client, workspace_with_members, second_project):
        """No property row at all is the default state, and means not opted in."""
        admin_client.post(labels_url(workspace_with_members.slug), {"name": "Compliance"}, format="json")

        assert mirrored(second_project, "Compliance") is None

    def test_update_rewrites_the_mirror(
        self, admin_client, workspace_with_members, enabled_project, make_workspace_label
    ):
        label = make_workspace_label("Compliance", color="#FF0000")
        admin_client.patch(label_url(workspace_with_members.slug, label.id), {"color": "#0000FF"}, format="json")

        mirror = mirrored(enabled_project, "Compliance")
        assert mirror is not None
        assert mirror.color == "#0000FF"

    def test_renaming_leaves_the_old_mirror_behind(
        self, admin_client, workspace_with_members, enabled_project, make_workspace_label
    ):
        """
        Mirrors are matched by name, so a rename creates a new one rather than
        moving the old. Pinned as the current contract: the layer never deletes
        a project label it cannot positively identify as its own.
        """
        label = make_workspace_label("Compliance")
        admin_client.post(labels_url(workspace_with_members.slug), {"name": "Compliance"}, format="json")

        admin_client.patch(label_url(workspace_with_members.slug, label.id), {"name": "Risk"}, format="json")

        assert mirrored(enabled_project, "Risk") is not None

    def test_delete_removes_the_mirror(
        self, admin_client, workspace_with_members, enabled_project, make_workspace_label
    ):
        label = make_workspace_label("Compliance")
        admin_client.patch(label_url(workspace_with_members.slug, label.id), {"color": "#0000FF"}, format="json")
        assert mirrored(enabled_project, "Compliance") is not None

        response = admin_client.delete(label_url(workspace_with_members.slug, label.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert mirrored(enabled_project, "Compliance") is None

    def test_delete_does_not_reach_an_opted_out_project(
        self, admin_client, workspace_with_members, opted_out_project, make_workspace_label
    ):
        """A label the project owns in its own right survives a workspace delete."""
        label = make_workspace_label("Compliance")
        Label.objects.create(
            name="Compliance", color="#123456", workspace=workspace_with_members, project=opted_out_project
        )

        admin_client.delete(label_url(workspace_with_members.slug, label.id))

        assert mirrored(opted_out_project, "Compliance") is not None


# --- switching one project on --------------------------------------------------


@pytest.mark.contract
class TestProjectOptIn:
    def test_get_creates_the_property_switched_off(self, admin_client, workspace_with_members, project, project_admin):
        response = admin_client.get(project_label_property_url(workspace_with_members.slug, project.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["is_enabled"] is False
        assert ProjectLabelProperty.objects.filter(project=project).count() == 1

    def test_enabling_back_fills_every_workspace_label(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        make_workspace_label("Compliance", color="#FF0000")
        make_workspace_label("Risk", color="#00FF00")

        response = admin_client.patch(
            project_label_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert mirrored(project, "Compliance").color == "#FF0000"
        assert mirrored(project, "Risk").color == "#00FF00"

    def test_the_back_fill_carries_the_label_hierarchy(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        parent = make_workspace_label("Compliance")
        child = make_workspace_label("Risk")
        child.parent = parent
        child.save()

        admin_client.patch(
            project_label_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert mirrored(project, "Risk").parent_id == mirrored(project, "Compliance").id

    def test_the_back_fill_updates_a_label_the_project_already_had(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        """
        Same name, so it is the same label as far as this layer is concerned: the
        workspace definition wins rather than a duplicate appearing.
        """
        make_workspace_label("Compliance", color="#FF0000")
        Label.objects.create(name="Compliance", color="#999999", workspace=workspace_with_members, project=project)

        admin_client.patch(
            project_label_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert Label.objects.filter(project=project, name="Compliance", deleted_at__isnull=True).count() == 1
        assert mirrored(project, "Compliance").color == "#FF0000"

    def test_switching_off_keeps_what_was_already_pushed_down(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        """Opting out stops future propagation; it does not strip the project."""
        make_workspace_label("Compliance")
        admin_client.patch(
            project_label_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        admin_client.patch(
            project_label_property_url(workspace_with_members.slug, project.id), {"is_enabled": False}, format="json"
        )

        assert mirrored(project, "Compliance") is not None

    def test_re_enabling_does_not_duplicate(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        make_workspace_label("Compliance")
        url = project_label_property_url(workspace_with_members.slug, project.id)

        admin_client.patch(url, {"is_enabled": True}, format="json")
        admin_client.patch(url, {"is_enabled": False}, format="json")
        admin_client.patch(url, {"is_enabled": True}, format="json")

        assert Label.objects.filter(project=project, name="Compliance", deleted_at__isnull=True).count() == 1


# --- which workspace labels a project is tagged with -------------------------


@pytest.mark.contract
class TestProjectLabelMapping:
    def test_post_sets_the_mapping(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        first = make_workspace_label("Compliance")
        second = make_workspace_label("Risk")

        response = admin_client.post(
            project_labels_url(workspace_with_members.slug, project.id),
            {"label_ids": [str(first.id), str(second.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert set(ProjectProjectLabel.objects.filter(project=project).values_list("label_id", flat=True)) == {
            first.id,
            second.id,
        }

    def test_post_replaces_what_was_there(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        first = make_workspace_label("Compliance")
        second = make_workspace_label("Risk")
        url = project_labels_url(workspace_with_members.slug, project.id)
        admin_client.post(url, {"label_ids": [str(first.id)]}, format="json")

        admin_client.post(url, {"label_ids": [str(second.id)]}, format="json")

        assert list(ProjectProjectLabel.objects.filter(project=project).values_list("label_id", flat=True)) == [
            second.id
        ]

    def test_an_empty_payload_clears_the_mapping(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        label = make_workspace_label("Compliance")
        url = project_labels_url(workspace_with_members.slug, project.id)
        admin_client.post(url, {"label_ids": [str(label.id)]}, format="json")

        response = admin_client.post(url, {"label_ids": []}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert not ProjectProjectLabel.objects.filter(project=project).exists()

    def test_a_project_scoped_label_is_not_accepted(self, admin_client, workspace_with_members, project, project_admin):
        """Only workspace-level labels can tag a project; a project's own label cannot."""
        local = Label.objects.create(name="Local", color="#00FF00", workspace=workspace_with_members, project=project)

        response = admin_client.post(
            project_labels_url(workspace_with_members.slug, project.id),
            {"label_ids": [str(local.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert not ProjectProjectLabel.objects.filter(project=project).exists()

    def test_a_label_from_another_workspace_is_not_accepted(
        self, admin_client, workspace_with_members, project, project_admin, other_workspace
    ):
        foreign = Label.objects.create(name="Foreign", color="#00FF00", workspace=other_workspace, project=None)

        response = admin_client.post(
            project_labels_url(workspace_with_members.slug, project.id),
            {"label_ids": [str(foreign.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert not ProjectProjectLabel.objects.filter(project=project).exists()

    def test_posting_the_same_set_twice_is_a_no_op(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        label = make_workspace_label("Compliance")
        url = project_labels_url(workspace_with_members.slug, project.id)

        admin_client.post(url, {"label_ids": [str(label.id)]}, format="json")
        admin_client.post(url, {"label_ids": [str(label.id)]}, format="json")

        assert ProjectProjectLabel.objects.filter(project=project).count() == 1

    def test_get_lists_the_mapping_with_the_label_detail(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_label
    ):
        label = make_workspace_label("Compliance", color="#FF0000")
        url = project_labels_url(workspace_with_members.slug, project.id)
        admin_client.post(url, {"label_ids": [str(label.id)]}, format="json")

        response = admin_client.get(url)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert len(response.data) == 1
        assert response.data[0]["label_detail"]["name"] == "Compliance"
        assert response.data[0]["label_detail"]["color"] == "#FF0000"
