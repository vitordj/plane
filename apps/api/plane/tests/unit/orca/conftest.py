# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Shared fixtures for the Orca organizational-layer tests.

Everything here builds real rows in a real database: the layer's whole job is
to materialize native ``ProjectMember`` records, so a mocked ORM would test
nothing that matters.
"""

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    OrganizationalDirectoryConnection,
    OrganizationalDirectoryGroupMembership,
    OrganizationalDirectoryIdentity,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    Project,
    ProjectMember,
    State,
    StateGroup,
    User,
    Workspace,
    WorkspaceMember,
)

ROLE_ADMIN = 20
ROLE_MEMBER = 15
ROLE_GUEST = 5


@pytest.fixture(autouse=True)
def run_celery_inline(settings):
    """
    Execute queued tasks inline instead of reaching for a broker.

    @description Plane's soft delete fans out to ``soft_delete_related_objects``
    through Celery, so without this the delete endpoints would only be testable
    against a live broker. Running eagerly also means the cascade actually
    happens, which is what the ledger assertions need to observe.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


def make_user(email, username, first_name):
    user = User.objects.create(email=email, username=username, first_name=first_name)
    user.set_password("orca@123")
    user.save()
    return user


@pytest.fixture
def admin_user(db):
    return make_user("admin@plane.so", "admin", "Admin")


@pytest.fixture
def plain_user(db):
    return make_user("plain@plane.so", "plain", "Plain")


@pytest.fixture
def second_user(db):
    return make_user("second@plane.so", "second", "Second")


@pytest.fixture
def guest_user(db):
    return make_user("guest@plane.so", "guest", "Guest")


@pytest.fixture
def outsider_user(db):
    return make_user("outsider@plane.so", "outsider", "Outsider")


@pytest.fixture
def workspace_with_members(db, admin_user, plain_user, second_user, guest_user):
    """A workspace holding one admin, two members and one guest."""
    workspace = Workspace.objects.create(name="Orca", slug="orca-api", owner=admin_user)
    WorkspaceMember.objects.create(workspace=workspace, member=admin_user, role=ROLE_ADMIN)
    WorkspaceMember.objects.create(workspace=workspace, member=plain_user, role=ROLE_MEMBER)
    WorkspaceMember.objects.create(workspace=workspace, member=second_user, role=ROLE_MEMBER)
    WorkspaceMember.objects.create(workspace=workspace, member=guest_user, role=ROLE_GUEST)
    return workspace


@pytest.fixture
def other_workspace(db, outsider_user):
    """A second workspace, used to prove tenant isolation."""
    workspace = Workspace.objects.create(name="Other", slug="other-ws", owner=outsider_user)
    WorkspaceMember.objects.create(workspace=workspace, member=outsider_user, role=ROLE_ADMIN)
    return workspace


@pytest.fixture
def project(db, workspace_with_members, admin_user):
    return Project.objects.create(
        name="Onboarding", identifier="ONB", workspace=workspace_with_members, created_by=admin_user
    )


@pytest.fixture
def second_project(db, workspace_with_members, admin_user):
    return Project.objects.create(
        name="Billing", identifier="BIL", workspace=workspace_with_members, created_by=admin_user
    )


@pytest.fixture
def foreign_project(db, other_workspace, outsider_user):
    return Project.objects.create(name="Foreign", identifier="FGN", workspace=other_workspace, created_by=outsider_user)


@pytest.fixture
def unit(db, workspace_with_members):
    return OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Compliance", slug="compliance")


@pytest.fixture
def second_unit(db, workspace_with_members):
    return OrganizationalUnit.objects.create(workspace=workspace_with_members, name="Legal", slug="legal")


@pytest.fixture
def foreign_unit(db, other_workspace):
    return OrganizationalUnit.objects.create(workspace=other_workspace, name="Foreign", slug="foreign")


@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def member_client(plain_user):
    client = APIClient()
    client.force_authenticate(user=plain_user)
    return client


@pytest.fixture
def guest_client(guest_user):
    client = APIClient()
    client.force_authenticate(user=guest_user)
    return client


@pytest.fixture
def outsider_client(outsider_user):
    client = APIClient()
    client.force_authenticate(user=outsider_user)
    return client


# --- helpers -----------------------------------------------------------------


@pytest.fixture
def workspace_member_of(workspace_with_members):
    """Resolve a user to their ``WorkspaceMember`` row in the main workspace."""

    def _resolve(user):
        return WorkspaceMember.objects.get(workspace=workspace_with_members, member=user)

    return _resolve


@pytest.fixture
def link_project(workspace_with_members):
    """Link a unit to a project at a given inherited role."""

    def _link(unit, project, role=ROLE_MEMBER):
        return OrganizationalUnitProject.objects.create(
            organizational_unit=unit, project=project, workspace=workspace_with_members, default_role=role
        )

    return _link


