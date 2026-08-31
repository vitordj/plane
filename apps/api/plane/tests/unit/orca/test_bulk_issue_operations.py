# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for the Orca bulk work item operations endpoint.

``/bulk-operation-issues/`` applies one set of properties to many work items at
once — priority, state, dates, labels, assignees, cycle, modules, subscription.
It is a fork-only endpoint and had no tests.

Every write it makes is a raw ``bulk_update`` / ``bulk_create`` / queryset
delete, so the questions that matter are which rows it touches and what it
accepts as a valid reference. The regular work item serializer answers the
second question explicitly — assignees must be active project members, labels
and states must belong to the project — and these tests hold the bulk path to
the same rule, because a caller reaching it can otherwise attach another
tenant's rows.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    Cycle,
    CycleIssue,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueSubscriber,
    Label,
    Module,
    ModuleIssue,
    ProjectMember,
    State,
)

from .conftest import ROLE_ADMIN, ROLE_MEMBER

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def states(project, workspace_with_members):
    def _state(name, group, sequence):
        return State.objects.create(
            name=name,
            group=group,
            sequence=sequence,
            color="#000000",
            project=project,
            workspace=workspace_with_members,
        )

    return {"backlog": _state("Backlog", "backlog", 10), "started": _state("In Progress", "started", 20)}


@pytest.fixture
def make_issue(project, workspace_with_members, admin_user, states):
    def _make(name="Work item", state=None, project_override=None, workspace_override=None):
        return Issue.objects.create(
            name=name,
            project=project_override or project,
            workspace=workspace_override or workspace_with_members,
            state=state or states["backlog"],
            created_by=admin_user,
        )

    return _make


@pytest.fixture
def bulk_url(workspace_with_members, project):
    return f"/api/workspaces/{workspace_with_members.slug}/projects/{project.id}/bulk-operation-issues/"


@pytest.fixture
def foreign_scope(other_workspace, foreign_project, outsider_user):
    """A cycle, module, label and state living in another tenant entirely."""
    state = State.objects.create(
        name="Foreign",
        group="backlog",
        sequence=10,
        color="#000000",
        project=foreign_project,
        workspace=other_workspace,
    )
    return {
        "project": foreign_project,
        "state": state,
        "cycle": Cycle.objects.create(
            name="Foreign cycle", project=foreign_project, workspace=other_workspace, owned_by=outsider_user
        ),
        "module": Module.objects.create(
            name="Foreign module", project=foreign_project, workspace=other_workspace, created_by=outsider_user
        ),
        "label": Label.objects.create(
            name="Foreign label", color="#000000", project=foreign_project, workspace=other_workspace
        ),
        "user": outsider_user,
    }


def label_ids_on(issue):
    return set(IssueLabel.objects.filter(issue=issue, deleted_at__isnull=True).values_list("label_id", flat=True))


def assignee_ids_on(issue):
    return set(IssueAssignee.objects.filter(issue=issue, deleted_at__isnull=True).values_list("assignee_id", flat=True))


def cycle_ids_on(issue):
    return set(CycleIssue.objects.filter(issue=issue, deleted_at__isnull=True).values_list("cycle_id", flat=True))


def module_ids_on(issue):
    return set(ModuleIssue.objects.filter(issue=issue, deleted_at__isnull=True).values_list("module_id", flat=True))


# --- the plain field updates -------------------------------------------------


