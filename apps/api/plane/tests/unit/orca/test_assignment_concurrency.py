# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Two people, one item, at the same time.

The rules the rest of the suite checks are all sequential, and every one of
them survives a race by accident. These do not:

* twenty items allocated at once must not all go to whoever was least loaded
  when the first request arrived. The advisory lock per area is what makes the
  second request read a load that already includes the first;
* ten people claiming the same queued item must produce one assignment and
  nine refusals, not ten writes over each other. The row lock is the whole
  mechanism, and a test that runs the claims one after another would pass with
  no lock at all.

These need real transactions, so they run under ``transaction=True`` with their
own fixtures, and each thread closes its connection when it is done — a thread
that leaves one open holds a Postgres backend for the rest of the session.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from plane.app.services.orca import AlreadyClaimed, allocate, claim
from plane.db.models import (
    AssignmentMode,
    Issue,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    OrganizationalUnitAssignmentPolicy,
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
def world(transactional_db):
    """
    A workspace with one area covering one project, four people in both, and a
    policy that hands work to the least loaded.
    """
    owner = User.objects.create(email="owner@plane.so", username="conc-owner", first_name="Owner")
    workspace = Workspace.objects.create(name="Orca", slug="orca-concurrency", owner=owner)
    WorkspaceMember.objects.create(workspace=workspace, member=owner, role=ROLE_ADMIN)
    project = Project.objects.create(name="Queue", identifier="QUE", workspace=workspace, created_by=owner)
    State.objects.create(
        name="Todo", project=project, workspace=workspace, group=StateGroup.UNSTARTED.value, sequence=1, default=True
    )
    unit = OrganizationalUnit.objects.create(workspace=workspace, name="Support", slug="support")
    unit_project = OrganizationalUnitProject.objects.create(
        organizational_unit=unit, project=project, workspace=workspace, default_role=ROLE_MEMBER
    )
    OrganizationalUnitAssignmentPolicy.objects.create(
        organizational_unit=unit,
        workspace=workspace,
        unit_project=unit_project,
        default_mode=AssignmentMode.LEAST_LOADED,
        allowed_modes=[AssignmentMode.LEAST_LOADED.value, AssignmentMode.SELF_CLAIM.value],
    )

    members = []
    for index in range(4):
        user = User.objects.create(
            email=f"member{index}@plane.so", username=f"conc-member{index}", first_name=f"Member {index}"
        )
        workspace_member = WorkspaceMember.objects.create(workspace=workspace, member=user, role=ROLE_MEMBER)
        OrganizationalUnitMembership.objects.create(
            organizational_unit=unit, workspace_member=workspace_member, workspace=workspace
        )
        ProjectMember.objects.create(
            project=project, member=user, workspace=workspace, role=ROLE_MEMBER, is_active=True
        )
        members.append(user)

    state = State.objects.get(project=project)
    return {
        "workspace": workspace,
        "project": project,
        "unit": unit,
        "members": members,
        "state": state,
        "owner": owner,
    }


def make_linked_issue(world, name):
    issue = Issue.objects.create(
        name=name,
        project=world["project"],
        workspace=world["workspace"],
        state=world["state"],
        created_by=world["owner"],
    )
    IssueOrganizationalUnit.objects.create(
        issue=issue,
        organizational_unit=world["unit"],
        project=world["project"],
        workspace=world["workspace"],
    )
    return issue


def in_thread(work):
    """@description Run ``work`` and hand the thread's connection back. @returns Whatever work returns."""
    try:
        return work()
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
def test_simultaneous_allocations_spread_evenly(world):
    """
    Twenty items, four people, all at once. Without the advisory lock the
    requests all read the same "least loaded" and pile onto one person.
    """
    issues = [make_linked_issue(world, f"Item {index}") for index in range(20)]

    def allocate_one(issue):
        return in_thread(lambda: allocate(issue, world["unit"]).chosen_user_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        chosen = list(pool.map(allocate_one, issues))

    counts = {member.id: chosen.count(member.id) for member in world["members"]}
    assert sum(counts.values()) == 20
    assert sorted(counts.values()) == [5, 5, 5, 5], counts


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
def test_only_one_claim_wins(world):
    """Ten people reaching for the same item: one assignment, nine refusals."""
    issue = make_linked_issue(world, "Contested")
    link = IssueOrganizationalUnit.objects.get(issue=issue)
    link.routing_state = RoutingState.QUEUED
    link.save()
    claimants = [world["members"][index % len(world["members"])] for index in range(10)]

    def claim_one(user):
        def work():
            try:
                claim(issue, user)
                return "won"
            except AlreadyClaimed:
                return "lost"

        return in_thread(work)

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(claim_one, claimants))

    assert outcomes.count("won") == 1, outcomes
    assert outcomes.count("lost") == 9, outcomes

    link.refresh_from_db()
    assert link.routing_state == RoutingState.ASSIGNED
    assert link.primary_executor_id is not None


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
def test_a_contested_claim_names_the_winner(world):
    """The loser should not have to reload to find out who got it."""
    issue = make_linked_issue(world, "Contested too")
    first, second = world["members"][0], world["members"][1]
    claim(issue, first)

    with pytest.raises(AlreadyClaimed) as raised:
        claim(issue, second)

    assert raised.value.payload["primary_executor_id"] == str(first.id)


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
def test_an_allocation_and_a_claim_leave_one_executor(world):
    """
    The third race in RFC §10: a coordinator re-running the allocation while
    somebody claims the same item. Both paths take the row lock, so one of them
    waits and then sees the item the other left behind — either the claimer is
    refused, or the allocation lands on the person who already has it. What
    must never happen is two executors, or an executor nobody assigned.
    """
    issue = make_linked_issue(world, "Contested by two paths")
    claimant = world["members"][0]

    def allocate_it():
        return in_thread(lambda: allocate(issue, world["unit"]).chosen_user_id)

    def claim_it():
        def work():
            try:
                return claim(issue, claimant).chosen_user_id
            except AlreadyClaimed:
                return None

        return in_thread(work)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(allocate_it), pool.submit(claim_it)]
        outcomes = [future.result() for future in results]

    link = IssueOrganizationalUnit.objects.get(issue=issue)
    assert link.routing_state == RoutingState.ASSIGNED
    assert link.primary_executor_id is not None
    # Whoever the link ended up on is one of the two the threads chose, and the
    # item carries exactly one executor.
    assert link.primary_executor_id in {outcome for outcome in outcomes if outcome is not None}
