# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Closing tests for the area's queue (item 2.6).

The per-route tests say each endpoint behaves. These say the whole thing does:
a coordinator can empty a real queue using nothing but the tab's own requests,
the layer's access rules come out of it untouched, and every action leaves
exactly one decision — no more (a double write would make the log lie) and no
fewer (I5 says every change of executor or state is recorded).

The access assertion is the one worth stating plainly: the queue hands work
out, and handing work out must never widen who can see a project. ``I10`` says
no write to ``ProjectMember`` happens outside the reconcilers, and the way to
check it from the outside is to photograph the table before and after.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    AssignmentDecision,
    IssueOrganizationalUnit,
    Notification,
    ProjectMember,
    RoutingState,
)

from .conftest import ROLE_ADMIN, ROLE_MEMBER, issue_action_url, unit_queue_url

# Enough items that one action per item is a real pass over a queue rather
# than a demo, and few enough that the test stays a unit test.
QUEUE_SIZE = 30


def project_member_snapshot(project):
    """@description Every native membership row of a project, as comparable tuples."""
    return sorted(
        ProjectMember.objects.filter(project=project).values_list("member_id", "role", "is_active"),
        key=lambda row: str(row[0]),
    )


@pytest.fixture
def area(
    unit, project, link_project, add_member, add_coordinator, grant_manual_access, plain_user, second_user, admin_user
):
    """An area covering a project, with two executors and a coordinating admin."""
    link_project(unit, project, ROLE_MEMBER)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    add_coordinator(unit, admin_user)
    grant_manual_access(project, admin_user, ROLE_ADMIN)
    return unit


@pytest.fixture
def full_queue(area, project, make_issue):
    """``QUEUE_SIZE`` work items the area owns and nobody is on."""

    def _fill():
        links = []
        for index in range(QUEUE_SIZE):
            issue = make_issue(project, name=f"Item {index}")
            links.append(
                IssueOrganizationalUnit.objects.create(
                    issue=issue,
                    organizational_unit=area,
                    project=project,
                    workspace=project.workspace,
                    routing_state=RoutingState.QUEUED,
                    queue_reason="awaiting_coordinator",
                )
            )
        return links

    return _fill