@pytest.mark.contract
class TestBulkFieldUpdates:
    def test_priority_is_applied_to_every_listed_work_item(self, admin_client, project_admin, bulk_url, make_issue):
        first = make_issue("First")
        second = make_issue("Second")

        response = admin_client.post(
            bulk_url,
            {"issue_ids": [str(first.id), str(second.id)], "properties": {"priority": "urgent"}},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT, getattr(response, "data", None)
        for issue in (first, second):
            issue.refresh_from_db()
            assert issue.priority == "urgent"

    def test_state_is_applied(self, admin_client, project_admin, bulk_url, make_issue, states):
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"state_id": str(states["started"].id)}},
            format="json",
        )

        issue.refresh_from_db()
        assert issue.state_id == states["started"].id

    def test_dates_are_applied(self, admin_client, project_admin, bulk_url, make_issue):
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {
                "issue_ids": [str(issue.id)],
                "properties": {"start_date": "2026-01-01", "target_date": "2026-01-31"},
            },
            format="json",
        )

        issue.refresh_from_db()
        assert str(issue.start_date) == "2026-01-01"
        assert str(issue.target_date) == "2026-01-31"

    def test_an_empty_id_list_is_rejected(self, admin_client, project_admin, bulk_url):
        response = admin_client.post(bulk_url, {"issue_ids": [], "properties": {"priority": "urgent"}}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"error": "Issue IDs are required"}

    def test_a_start_date_after_the_target_date_is_rejected(self, admin_client, project_admin, bulk_url, make_issue):
        issue = make_issue()

        response = admin_client.post(
            bulk_url,
            {
                "issue_ids": [str(issue.id)],
                "properties": {"start_date": "2026-02-01", "target_date": "2026-01-01"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4100
        issue.refresh_from_db()
        assert issue.start_date is None

    def test_a_start_date_past_the_existing_target_date_is_rejected(
        self, admin_client, project_admin, bulk_url, make_issue
    ):
        issue = make_issue()
        issue.target_date = "2026-01-10"
        issue.save()

        response = admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"start_date": "2026-02-01"}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4101

    def test_a_target_date_before_the_existing_start_date_is_rejected(
        self, admin_client, project_admin, bulk_url, make_issue
    ):
        issue = make_issue()
        issue.start_date = "2026-02-01"
        issue.save()

        response = admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"target_date": "2026-01-01"}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4102

    def test_dates_can_be_cleared(self, admin_client, project_admin, bulk_url, make_issue):
        issue = make_issue()
        issue.start_date = "2026-01-01"
        issue.target_date = "2026-01-31"
        issue.save()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"start_date": None, "target_date": None}},
            format="json",
        )

        issue.refresh_from_db()
        assert issue.start_date is None
        assert issue.target_date is None

    def test_subscription_can_be_added_and_removed(self, admin_client, project_admin, bulk_url, make_issue):
        issue = make_issue()

        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"is_subscribed": True}}, format="json"
        )
        assert IssueSubscriber.objects.filter(issue=issue, deleted_at__isnull=True).count() == 1

        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"is_subscribed": False}}, format="json"
        )
        assert not IssueSubscriber.objects.filter(issue=issue, deleted_at__isnull=True).exists()


# --- set semantics for the many-to-many properties ---------------------------