@pytest.fixture
def add_member(workspace_with_members, workspace_member_of):
    """Put a user in a unit without going through the API."""

    def _add(unit, user, role="member"):
        return OrganizationalUnitMembership.objects.create(
            organizational_unit=unit,
            workspace_member=workspace_member_of(user),
            workspace=workspace_with_members,
            role=role,
        )

    return _add


@pytest.fixture
def grant_manual_access(workspace_with_members):
    """Create a native ProjectMember by hand, as an admin would in the UI."""

    def _grant(project, user, role=ROLE_MEMBER):
        return ProjectMember.objects.create(
            project=project, member=user, workspace=workspace_with_members, role=role, is_active=True
        )

    return _grant


@pytest.fixture
def make_issue(workspace_with_members, admin_user):
    """Create a work item, optionally in a state group (for workload counts)."""

    def _make(project, name="Work item", state_group=StateGroup.UNSTARTED.value):
        state, _ = State.objects.get_or_create(
            project=project,
            group=state_group,
            defaults={"name": f"{state_group}-state"},
        )
        return Issue.objects.create(
            name=name,
            project=project,
            workspace=workspace_with_members,
            state=state,
            created_by=admin_user,
        )

    return _make


# --- url builders ------------------------------------------------------------


def units_url(slug):
    return f"/api/orca/workspaces/{slug}/organizational-units/"


def unit_url(slug, unit_id):
    return f"{units_url(slug)}{unit_id}/"


def members_url(slug, unit_id):
    return f"{units_url(slug)}{unit_id}/members/"


def member_url(slug, unit_id, pk):
    return f"{members_url(slug, unit_id)}{pk}/"


def unit_projects_url(slug, unit_id):
    return f"{units_url(slug)}{unit_id}/projects/"


def unit_project_url(slug, unit_id, pk):
    return f"{unit_projects_url(slug, unit_id)}{pk}/"


def effective_access_url(slug, unit_id):
    return f"{units_url(slug)}{unit_id}/effective-access/"


def workload_url(slug, unit_id):
    return f"{units_url(slug)}{unit_id}/workload/"


def issue_unit_url(slug, project_id, issue_id):
    return f"/api/orca/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/organizational-unit/"


def issue_assign_url(slug, project_id, issue_id):
    return f"/api/orca/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/organizational-unit-assign/"


# --- directory (SCIM) fixtures -----------------------------------------------


@pytest.fixture
def directory_connection(db, workspace_with_members):
    """An enabled directory connection, with a token the tests know."""
    connection = OrganizationalDirectoryConnection.objects.create(
        workspace=workspace_with_members, tenant_id="test-tenant"
    )
    token = connection.issue_token()
    connection.is_enabled = True
    connection.save()
    # The plain token is only ever returned once, so hand it to the test here.
    connection.plain_token = token
    return connection


@pytest.fixture
def scim_client(directory_connection):
    """An API client that authenticates the way Entra does."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {directory_connection.plain_token}")
    return client


@pytest.fixture
def make_identity(workspace_with_members):
    """Create a mirrored directory identity, optionally already linked."""

    def _make(user_name, email=None, display_name="", external_id="", is_active=True):
        return OrganizationalDirectoryIdentity.objects.create(
            workspace=workspace_with_members,
            user_name=user_name,
            email=email if email is not None else user_name,
            display_name=display_name or user_name,
            external_id=external_id,
            is_active=is_active,
        )

    return _make


@pytest.fixture
def put_in_group(workspace_with_members):
    """Record the directory's assertion that an identity belongs to a unit."""

    def _put(unit, identity):
        return OrganizationalDirectoryGroupMembership.objects.create(
            organizational_unit=unit, identity=identity, workspace=workspace_with_members
        )

    return _put


@pytest.fixture
def bound_unit(unit):
    """A unit already bound to a directory group."""
    unit.external_id = "entra-group-compliance"
    unit.sync_source = "scim"
    unit.save()
    return unit


# --- SCIM url builders --------------------------------------------------------


def scim_base(slug):
    return f"/api/orca/scim/v2/workspaces/{slug}"


def scim_users_url(slug):
    return f"{scim_base(slug)}/Users"


def scim_user_url(slug, identity_id):
    return f"{scim_users_url(slug)}/{identity_id}"


def scim_groups_url(slug):
    return f"{scim_base(slug)}/Groups"


def scim_group_url(slug, unit_id):
    return f"{scim_groups_url(slug)}/{unit_id}"


def directory_url(slug):
    return f"/api/orca/workspaces/{slug}/directory/"


def directory_token_url(slug):
    return f"{directory_url(slug)}token/"


def directory_resync_url(slug):
    return f"{directory_url(slug)}resync/"


def directory_unresolved_url(slug):
    return f"{directory_url(slug)}unresolved/"
