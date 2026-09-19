# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for what the fork does when a project is created.

Two changes, neither tested before:

* **Features start off.** Cycles, Modules, Views and Intake are disabled on a
  new project instead of on, so a project only carries the features someone
  asked for.
* **A new project joins the workspace layers automatically.** Creating one
  writes its ``ProjectStateProperty`` and ``ProjectLabelProperty`` rows, enabled
  exactly when the corresponding workspace switch is on, and runs the same sync
  an existing project gets when it is switched on by hand — otherwise a project
  created after the workspace standardized its states or labels would silently
  sit outside the standard.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    Label,
    Project,
    ProjectLabelProperty,
    ProjectState,
    ProjectStateProperty,
    State,
    WorkspaceProjectLabelSettings,
    WorkspaceProjectStateSettings,
)

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def projects_url(workspace_with_members):
    return f"/api/workspaces/{workspace_with_members.slug}/projects/"


@pytest.fixture
def workspace_states(workspace_with_members):
    """A workspace that has standardized on two states."""
    return [
        ProjectState.objects.create(
            name="Backlog",
            group="backlog",
            color="#60646C",
            sequence=15000,
            default=True,
            workspace=workspace_with_members,
        ),
        ProjectState.objects.create(
            name="Shipped",
            group="completed",
            color="#46A758",
            sequence=45000,
            workspace=workspace_with_members,
        ),
    ]


@pytest.fixture
def workspace_labels(workspace_with_members):
    return [
        Label.objects.create(name="Compliance", color="#FF0000", workspace=workspace_with_members, project=None),
        Label.objects.create(name="Risk", color="#00FF00", workspace=workspace_with_members, project=None),
    ]


@pytest.fixture
def states_layer_on(workspace_with_members):
    return WorkspaceProjectStateSettings.objects.create(workspace=workspace_with_members, is_enabled=True)


@pytest.fixture
def labels_layer_on(workspace_with_members):
    return WorkspaceProjectLabelSettings.objects.create(workspace=workspace_with_members, is_enabled=True)


def create_project(client, url, name="Fresh", identifier="FRSH"):
    return client.post(url, {"name": name, "identifier": identifier}, format="json")


# --- feature defaults --------------------------------------------------------


@pytest.mark.contract
class TestFeatureDefaults:
    def test_a_new_project_starts_with_the_optional_features_off(self, admin_client, projects_url):
        response = create_project(admin_client, projects_url)

        assert response.status_code == status.HTTP_201_CREATED, response.data
        created = Project.objects.get(id=response.data["id"])
        assert created.cycle_view is False
        assert created.module_view is False
        assert created.issue_views_view is False
        assert created.intake_view is False
        assert created.is_time_tracking_enabled is False

    def test_pages_stay_on(self, admin_client, projects_url):
        """Pages is the one feature a project is expected to have from the start."""
        response = create_project(admin_client, projects_url)

        assert Project.objects.get(id=response.data["id"]).page_view is True

    def test_a_feature_asked_for_at_creation_is_honoured(self, admin_client, projects_url):
        response = admin_client.post(
            projects_url, {"name": "With cycles", "identifier": "WCY", "cycle_view": True}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert Project.objects.get(id=response.data["id"]).cycle_view is True


# --- joining the workspace state layer ---------------------------------------


@pytest.mark.contract
class TestJoiningTheStateLayer:
    def test_a_state_property_row_is_created_switched_off(self, admin_client, projects_url, workspace_states):
        response = create_project(admin_client, projects_url)

        prop = ProjectStateProperty.objects.get(project_id=response.data["id"])
        assert prop.is_enabled is False
        assert prop.state_id == workspace_states[0].id, "the row points at the workspace default"

    def test_no_workspace_default_leaves_the_pointer_empty(self, admin_client, projects_url):
        response = create_project(admin_client, projects_url)

        prop = ProjectStateProperty.objects.get(project_id=response.data["id"])
        assert prop.is_enabled is False
        assert prop.state_id is None

    def test_the_property_is_enabled_when_the_workspace_layer_is_on(
        self, admin_client, projects_url, workspace_states, states_layer_on
    ):
        response = create_project(admin_client, projects_url)

        assert ProjectStateProperty.objects.get(project_id=response.data["id"]).is_enabled is True

    def test_the_workspace_states_are_synced_in(self, admin_client, projects_url, workspace_states, states_layer_on):
        """
        A project created after the workspace standardized its states must come
        out standardized, not carrying Plane's own default set.
        """
        response = create_project(admin_client, projects_url)

        names = set(
            State.objects.filter(project_id=response.data["id"], deleted_at__isnull=True).values_list("name", flat=True)
        )
        assert names == {"Backlog", "Shipped"}

    def test_nothing_is_synced_when_the_workspace_layer_is_off(self, admin_client, projects_url, workspace_states):
        response = create_project(admin_client, projects_url)

        names = set(
            State.objects.filter(project_id=response.data["id"], deleted_at__isnull=True).values_list("name", flat=True)
        )
        assert "Shipped" not in names, "the workspace set must not be pushed into a project that did not opt in"
        assert names, "the project should still have Plane's own default states"


# --- joining the workspace label layer ---------------------------------------


@pytest.mark.contract
class TestJoiningTheLabelLayer:
    def test_a_label_property_row_is_created_switched_off(self, admin_client, projects_url, workspace_labels):
        response = create_project(admin_client, projects_url)

        assert ProjectLabelProperty.objects.get(project_id=response.data["id"]).is_enabled is False

    def test_the_property_is_enabled_when_the_workspace_layer_is_on(
        self, admin_client, projects_url, workspace_labels, labels_layer_on
    ):
        response = create_project(admin_client, projects_url)

        assert ProjectLabelProperty.objects.get(project_id=response.data["id"]).is_enabled is True

    def test_the_workspace_labels_are_synced_in(self, admin_client, projects_url, workspace_labels, labels_layer_on):
        response = create_project(admin_client, projects_url)

        labels = Label.objects.filter(project_id=response.data["id"], deleted_at__isnull=True)
        assert {label.name for label in labels} == {"Compliance", "Risk"}
        assert labels.get(name="Compliance").color == "#FF0000"

    def test_nothing_is_synced_when_the_workspace_layer_is_off(self, admin_client, projects_url, workspace_labels):
        response = create_project(admin_client, projects_url)

        assert not Label.objects.filter(project_id=response.data["id"], deleted_at__isnull=True).exists()

    def test_the_two_layers_are_independent(
        self, admin_client, projects_url, workspace_states, workspace_labels, labels_layer_on
    ):
        """Opting the workspace into labels must not drag states along with it."""
        response = create_project(admin_client, projects_url)

        assert ProjectLabelProperty.objects.get(project_id=response.data["id"]).is_enabled is True
        assert ProjectStateProperty.objects.get(project_id=response.data["id"]).is_enabled is False
        assert "Shipped" not in set(
            State.objects.filter(project_id=response.data["id"], deleted_at__isnull=True).values_list("name", flat=True)
        )

    def test_a_second_project_is_set_up_the_same_way(
        self, admin_client, projects_url, workspace_labels, labels_layer_on
    ):
        first = create_project(admin_client, projects_url, name="First", identifier="ONE")
        second = create_project(admin_client, projects_url, name="Second", identifier="TWO")

        for response in (first, second):
            assert response.status_code == status.HTTP_201_CREATED, response.data
            assert ProjectLabelProperty.objects.get(project_id=response.data["id"]).is_enabled is True
            assert Label.objects.filter(project_id=response.data["id"], deleted_at__isnull=True).count() == 2