@pytest.mark.contract
class TestBulkSetProperties:
    def test_labels_replace_rather_than_accumulate(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members
    ):
        first = Label.objects.create(name="one", color="#000000", project=project, workspace=workspace_with_members)
        second = Label.objects.create(name="two", color="#000000", project=project, workspace=workspace_with_members)
        issue = make_issue()

        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"label_ids": [str(first.id)]}}, format="json"
        )
        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"label_ids": [str(second.id)]}}, format="json"
        )

        assert label_ids_on(issue) == {second.id}

    def test_an_empty_label_list_clears_them(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members
    ):
        label = Label.objects.create(name="one", color="#000000", project=project, workspace=workspace_with_members)
        issue = make_issue()
        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"label_ids": [str(label.id)]}}, format="json"
        )

        admin_client.post(bulk_url, {"issue_ids": [str(issue.id)], "properties": {"label_ids": []}}, format="json")

        assert label_ids_on(issue) == set()

    def test_assignees_replace_rather_than_accumulate(
        self,
        admin_client,
        project_admin,
        bulk_url,
        make_issue,
        project,
        workspace_with_members,
        plain_user,
        second_user,
    ):
        ProjectMember.objects.create(
            project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
        )
        ProjectMember.objects.create(
            project=project, member=second_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
        )
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"assignee_ids": [str(plain_user.id)]}},
            format="json",
        )
        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"assignee_ids": [str(second_user.id)]}},
            format="json",
        )

        assert assignee_ids_on(issue) == {second_user.id}

    def test_the_cycle_is_replaced(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members, admin_user
    ):
        first = Cycle.objects.create(name="One", project=project, workspace=workspace_with_members, owned_by=admin_user)
        second = Cycle.objects.create(
            name="Two", project=project, workspace=workspace_with_members, owned_by=admin_user
        )
        issue = make_issue()

        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"cycle_id": str(first.id)}}, format="json"
        )
        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"cycle_id": str(second.id)}}, format="json"
        )

        assert cycle_ids_on(issue) == {second.id}

    def test_a_null_cycle_takes_the_work_item_out_of_its_cycle(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members, admin_user
    ):
        cycle = Cycle.objects.create(name="One", project=project, workspace=workspace_with_members, owned_by=admin_user)
        issue = make_issue()
        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"cycle_id": str(cycle.id)}}, format="json"
        )

        admin_client.post(bulk_url, {"issue_ids": [str(issue.id)], "properties": {"cycle_id": None}}, format="json")

        assert cycle_ids_on(issue) == set()

    def test_modules_replace_rather_than_accumulate(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members, admin_user
    ):
        first = Module.objects.create(
            name="One", project=project, workspace=workspace_with_members, created_by=admin_user
        )
        second = Module.objects.create(
            name="Two", project=project, workspace=workspace_with_members, created_by=admin_user
        )
        issue = make_issue()

        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"module_ids": [str(first.id)]}}, format="json"
        )
        admin_client.post(
            bulk_url, {"issue_ids": [str(issue.id)], "properties": {"module_ids": [str(second.id)]}}, format="json"
        )

        assert module_ids_on(issue) == {second.id}

    def test_applying_the_same_set_twice_is_a_no_op(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members
    ):
        label = Label.objects.create(name="one", color="#000000", project=project, workspace=workspace_with_members)
        issue = make_issue()
        payload = {"issue_ids": [str(issue.id)], "properties": {"label_ids": [str(label.id)]}}

        admin_client.post(bulk_url, payload, format="json")
        admin_client.post(bulk_url, payload, format="json")

        assert IssueLabel.objects.filter(issue=issue, deleted_at__isnull=True).count() == 1


# --- what counts as a valid reference ----------------------------------------


@pytest.mark.contract
class TestScoping:
    """
    The regular work item serializer refuses references outside the project.
    The bulk path has to refuse the same ones, or it becomes a way to attach
    another tenant's rows to your own work items.
    """

    def test_a_work_item_from_another_project_is_ignored(
        self, admin_client, project_admin, bulk_url, make_issue, foreign_scope
    ):
        mine = make_issue("Mine")
        theirs = make_issue(
            "Theirs",
            state=foreign_scope["state"],
            project_override=foreign_scope["project"],
            workspace_override=foreign_scope["project"].workspace,
        )

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(mine.id), str(theirs.id)], "properties": {"priority": "urgent"}},
            format="json",
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.priority == "urgent"
        assert theirs.priority == "none", "a work item outside the project must not be written to"

    def test_a_state_from_another_project_is_ignored(
        self, admin_client, project_admin, bulk_url, make_issue, states, foreign_scope
    ):
        issue = make_issue()

        response = admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"state_id": str(foreign_scope["state"].id)}},
            format="json",
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        issue.refresh_from_db()
        assert issue.state_id == states["backlog"].id

    def test_a_cycle_from_another_project_is_refused(
        self, admin_client, project_admin, bulk_url, make_issue, foreign_scope
    ):
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"cycle_id": str(foreign_scope["cycle"].id)}},
            format="json",
        )

        assert cycle_ids_on(issue) == set(), "a cycle from another tenant must not be attached"

    def test_a_module_from_another_project_is_refused(
        self, admin_client, project_admin, bulk_url, make_issue, foreign_scope
    ):
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"module_ids": [str(foreign_scope["module"].id)]}},
            format="json",
        )

        assert module_ids_on(issue) == set(), "a module from another tenant must not be attached"

    def test_a_label_from_another_project_is_refused(
        self, admin_client, project_admin, bulk_url, make_issue, foreign_scope
    ):
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"label_ids": [str(foreign_scope["label"].id)]}},
            format="json",
        )

        assert label_ids_on(issue) == set(), "a label from another tenant must not be attached"

    def test_a_label_from_a_sibling_project_is_refused(
        self, admin_client, project_admin, bulk_url, make_issue, second_project, workspace_with_members
    ):
        """Same workspace, different project — still not this project's label."""
        sibling_label = Label.objects.create(
            name="Sibling", color="#000000", project=second_project, workspace=workspace_with_members
        )
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"label_ids": [str(sibling_label.id)]}},
            format="json",
        )

        assert label_ids_on(issue) == set()

    def test_a_user_who_is_not_a_project_member_is_refused_as_assignee(
        self, admin_client, project_admin, bulk_url, make_issue, plain_user
    ):
        """
        `IssueSerializer.validate` only accepts active project members with a
        role of at least Member; the bulk path must not be a way around it.
        """
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"assignee_ids": [str(plain_user.id)]}},
            format="json",
        )

        assert assignee_ids_on(issue) == set()

    def test_a_guest_project_member_is_refused_as_assignee(
        self, admin_client, project_admin, bulk_url, make_issue, guest_user, project, workspace_with_members
    ):
        from .conftest import ROLE_GUEST

        ProjectMember.objects.create(
            project=project, member=guest_user, workspace=workspace_with_members, role=ROLE_GUEST, is_active=True
        )
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"assignee_ids": [str(guest_user.id)]}},
            format="json",
        )

        assert assignee_ids_on(issue) == set()

    def test_a_member_of_the_project_is_accepted_as_assignee(
        self, admin_client, project_admin, bulk_url, make_issue, plain_user, project, workspace_with_members
    ):
        ProjectMember.objects.create(
            project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
        )
        issue = make_issue()

        admin_client.post(
            bulk_url,
            {"issue_ids": [str(issue.id)], "properties": {"assignee_ids": [str(plain_user.id)]}},
            format="json",
        )

        assert assignee_ids_on(issue) == {plain_user.id}


