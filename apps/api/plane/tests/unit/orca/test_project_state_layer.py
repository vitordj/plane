# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for the Orca workspace-level state layer.

The fork lets a workspace define one canonical set of states (stored as
``ProjectState`` rows) and push it down as the work item states of every project
that opts in. It had no tests, and it is the most destructive thing the fork
does: switching a project on **deletes** the project states that are not in the
workspace set and repoints their work items at the workspace default.

Three pieces:

* ``/project-states/settings/`` — the workspace switch, which also seeds
  ``DEFAULT_PROJECT_STATES`` the first time it is read;
* ``propagate_workspace_state_change`` — each write to a workspace state mirrors
  into the ``State`` rows of the enabled projects, matched by slug;
* ``sync_workspace_states_to_project`` — switching a project on reconciles it
  against the workspace set in both directions.

Which rows are created, updated, deleted and repointed is the whole behaviour,
so all of it runs against a real database.
"""

import pytest
from django.utils.text import slugify
from rest_framework import status

from plane.db.models import (
    Issue,
    ProjectMember,
    ProjectState,
    ProjectStateProperty,
    State,
    WorkspaceProjectStateSettings,
)

from .conftest import ROLE_ADMIN

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    """``ProjectBasePermission`` gates the project-scoped endpoint on a project role."""
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def make_workspace_state(workspace_with_members):
    def _make(name, group="backlog", color="#111111", sequence=15000, default=False):
        return ProjectState.objects.create(
            name=name,
            group=group,
            color=color,
            sequence=sequence,
            default=default,
            workspace=workspace_with_members,
        )

    return _make


@pytest.fixture
def make_project_state(workspace_with_members):
    def _make(project, name, group="backlog", color="#222222", sequence=15000, default=False):
        return State.objects.create(
            name=name,
            group=group,
            color=color,
            sequence=sequence,
            default=default,
            project=project,
            workspace=workspace_with_members,
        )

    return _make


@pytest.fixture
def make_workspace_state_default_backlog(workspace_with_members, project):
    """A workspace default plus its mirror in the project, as the seeded flow leaves things."""
    default = ProjectState.objects.create(
        name="Backlog",
        group="backlog",
        color="#60646C",
        sequence=15000,
        default=True,
        workspace=workspace_with_members,
    )
    State.objects.create(
        name="Backlog",
        group="backlog",
        color="#60646C",
        sequence=15000,
        default=True,
        project=project,
        workspace=workspace_with_members,
    )
    return default


@pytest.fixture
def make_issue_in(workspace_with_members, admin_user):
    def _make(project, state, name="Work item"):
        return Issue.objects.create(
            name=name, project=project, workspace=workspace_with_members, state=state, created_by=admin_user
        )

    return _make


@pytest.fixture
def enabled_project(project):
    ProjectStateProperty.objects.create(project=project, is_enabled=True)
    return project


@pytest.fixture
def opted_out_project(second_project):
    ProjectStateProperty.objects.create(project=second_project, is_enabled=False)
    return second_project


def settings_url(slug):
    return f"/api/orca/workspaces/{slug}/project-states/settings/"


def states_url(slug):
    return f"/api/orca/workspaces/{slug}/project-states/"


def state_url(slug, state_id):
    return f"{states_url(slug)}{state_id}/"


def state_property_url(slug, project_id):
    return f"/api/orca/workspaces/{slug}/projects/{project_id}/project-state/"


def mirrored(project, name):
    """The project work item state mirroring a workspace state, if one is live."""
    return State.objects.filter(project=project, slug=slugify(name), deleted_at__isnull=True).first()


def live_state_names(project):
    return set(State.objects.filter(project=project, deleted_at__isnull=True).values_list("name", flat=True))


# --- the workspace switch ----------------------------------------------------


@pytest.mark.contract
class TestWorkspaceStateSettings:
    def test_first_read_seeds_the_default_states(self, admin_client, workspace_with_members):
        assert not ProjectState.objects.filter(workspace=workspace_with_members).exists()

        response = admin_client.get(settings_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["is_enabled"] is False
        assert set(ProjectState.objects.filter(workspace=workspace_with_members).values_list("name", flat=True)) == {
            "Backlog",
            "Todo",
            "In Progress",
            "Done",
            "Cancelled",
        }
        assert ProjectState.objects.get(workspace=workspace_with_members, default=True).name == "Backlog"

    def test_seeding_happens_once(self, admin_client, workspace_with_members):
        admin_client.get(settings_url(workspace_with_members.slug))
        admin_client.get(settings_url(workspace_with_members.slug))

        assert ProjectState.objects.filter(workspace=workspace_with_members).count() == 5
        assert WorkspaceProjectStateSettings.objects.filter(workspace=workspace_with_members).count() == 1

    def test_an_existing_set_is_not_reseeded(self, admin_client, workspace_with_members, make_workspace_state):
        make_workspace_state("Only one", default=True)

        admin_client.get(settings_url(workspace_with_members.slug))

        assert list(ProjectState.objects.filter(workspace=workspace_with_members).values_list("name", flat=True)) == [
            "Only one"
        ]

    def test_patch_turns_it_on(self, admin_client, workspace_with_members):
        response = admin_client.patch(settings_url(workspace_with_members.slug), {"is_enabled": True}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert WorkspaceProjectStateSettings.objects.get(workspace=workspace_with_members).is_enabled is True

    def test_a_guest_is_refused(self, guest_client, workspace_with_members):
        assert guest_client.get(settings_url(workspace_with_members.slug)).status_code == status.HTTP_403_FORBIDDEN


# --- CRUD on the workspace states --------------------------------------------


@pytest.mark.contract
class TestWorkspaceStateCrud:
    def test_create_stores_a_workspace_state(self, admin_client, workspace_with_members):
        response = admin_client.post(
            states_url(workspace_with_members.slug),
            {"name": "Blocked", "group": "started", "color": "#FF0000", "sequence": 30000},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        state = ProjectState.objects.get(id=response.data["id"])
        assert state.workspace_id == workspace_with_members.id
        assert state.group == "started"

    def test_list_is_scoped_to_its_own_workspace(
        self, admin_client, workspace_with_members, other_workspace, make_workspace_state
    ):
        make_workspace_state("Mine")
        ProjectState.objects.create(name="Theirs", group="backlog", color="#000000", workspace=other_workspace)

        response = admin_client.get(states_url(workspace_with_members.slug))

        assert {row["name"] for row in response.data} == {"Mine"}

    def test_creating_a_default_clears_the_previous_one(
        self, admin_client, workspace_with_members, make_workspace_state
    ):
        """A workspace has exactly one default state; the newest claim wins."""
        old_default = make_workspace_state("Backlog", default=True)

        admin_client.post(
            states_url(workspace_with_members.slug),
            {"name": "Triaging", "group": "unstarted", "color": "#FF0000", "default": True},
            format="json",
        )

        old_default.refresh_from_db()
        assert old_default.default is False
        assert ProjectState.objects.filter(workspace=workspace_with_members, default=True).count() == 1

    def test_promoting_a_state_to_default_clears_the_previous_one(
        self, admin_client, workspace_with_members, make_workspace_state
    ):
        old_default = make_workspace_state("Backlog", default=True)
        other = make_workspace_state("Todo", group="unstarted", sequence=25000)

        admin_client.patch(state_url(workspace_with_members.slug, other.id), {"default": True}, format="json")

        old_default.refresh_from_db()
        other.refresh_from_db()
        assert old_default.default is False
        assert other.default is True

    def test_a_guest_can_read_but_not_write(self, guest_client, workspace_with_members, make_workspace_state):
        make_workspace_state("Backlog", default=True)

        assert guest_client.get(states_url(workspace_with_members.slug)).status_code == status.HTTP_200_OK
        assert (
            guest_client.post(states_url(workspace_with_members.slug), {"name": "Nope"}, format="json").status_code
            == status.HTTP_403_FORBIDDEN
        )

    def test_a_member_can_write(self, member_client, workspace_with_members):
        """
        As with the label layer: ``WorkSpaceAdminPermission`` admits Admin *and*
        Member, so a member can change the workspace's canonical states — which
        propagate into every opted-in project. Pinned, not endorsed.
        """
        response = member_client.post(
            states_url(workspace_with_members.slug),
            {"name": "Blocked", "group": "started", "color": "#FF0000"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data


# --- propagation into the projects that opted in -----------------------------


@pytest.mark.contract
class TestPropagationToProjects:
    def test_create_mirrors_into_enabled_projects_only(
        self, admin_client, workspace_with_members, enabled_project, opted_out_project
    ):
        admin_client.post(
            states_url(workspace_with_members.slug),
            {"name": "Blocked", "group": "started", "color": "#FF0000", "sequence": 30000},
            format="json",
        )

        mirror = mirrored(enabled_project, "Blocked")
        assert mirror is not None, "an opted-in project should have received the state"
        assert mirror.group == "started"
        assert mirror.color == "#FF0000"
        assert mirrored(opted_out_project, "Blocked") is None

    def test_a_project_with_no_property_row_is_left_alone(self, admin_client, workspace_with_members, second_project):
        admin_client.post(
            states_url(workspace_with_members.slug),
            {"name": "Blocked", "group": "started", "color": "#FF0000"},
            format="json",
        )

        assert mirrored(second_project, "Blocked") is None

    def test_update_rewrites_the_mirror(
        self, admin_client, workspace_with_members, enabled_project, make_workspace_state
    ):
        state = make_workspace_state("Blocked", group="started", color="#FF0000", sequence=30000)
        admin_client.patch(state_url(workspace_with_members.slug, state.id), {"color": "#0000FF"}, format="json")

        assert mirrored(enabled_project, "Blocked").color == "#0000FF"

    def test_delete_removes_the_mirror_and_rehomes_its_work_items(
        self,
        admin_client,
        workspace_with_members,
        enabled_project,
        make_workspace_state,
        make_workspace_state_default_backlog,
        make_issue_in,
    ):
        """
        The destructive path: the mirrored state goes, and anything sitting in it
        moves to the mirror of the workspace default rather than being orphaned.
        """
        blocked = make_workspace_state("Blocked", group="started", color="#FF0000", sequence=30000)
        admin_client.patch(state_url(workspace_with_members.slug, blocked.id), {"color": "#FF0001"}, format="json")
        blocked_mirror = mirrored(enabled_project, "Blocked")
        backlog_mirror = mirrored(enabled_project, "Backlog")
        issue = make_issue_in(enabled_project, blocked_mirror)

        response = admin_client.delete(state_url(workspace_with_members.slug, blocked.id))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert mirrored(enabled_project, "Blocked") is None
        issue.refresh_from_db()
        assert issue.state_id == backlog_mirror.id

    def test_delete_does_not_reach_an_opted_out_project(
        self, admin_client, workspace_with_members, opted_out_project, make_workspace_state, make_project_state
    ):
        state = make_workspace_state("Blocked", group="started", sequence=30000)
        make_project_state(opted_out_project, "Blocked", group="started")

        admin_client.delete(state_url(workspace_with_members.slug, state.id))

        assert mirrored(opted_out_project, "Blocked") is not None


# --- switching one project on ------------------------------------------------


@pytest.mark.contract
class TestProjectOptIn:
    def test_get_creates_the_property_switched_off(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_state
    ):
        default = make_workspace_state("Backlog", default=True)

        response = admin_client.get(state_property_url(workspace_with_members.slug, project.id))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["is_enabled"] is False
        prop = ProjectStateProperty.objects.get(project=project)
        assert prop.state_id == default.id, "a new property row points at the workspace default"

    def test_enabling_creates_every_workspace_state_in_the_project(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_state
    ):
        make_workspace_state("Backlog", default=True)
        make_workspace_state("In Progress", group="started", sequence=35000)
        make_workspace_state("Done", group="completed", sequence=45000)

        response = admin_client.patch(
            state_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert live_state_names(project) == {"Backlog", "In Progress", "Done"}
        assert mirrored(project, "In Progress").group == "started"

    def test_enabling_updates_a_state_the_project_already_had(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_state, make_project_state
    ):
        make_workspace_state("Backlog", color="#60646C", default=True)
        existing = make_project_state(project, "Backlog", color="#999999")

        admin_client.patch(
            state_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        existing.refresh_from_db()
        assert existing.color == "#60646C", "the workspace definition wins"
        assert State.objects.filter(project=project, slug="backlog", deleted_at__isnull=True).count() == 1

    def test_enabling_deletes_a_state_the_workspace_does_not_define(
        self,
        admin_client,
        workspace_with_members,
        project,
        project_admin,
        make_workspace_state,
        make_project_state,
        make_issue_in,
    ):
        """
        The point of the layer — and its sharpest edge. A project state outside
        the workspace set is removed, and its work items are moved to the mirror
        of the workspace default rather than left pointing at a dead state.
        """
        make_workspace_state("Backlog", default=True)
        stray = make_project_state(project, "Homegrown", group="unstarted", sequence=25000)
        issue = make_issue_in(project, stray)

        admin_client.patch(
            state_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert live_state_names(project) == {"Backlog"}
        issue.refresh_from_db()
        assert issue.state_id == mirrored(project, "Backlog").id

    def test_enabling_keeps_the_default_state_even_when_unmatched(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_state, make_project_state
    ):
        """
        With no workspace state matching it by slug, the project state chosen as
        the default is still the landing place for rehomed work, so it survives.
        """
        make_workspace_state("Backlog", default=True)
        make_project_state(project, "Homegrown", group="unstarted", sequence=25000)

        admin_client.patch(
            state_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert "Backlog" in live_state_names(project)

    def test_enabling_with_no_workspace_states_changes_nothing(
        self, admin_client, workspace_with_members, project, project_admin, make_project_state
    ):
        """An empty workspace set must not be read as "delete everything"."""
        make_project_state(project, "Homegrown", group="unstarted")

        response = admin_client.patch(
            state_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert live_state_names(project) == {"Homegrown"}

    def test_switching_off_does_not_reconcile(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_state, make_project_state
    ):
        make_workspace_state("Backlog", default=True)
        make_project_state(project, "Homegrown", group="unstarted", sequence=25000)
        url = state_property_url(workspace_with_members.slug, project.id)

        response = admin_client.patch(url, {"is_enabled": False}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert live_state_names(project) == {"Homegrown"}

    def test_re_enabling_is_idempotent(
        self, admin_client, workspace_with_members, project, project_admin, make_workspace_state
    ):
        make_workspace_state("Backlog", default=True)
        url = state_property_url(workspace_with_members.slug, project.id)

        admin_client.patch(url, {"is_enabled": True}, format="json")
        admin_client.patch(url, {"is_enabled": False}, format="json")
        admin_client.patch(url, {"is_enabled": True}, format="json")

        assert State.objects.filter(project=project, slug="backlog", deleted_at__isnull=True).count() == 1

    def test_another_project_is_untouched(
        self,
        admin_client,
        workspace_with_members,
        project,
        project_admin,
        second_project,
        make_workspace_state,
        make_project_state,
    ):
        make_workspace_state("Backlog", default=True)
        make_project_state(second_project, "Homegrown", group="unstarted")

        admin_client.patch(
            state_property_url(workspace_with_members.slug, project.id), {"is_enabled": True}, format="json"
        )

        assert live_state_names(second_project) == {"Homegrown"}