@pytest.mark.unit
@pytest.mark.django_db
class TestEmptyingAQueue:
    def test_a_coordinator_empties_thirty_items_through_the_tab_alone(
        self, admin_client, workspace_with_members, area, project, full_queue, plain_user, second_user
    ):
        before = project_member_snapshot(project)
        full_queue()
        slug = workspace_with_members.slug

        # Read the queue the way the tab does, then act on what came back —
        # no direct model writes, no service calls.
        listing = admin_client.get(unit_queue_url(slug, area.id))
        assert listing.status_code == status.HTTP_200_OK
        rows = listing.data["items"]
        assert len(rows) == QUEUE_SIZE

        executors = [plain_user, second_user]
        for index, row in enumerate(rows):
            response = admin_client.post(
                issue_action_url(slug, project.id, row["issue_id"], "reassign"),
                {"executor_id": str(executors[index % 2].id)},
                format="json",
            )
            assert response.status_code == status.HTTP_200_OK

        emptied = admin_client.get(unit_queue_url(slug, area.id))
        assert emptied.data["items"] == []

        in_progress = admin_client.get(unit_queue_url(slug, area.id) + "?routing_state=assigned")
        assert len(in_progress.data["items"]) == QUEUE_SIZE
        # Even split, because the coordinator alternated: the point is that the
        # queue is empty and the work is on people, not how it was divided.
        assert {
            str(executors[0].id): sum(
                1 for row in in_progress.data["items"] if str(row["primary_executor"]) == str(executors[0].id)
            ),
            str(executors[1].id): sum(
                1 for row in in_progress.data["items"] if str(row["primary_executor"]) == str(executors[1].id)
            ),
        } == {str(executors[0].id): QUEUE_SIZE // 2, str(executors[1].id): QUEUE_SIZE // 2}

        # I10 from the outside: emptying a queue changed nobody's access.
        assert project_member_snapshot(project) == before

    def test_returning_everything_leaves_the_queue_as_it_started(
        self, admin_client, workspace_with_members, area, project, full_queue, plain_user
    ):
        full_queue()
        slug = workspace_with_members.slug
        rows = admin_client.get(unit_queue_url(slug, area.id)).data["items"]

        for row in rows:
            admin_client.post(
                issue_action_url(slug, project.id, row["issue_id"], "reassign"),
                {"executor_id": str(plain_user.id)},
                format="json",
            )
        for row in rows:
            response = admin_client.post(issue_action_url(slug, project.id, row["issue_id"], "return"))
            assert response.status_code == status.HTTP_200_OK

        back = admin_client.get(unit_queue_url(slug, area.id))
        assert len(back.data["items"]) == QUEUE_SIZE
        assert {row["queue_reason"] for row in back.data["items"]} == {"manually_returned"}


@pytest.mark.unit
@pytest.mark.django_db
class TestOneActionOneDecision:
    """
    Every action of the tab writes exactly one ``AssignmentDecision``.

    @description Two would make the log lie about how often the item moved;
    none would break I5, which is what makes "why does this person have this?"
    answerable a week later.
    """

    @pytest.fixture
    def queued_issue(self, area, project, make_issue):
        def _make():
            issue = make_issue(project)
            IssueOrganizationalUnit.objects.create(
                issue=issue,
                organizational_unit=area,
                project=project,
                workspace=project.workspace,
                routing_state=RoutingState.QUEUED,
                queue_reason="awaiting_coordinator",
            )
            return issue

        return _make

    def decisions_for(self, issue):
        return AssignmentDecision.objects.filter(issue=issue).count()

    def test_claiming(self, member_client, workspace_with_members, project, queued_issue):
        issue = queued_issue()
        member_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "claim"))

        assert self.decisions_for(issue) == 1

    def test_reassigning(self, admin_client, workspace_with_members, project, queued_issue, second_user):
        issue = queued_issue()
        admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "reassign"),
            {"executor_id": str(second_user.id)},
            format="json",
        )

        assert self.decisions_for(issue) == 1

    def test_returning(self, admin_client, workspace_with_members, project, queued_issue, second_user):
        issue = queued_issue()
        admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "reassign"),
            {"executor_id": str(second_user.id)},
            format="json",
        )
        admin_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "return"))

        assert self.decisions_for(issue) == 2

    def test_suspending(self, admin_client, workspace_with_members, project, queued_issue):
        issue = queued_issue()
        admin_client.post(issue_action_url(workspace_with_members.slug, project.id, issue.id, "suspend"))

        assert self.decisions_for(issue) == 1

    def test_a_refused_action_writes_none(
        self, admin_client, workspace_with_members, project, queued_issue, guest_user
    ):
        issue = queued_issue()
        response = admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "reassign"),
            {"executor_id": str(guest_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert self.decisions_for(issue) == 0

    def test_a_transfer_records_the_move_and_the_new_areas_decision(
        self, admin_client, workspace_with_members, project, queued_issue, second_unit, link_project
    ):
        from plane.db.models import IssueResponsibilityEvent

        link_project(second_unit, project, ROLE_MEMBER)
        issue = queued_issue()

        admin_client.post(
            issue_action_url(workspace_with_members.slug, project.id, issue.id, "transfer"),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )

        # I6: the change of area is its own event, and the new area applying
        # its policy is a decision — two records for two different facts.
        assert IssueResponsibilityEvent.objects.filter(issue=issue, to_unit=second_unit).count() == 1
        assert self.decisions_for(issue) == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestTheNegativeMatrix:
    """
    Who is refused, route by route (RFC §10).

    @description The positive cases live in ``test_unit_queue_api.py``; these
    are the ones that would leak. A Guest, somebody outside the workspace and
    a coordinator of a different area get nothing from any of the queue's
    routes.
    """

    @pytest.fixture
    def item(self, area, project, make_issue):
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=area,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.QUEUED,
        )
        return issue

    @pytest.mark.parametrize("action", ["claim", "reassign", "return", "suspend", "transfer"])
    def test_a_guest_of_the_workspace_is_refused_every_action(
        self, guest_client, workspace_with_members, project, item, action, second_user
    ):
        response = guest_client.post(
            issue_action_url(workspace_with_members.slug, project.id, item.id, action),
            {"executor_id": str(second_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize("action", ["claim", "reassign", "return", "suspend", "transfer"])
    def test_somebody_outside_the_workspace_is_refused_every_action(
        self, outsider_client, workspace_with_members, project, item, action
    ):
        response = outsider_client.post(
            issue_action_url(workspace_with_members.slug, project.id, item.id, action), {}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_coordinator_of_another_area_cannot_move_this_ones_work(
        self, workspace_with_members, project, item, second_unit, add_coordinator, second_user
    ):
        from rest_framework.test import APIClient

        add_coordinator(second_unit, second_user)
        client = APIClient()
        client.force_authenticate(user=second_user)

        response = client.post(
            issue_action_url(workspace_with_members.slug, project.id, item.id, "suspend"), {}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_member_of_the_area_cannot_read_the_decision_log(self, member_client, workspace_with_members, area):
        from .conftest import unit_decisions_url

        response = member_client.get(unit_decisions_url(workspace_with_members.slug, area.id))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_member_of_the_area_cannot_appoint_a_coordinator(
        self, member_client, workspace_with_members, area, second_user
    ):
        from .conftest import unit_coordinators_url

        response = member_client.post(
            unit_coordinators_url(workspace_with_members.slug, area.id),
            {"member_id": str(second_user.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_refusal_never_alerts_anybody(self, workspace_with_members, project, item, guest_client):
        # A refused action that still notified a coordinator would be the queue
        # crying wolf at the one person who has to trust it.
        guest_client.post(issue_action_url(workspace_with_members.slug, project.id, item.id, "suspend"))

        assert Notification.objects.count() == 0
