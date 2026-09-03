# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What happens when two people — or two robots — act at the same instant.

These are the tests that justify the locking, and they are the ones a
refactor is most likely to break without any other test noticing: read a load,
decide, write. Two requests reading the same load hand the same person both
work items, which is exactly what least-loaded exists to prevent.

Every test runs against a real database with real transactions
(``transaction=True``) and one connection per thread, closed at the end of
each: Django's connection is thread-local, and a thread that leaves one open
holds a transaction the next test then waits on.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

from plane.app.services.orca import allocate, claim, reassign, set_responsibility
from plane.db.models import (
    AssignmentDecision,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    ProjectMember,
    WorkspaceMember,
)
from plane.db.models.organizational_unit import RoutingState

from .conftest import ROLE_MEMBER, make_user


def in_own_connection(work):
    """
    @description Run ``work`` with a connection this thread owns and closes.
    A leaked connection keeps its transaction open, and the next test blocks
    on a lock nobody is holding on purpose.
    """

    def _run(*args, **kwargs):
        try:
            return work(*args, **kwargs)
        finally:
            connection.close()

    return _run


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
class TestConcurrentAllocation:
    def test_twenty_allocations_spread_evenly_over_four_people(
        self, workspace_with_members, unit, project, link_project, add_member, make_issue
    ):
        """
        The property least-loaded exists for. Without the area lock the same
        person wins several races and the spread is lopsided.
        """
        link_project(unit, project)
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode="least_loaded",
            allowed_modes=["least_loaded"],
        )
        people = []
        for index in range(4):
            person = make_user(f"executor{index}@plane.so", f"executor{index}", f"Executor {index}")
            WorkspaceMember.objects.create(workspace=workspace_with_members, member=person, role=ROLE_MEMBER)
            add_member(unit, person)
            ProjectMember.objects.create(
                project=project, member=person, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
            )
            people.append(person)

        issues = [make_issue(project, name=f"item {index}") for index in range(20)]
        for issue in issues:
            IssueOrganizationalUnit.objects.create(
                issue=issue, organizational_unit=unit, project=project, workspace=workspace_with_members
            )

        barrier = threading.Barrier(len(issues))

        def take(issue):
            barrier.wait()
            return allocate(issue, unit, requested_mode="least_loaded", trigger="public_api")

        with ThreadPoolExecutor(max_workers=len(issues)) as pool:
            list(pool.map(in_own_connection(take), issues))

        counts = {}
        for link in IssueOrganizationalUnit.objects.filter(organizational_unit=unit):
            counts[link.primary_executor_id] = counts.get(link.primary_executor_id, 0) + 1

        assert sorted(counts.values()) == [5, 5, 5, 5]

    def test_ten_simultaneous_claims_leave_exactly_one_winner(
        self, workspace_with_members, unit, project, link_project, add_member, make_issue
    ):
        link_project(unit, project)
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode="self_claim",
            allowed_modes=["self_claim"],
        )
        people = []
        for index in range(10):
            person = make_user(f"claimant{index}@plane.so", f"claimant{index}", f"Claimant {index}")
            WorkspaceMember.objects.create(workspace=workspace_with_members, member=person, role=ROLE_MEMBER)
            add_member(unit, person)
            ProjectMember.objects.create(
                project=project, member=person, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
            )
            people.append(person)

        issue = make_issue(project)
        set_responsibility(issue, unit, trigger="internal_api")

        barrier = threading.Barrier(len(people))

        def try_claim(person):
            barrier.wait()
            try:
                claim(issue, person)
                return "won"
            except Exception as exc:  # noqa: BLE001 - the point is which one
                return type(exc).__name__

        with ThreadPoolExecutor(max_workers=len(people)) as pool:
            outcomes = list(pool.map(in_own_connection(try_claim), people))

        assert outcomes.count("won") == 1
        assert outcomes.count("AlreadyClaimed") == len(people) - 1
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED

    def test_two_reassignments_from_the_same_view_leave_one_winner(
        self, workspace_with_members, unit, project, link_project, add_member, make_issue, admin_user
    ):
        """
        Two coordinators looking at the same queue page, both acting. The
        If-Match is what stops the second from silently undoing the first.
        """
        link_project(unit, project)
        people = []
        for index in range(3):
            person = make_user(f"coordinated{index}@plane.so", f"coordinated{index}", f"Coordinated {index}")
            WorkspaceMember.objects.create(workspace=workspace_with_members, member=person, role=ROLE_MEMBER)
            add_member(unit, person)
            ProjectMember.objects.create(
                project=project, member=person, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
            )
            people.append(person)

        issue = make_issue(project)
        first = set_responsibility(issue, unit, explicit_executor=people[0], trigger="internal_api")
        seen_decision_id = first.decision.id

        barrier = threading.Barrier(2)

        def try_reassign(person):
            barrier.wait()
            try:
                reassign(issue, person, actor=admin_user, expected_decision_id=seen_decision_id)
                return "won"
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(in_own_connection(try_reassign), people[1:]))

        assert outcomes.count("won") == 1
        assert outcomes.count("DecisionStale") == 1
        # One winner, and the trail says exactly what happened.
        assert AssignmentDecision.objects.filter(issue=issue).count() == 2
