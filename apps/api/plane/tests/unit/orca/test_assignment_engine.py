# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for unit-based work assignment.

The engine must respect Plane's own rule that an assignee is a person who is
an active member of the project, rank by real open workload across the unit's
projects, and never take a work item away from whoever already has it.
"""

import pytest

from plane.app.services.orca import MODE_APPEND, assign_from_unit, candidates_for, workload_snapshot
from plane.app.services.orca.org_unit_reconciler import reconcile_access
from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    Project,
    ProjectMember,
    RoutingState,
    State,
    StateGroup,
    User,
    Workspace,
    WorkspaceMember,
)

ROLE_ADMIN = 20
ROLE_MEMBER = 15


@pytest.fixture
def owner(db):
    user = User.objects.create(email="owner@plane.so", username="owner", first_name="Owner")
    user.set_password("owner@123")
    user.save()
    return user


@pytest.fixture
def workspace(db, owner):
    workspace = Workspace.objects.create(name="Orca", slug="orca-assign", owner=owner)
    WorkspaceMember.objects.create(workspace=workspace, member=owner, role=ROLE_ADMIN)
    return workspace


@pytest.fixture
def project(db, workspace, owner):
    return Project.objects.create(name="Onboarding", identifier="ONB", workspace=workspace, created_by=owner)


@pytest.fixture
def open_state(db, project):
    return State.objects.create(name="Todo", project=project, group=StateGroup.UNSTARTED.value, sequence=1)


@pytest.fixture
def done_state(db, project):
    return State.objects.create(name="Done", project=project, group=StateGroup.COMPLETED.value, sequence=2)


@pytest.fixture
def unit(db, workspace, project):
    unit = OrganizationalUnit.objects.create(workspace=workspace, name="Compliance", slug="compliance")
    OrganizationalUnitProject.objects.create(
        organizational_unit=unit, project=project, workspace=workspace, default_role=ROLE_MEMBER
    )
    return unit


@pytest.fixture
def make_unit_member(db, workspace, unit):
    def _make(name):
        user = User.objects.create(email=f"{name}@plane.so", username=name, first_name=name.title())
        user.set_password("member@123")
        user.save()
        workspace_member = WorkspaceMember.objects.create(workspace=workspace, member=user, role=ROLE_MEMBER)
        OrganizationalUnitMembership.objects.create(
            organizational_unit=unit, workspace_member=workspace_member, workspace=workspace
        )
        return user

    return _make


def make_issue(project, state, owner, name="Validate registration"):
    return Issue.objects.create(
        name=name,
        project=project,
        workspace=project.workspace,
        state=state,
        created_by=owner,
    )


def assign(issue, user):
    """Put ``user`` on the work item as an assignee, and nothing more."""
    return IssueAssignee.objects.create(issue=issue, assignee=user, project=issue.project, workspace=issue.workspace)


def carry(issue, user, unit):
    """
    @description Make ``user`` the area's executor for ``issue``.

    Load is measured on the executor since D0.5, not on ``IssueAssignee``: a
    collaborator left over from an earlier assignment is not the person
    answerable for the work, and counting them kept pushing them down the
    ranking. So a test about the ranking has to hand work out this way —
    ``assign`` alone produces a work item that looks assigned on screen and
    weighs nothing. Tests about "somebody is already on it" still want
    ``assign``.
    @returns The responsibility link.
    """
    assign(issue, user)
    return IssueOrganizationalUnit.objects.create(
        issue=issue,
        organizational_unit=unit,
        project=issue.project,
        workspace=issue.workspace,
        routing_state=RoutingState.ASSIGNED,
        primary_executor=user,
    )


@pytest.mark.unit
class TestAssignmentEngine:
    def test_picks_the_least_loaded_member(self, workspace, project, unit, open_state, owner, make_unit_member):
        """Ranking is by open work, so the busiest member is not chosen."""
        maria = make_unit_member("maria")
        ana = make_unit_member("ana")
        reconcile_access(workspace.id)

        for index in range(3):
            carry(make_issue(project, open_state, owner, f"Existing {index}"), maria, unit)
        carry(make_issue(project, open_state, owner, "Existing ana"), ana, unit)

        issue = make_issue(project, open_state, owner, "New work")
        chosen, reason = assign_from_unit(issue, unit)

        assert reason == "assigned"
        assert chosen.user_id == ana.id
        assert IssueAssignee.objects.filter(issue=issue, assignee=ana).exists()

    def test_completed_work_does_not_count_as_load(
        self, workspace, project, unit, open_state, done_state, owner, make_unit_member
    ):
        """Closed work items are not workload, so a finished backlog frees someone up."""
        maria = make_unit_member("maria")
        ana = make_unit_member("ana")
        reconcile_access(workspace.id)

        for index in range(4):
            carry(make_issue(project, done_state, owner, f"Closed {index}"), maria, unit)
        carry(make_issue(project, open_state, owner, "Open ana"), ana, unit)

        issue = make_issue(project, open_state, owner, "New work")
        chosen, _ = assign_from_unit(issue, unit)

        assert chosen.user_id == maria.id

    def test_non_project_members_are_never_assigned(
        self, workspace, project, unit, open_state, owner, make_unit_member
    ):
        """A unit member without active project access is not an eligible assignee."""
        lucas = make_unit_member("lucas")
        reconcile_access(workspace.id)
        ProjectMember.objects.filter(project=project, member=lucas).update(is_active=False)

        issue = make_issue(project, open_state, owner)
        chosen, reason = assign_from_unit(issue, unit)

        assert chosen is None
        assert reason == "no_eligible_member"

    def test_existing_assignee_is_never_replaced(self, workspace, project, unit, open_state, owner, make_unit_member):
        """The default mode leaves an already-assigned work item alone."""
        make_unit_member("maria")
        reconcile_access(workspace.id)
        issue = make_issue(project, open_state, owner)
        assign(issue, owner)

        chosen, reason = assign_from_unit(issue, unit)

        assert chosen is None
        assert reason == "already_assigned"
        assert list(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True)) == [owner.id]

    def test_append_mode_adds_alongside_existing_assignees(
        self, workspace, project, unit, open_state, owner, make_unit_member
    ):
        """Append adds a unit member without removing whoever is already there."""
        maria = make_unit_member("maria")
        reconcile_access(workspace.id)
        issue = make_issue(project, open_state, owner)
        assign(issue, owner)

        chosen, reason = assign_from_unit(issue, unit, mode=MODE_APPEND)

        assert reason == "assigned"
        assert chosen.user_id == maria.id
        assert IssueAssignee.objects.filter(issue=issue).count() == 2

    def test_ranking_is_deterministic_on_ties(self, workspace, project, unit, open_state, owner, make_unit_member):
        """With equal load and no assignment history, order is stable."""
        make_unit_member("ana")
        make_unit_member("maria")
        reconcile_access(workspace.id)

        first = [candidate.user_id for candidate in candidates_for(unit, project.id)]
        second = [candidate.user_id for candidate in candidates_for(unit, project.id)]

        assert first == second

    def test_workload_snapshot_counts_open_work(
        self, workspace, project, unit, open_state, done_state, owner, make_unit_member
    ):
        maria = make_unit_member("maria")
        make_unit_member("ana")
        reconcile_access(workspace.id)
        assign(make_issue(project, open_state, owner, "Open"), maria)
        assign(make_issue(project, done_state, owner, "Closed"), maria)

        snapshot = {row["display_name"]: row["open_issues"] for row in workload_snapshot(unit)}

        assert snapshot[maria.display_name] == 1
        assert sum(snapshot.values()) == 1