# --- failing halfway through -------------------------------------------------


@pytest.mark.contract
class TestPartialFailure:
    def test_a_rejected_batch_does_not_strip_the_earlier_labels(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members
    ):
        """
        The date checks run per work item inside the loop, so a work item rejected
        late in the batch can leave the earlier ones already rewritten. Either the
        whole batch applies or none of it does.

        The endpoint walks the work items newest-first, so the one that fails is
        created *first* here to make sure it is processed last.
        """
        old_label = Label.objects.create(
            name="keep", color="#000000", project=project, workspace=workspace_with_members
        )
        new_label = Label.objects.create(name="new", color="#000000", project=project, workspace=workspace_with_members)
        doomed = make_issue("Processed last")
        doomed.start_date = "2026-02-01"
        doomed.save()
        victim = make_issue("Processed first")
        IssueLabel.objects.create(issue=victim, label=old_label, project=project, workspace=workspace_with_members)

        response = admin_client.post(
            bulk_url,
            {
                "issue_ids": [str(victim.id), str(doomed.id)],
                "properties": {"label_ids": [str(new_label.id)], "target_date": "2026-01-01"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4102
        assert label_ids_on(victim) == {old_label.id}, "the earlier work item lost its label to a rejected batch"

    def test_a_rejected_batch_does_not_move_a_cycle(
        self, admin_client, project_admin, bulk_url, make_issue, project, workspace_with_members, admin_user
    ):
        cycle = Cycle.objects.create(name="One", project=project, workspace=workspace_with_members, owned_by=admin_user)
        doomed = make_issue("Processed last")
        doomed.start_date = "2026-02-01"
        doomed.save()
        victim = make_issue("Processed first")

        admin_client.post(
            bulk_url,
            {
                "issue_ids": [str(victim.id), str(doomed.id)],
                "properties": {"cycle_id": str(cycle.id), "target_date": "2026-01-01"},
            },
            format="json",
        )

        assert cycle_ids_on(victim) == set(), "a rejected batch put the earlier work item in a cycle anyway"

    def test_a_rejected_batch_does_not_change_a_priority(self, admin_client, project_admin, bulk_url, make_issue):
        doomed = make_issue("Processed last")
        doomed.start_date = "2026-02-01"
        doomed.save()
        victim = make_issue("Processed first")

        admin_client.post(
            bulk_url,
            {
                "issue_ids": [str(victim.id), str(doomed.id)],
                "properties": {"priority": "urgent", "target_date": "2026-01-01"},
            },
            format="json",
        )

        victim.refresh_from_db()
        assert victim.priority == "none"
