# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The invariants of the routing layer, each named for the one it pins.

The others live where the behaviour they belong to is tested (I2 in
``test_issue_unit_coverage``, I4 to I7 and I10 in ``test_assignment_service``).
This file holds the two that belong to no single operation — one area per work
item, and "assigned" meaning somebody really is on it — plus the kill switch,
which is what makes the whole layer safe to turn off.
"""

import pytest
from rest_framework import status

from plane.app.services.orca import set_responsibility
from plane.db.models import (
    AssignmentDecision,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitCoordinator,
    ProjectMember,
)
from plane.db.models.organizational_unit import RoutingState

from .conftest import (
    ROLE_MEMBER,
    issue_assign_url,
    issue_unit_url,
    unit_url,
    units_url,
    workload_url,
)


def queue_action_url(slug, project_id, issue_id, action):
    return f"/api/orca/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/organizational-unit/{action}/"


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def executor(covered_unit, project, workspace_with_members, add_member, plain_user):
    add_member(covered_unit, plain_user)
    ProjectMember.objects.create(
        project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
    )
    return plain_user


@pytest.mark.unit
@pytest.mark.django_db
class TestI1OneAreaPerWorkItem:
    def test_i1_taking_responsibility_twice_leaves_one_live_link(
        self, covered_unit, second_unit, project, link_project, make_issue
    ):
        link_project(second_unit, project)
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, trigger="internal_api")
        set_responsibility(issue, second_unit, trigger="internal_api")

        links = IssueOrganizationalUnit.objects.filter(issue=issue, deleted_at__isnull=True)
        assert links.count() == 1
        assert links.first().organizational_unit_id == second_unit.id

    def test_i1_clearing_and_setting_again_works(
        self, admin_client, workspace_with_members, covered_unit, project, make_issue
    ):
        """
        The soft-delete trap: the link is kept as history when cleared, so a
        unique index over every row would make set → clear → set fail.
        """
        issue = make_issue(project)
        url = issue_unit_url(workspace_with_members.slug, project.id, issue.id)
        admin_client.post(url, {"organizational_unit_id": str(covered_unit.id)}, format="json")
        admin_client.delete(url)

        response = admin_client.post(url, {"organizational_unit_id": str(covered_unit.id)}, format="json")

        assert response.status_code == status.HTTP_200_OK, response.data


@pytest.mark.unit
@pytest.mark.django_db
class TestI3AssignedMeansSomebodyIsOnIt:
    def test_i3_an_assigned_item_has_an_executor_who_is_an_assignee(self, covered_unit, project, make_issue, executor):
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == executor.id
        assert IssueAssignee.objects.filter(issue=issue, assignee=executor, deleted_at__isnull=True).exists()

    def test_i3_a_queued_item_has_no_executor(self, covered_unit, project, make_issue, executor):
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, trigger="internal_api")

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.primary_executor_id is None


@pytest.mark.unit
@pytest.mark.django_db
class TestTheKillSwitch:
    """
    With the layer off every route answers 404 — not 403, because a workspace
    that has never enabled it should not learn the endpoints exist. This is
    what makes the whole layer safe to switch off in an incident.
    """

    @pytest.fixture(autouse=True)
    def layer_off(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = False

    def test_the_unit_routes_are_gone(self, admin_client, workspace_with_members, unit):
        slug = workspace_with_members.slug

        assert admin_client.get(units_url(slug)).status_code == status.HTTP_404_NOT_FOUND
        assert admin_client.get(unit_url(slug, unit.id)).status_code == status.HTTP_404_NOT_FOUND
        assert admin_client.get(workload_url(slug, unit.id)).status_code == status.HTTP_404_NOT_FOUND

    def test_the_work_item_routes_are_gone(self, admin_client, workspace_with_members, project, make_issue):
        issue = make_issue(project)
        slug = workspace_with_members.slug

        assert admin_client.get(issue_unit_url(slug, project.id, issue.id)).status_code == status.HTTP_404_NOT_FOUND
        assert (
            admin_client.post(issue_assign_url(slug, project.id, issue.id), {}, format="json").status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_the_policy_route_is_gone(self, admin_client, workspace_with_members, unit):
        url = f"/api/orca/workspaces/{workspace_with_members.slug}/organizational-units/{unit.id}/policy/"

        assert admin_client.get(url).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
@pytest.mark.django_db
class TestI10AccessIsNotWrittenByTheQueue:
    """
    I10: only the reconciler writes ``ProjectMember``. The queue is where that
    invariant is most likely to break — a coordinator handing work to somebody
    is one obvious place to "just make sure they have access" — so this is the
    test that pins it: a whole queue emptied through the API, and the access
    table byte-identical afterwards.
    """

    def test_a_coordinator_empties_a_queue_without_granting_anybody_access(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        make_issue,
        add_member,
        grant_manual_access,
        workspace_member_of,
        plain_user,
        second_user,
        admin_user,
    ):
        OrganizationalUnitCoordinator.objects.create(
            organizational_unit=covered_unit,
            workspace_member=workspace_member_of(plain_user),
            workspace=workspace_with_members,
        )
        # Two people who can actually do the work, with the access they would
        # normally have from the area's project link.
        add_member(covered_unit, second_user)
        add_member(covered_unit, admin_user)
        grant_manual_access(project, second_user)
        grant_manual_access(project, admin_user)
        # The coordinator's own access to the project: in the product the
        # reconciler grants it, here it is set up by hand — and it is part of
        # the "before" snapshot, so it is not something the queue granted.
        grant_manual_access(project, plain_user)

        issues = [make_issue(project, name=f"Item {index}") for index in range(30)]
        for issue in issues:
            set_responsibility(issue, covered_unit, trigger="internal_api")

        before = sorted(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active"))

        executors = [second_user, admin_user]
        for index, issue in enumerate(issues):
            response = member_client.post(
                queue_action_url(workspace_with_members.slug, project.id, issue.id, "assign-to"),
                {"primary_executor": str(executors[index % 2].id)},
                format="json",
            )
            assert response.status_code == status.HTTP_200_OK, response.data

        assert not IssueOrganizationalUnit.objects.filter(
            organizational_unit=covered_unit, routing_state=RoutingState.QUEUED
        ).exists()
        after = sorted(ProjectMember.objects.values_list("project_id", "member_id", "role", "is_active"))
        assert after == before


@pytest.mark.unit
@pytest.mark.django_db
class TestEveryActionWritesOneDecision:
    """
    A decision per action, no more and no less. Two decisions for one click
    make the history lie about what happened; none makes "why them?"
    unanswerable, which is the question the whole record exists to answer.
    """

    @pytest.fixture
    def coordinating_executor(
        self, covered_unit, workspace_with_members, add_member, workspace_member_of, plain_user, project
    ):
        """One person who is both in the area and runs it — every action in one test."""
        add_member(covered_unit, plain_user)
        ProjectMember.objects.get_or_create(
            project=project,
            member=plain_user,
            defaults={"workspace": workspace_with_members, "role": ROLE_MEMBER, "is_active": True},
        )
        OrganizationalUnitCoordinator.objects.create(
            organizational_unit=covered_unit,
            workspace_member=workspace_member_of(plain_user),
            workspace=workspace_with_members,
        )
        return plain_user

    def _decisions(self, issue):
        return AssignmentDecision.objects.filter(issue=issue).count()

    def test_claim_assign_return_and_transfer_each_write_exactly_one(
        self,
        member_client,
        workspace_with_members,
        project,
        covered_unit,
        second_unit,
        link_project,
        make_issue,
        coordinating_executor,
        second_user,
        add_member,
        grant_manual_access,
    ):
        link_project(second_unit, project)
        add_member(second_unit, second_user)
        grant_manual_access(project, second_user)
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="self_claim",
            allowed_modes=["self_claim", "manual", "explicit"],
        )

        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")
        slug = workspace_with_members.slug

        for action, payload in (
            ("claim", {}),
            ("return", {"reason": "handing it back"}),
            ("assign-to", {"primary_executor": str(coordinating_executor.id)}),
            ("transfer", {"organizational_unit_id": str(second_unit.id)}),
        ):
            before = self._decisions(issue)

            response = member_client.post(queue_action_url(slug, project.id, issue.id, action), payload, format="json")

            assert response.status_code == status.HTTP_200_OK, (action, response.data)
            assert self._decisions(issue) == before + 1, action
