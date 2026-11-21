# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Review gaps closed after the R2 merge (batch A): who may change an item's area
or executor (D2), the public create-or-find bypass, executor return parity,
process-instance progress leaks (R2.03) and SCIM replace emptying a group
(R2.04).

Rule under test (D2): any project Member may give an area to an item that has
none; changing or removing the area, or taking the item from / replacing its
executor, needs a coordinator of the item's CURRENT area or a workspace Admin.
The executor may return their own item to the queue.
"""

import pytest

from plane.app.services.orca import set_responsibility
from plane.db.models import (
    AutomationOperation,
    ExternalWorkItemBinding,
    IssueOrganizationalUnit,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ProjectMember,
    RoutingState,
    State,
    StateGroup,
)

from .conftest import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_MEMBER,
    issue_assign_url,
    issue_unit_url,
    public_process_instance_url,
    public_reassign_url,
    public_transfer_url,
    public_work_items_url,
    scim_group_url,
)

SCHEMA_PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SOURCE = "espo-onboarding"


@pytest.fixture
def setup(
    project, unit, second_unit, link_project, add_member, grant_manual_access, plain_user, second_user, admin_user
):
    link_project(unit, project, ROLE_MEMBER)
    link_project(second_unit, project, ROLE_MEMBER)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user, ROLE_MEMBER)
    grant_manual_access(project, admin_user, ROLE_MEMBER)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    return project, unit, second_unit


@pytest.fixture
def assigned(setup, make_issue, second_user):
    project, unit, _ = setup
    issue = make_issue(project)
    result = set_responsibility(issue, unit, explicit_executor=second_user)
    return issue, result.decision


def link_of(issue):
    return IssueOrganizationalUnit.objects.get(issue=issue)


def bind(issue, workspace, external_id="cust-1"):
    return ExternalWorkItemBinding.objects.create(
        workspace=workspace, external_source=SOURCE, external_id=external_id, issue=issue
    )


def create_body(unit_slug, external_id="cust-1"):
    return {
        "external": {"source": SOURCE, "id": external_id},
        "work_item": {"name": "Validate documents"},
        "responsibility": {"unit": unit_slug, "assignment": {"mode": "default"}},
    }


def post_create(client, project, payload, key="k-1"):
    return client.post(
        public_work_items_url(project.workspace.slug, project.id), payload, format="json", HTTP_IDEMPOTENCY_KEY=key
    )


@pytest.mark.unit
class TestPublicCreateOrFindGate:
    def test_member_token_cannot_move_bound_item_to_another_area(
        self, workspace_with_members, setup, assigned, plain_user, second_user, token_client
    ):
        project, unit, second_unit = setup
        issue, _ = assigned
        bind(issue, workspace_with_members)
        operations_before = AutomationOperation.objects.count()

        response = post_create(token_client(plain_user), project, create_body(second_unit.slug))

        assert response.status_code == 403
        assert response.data["error_message"] == "ORG_NOT_UNIT_COORDINATOR"
        assert AutomationOperation.objects.count() == operations_before  # no key spent
        link = link_of(issue)
        assert link.organizational_unit_id == unit.id
        assert link.primary_executor_id == second_user.id

    def test_same_area_find_stays_a_no_op_for_a_member(
        self, workspace_with_members, setup, assigned, plain_user, second_user, token_client
    ):
        project, unit, _ = setup
        issue, _ = assigned
        bind(issue, workspace_with_members)

        response = post_create(token_client(plain_user), project, create_body(unit.slug))

        assert response.status_code == 201, response.data
        link = link_of(issue)
        assert link.organizational_unit_id == unit.id
        assert link.primary_executor_id == second_user.id

    def test_coordinator_of_current_area_may_move_it(
        self, workspace_with_members, setup, assigned, plain_user, add_coordinator, token_client
    ):
        project, unit, second_unit = setup
        issue, _ = assigned
        bind(issue, workspace_with_members)
        add_coordinator(unit, plain_user)

        response = post_create(token_client(plain_user), project, create_body(second_unit.slug))

        assert response.status_code == 201, response.data
        assert link_of(issue).organizational_unit_id == second_unit.id

    def test_member_may_place_bound_item_that_has_no_area_yet(
        self, workspace_with_members, setup, make_issue, plain_user, token_client
    ):
        project, _, second_unit = setup
        issue = make_issue(project)
        bind(issue, workspace_with_members)

        response = post_create(token_client(plain_user), project, create_body(second_unit.slug))

        assert response.status_code == 201, response.data
        assert link_of(issue).organizational_unit_id == second_unit.id

    def test_member_may_create_a_new_item(self, setup, plain_user, token_client):
        project, unit, _ = setup
        response = post_create(token_client(plain_user), project, create_body(unit.slug, "brand-new"))
        assert response.status_code == 201, response.data


@pytest.mark.unit
class TestPublicReturnToQueueByExecutor:
    def test_executor_may_return_own_item(self, setup, assigned, second_user, token_client):
        project, _, _ = setup
        issue, decision = assigned

        response = token_client(second_user).post(
            public_reassign_url(project.workspace.slug, project.id, issue.id),
            {"return_to_queue": True},
            format="json",
            HTTP_IDEMPOTENCY_KEY="ret-1",
            HTTP_IF_MATCH=str(decision.id),
        )

        assert response.status_code == 200, response.data
        assert link_of(issue).routing_state == RoutingState.QUEUED

    def test_executor_may_not_reassign_or_transfer(self, setup, assigned, second_user, token_client):
        project, _, second_unit = setup
        issue, decision = assigned
        client = token_client(second_user)

        reassign = client.post(
            public_reassign_url(project.workspace.slug, project.id, issue.id),
            {"primary_executor": str(second_user.id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY="ex-1",
            HTTP_IF_MATCH=str(decision.id),
        )
        transfer = client.post(
            public_transfer_url(project.workspace.slug, project.id, issue.id),
            {"unit": second_unit.slug},
            format="json",
            HTTP_IDEMPOTENCY_KEY="ex-2",
        )

        assert reassign.status_code == 403
        assert transfer.status_code == 403
        assert link_of(issue).routing_state == RoutingState.ASSIGNED

    def test_other_member_may_not_return(self, setup, assigned, plain_user, token_client):
        project, _, _ = setup
        issue, decision = assigned

        response = token_client(plain_user).post(
            public_reassign_url(project.workspace.slug, project.id, issue.id),
            {"return_to_queue": True},
            format="json",
            HTTP_IDEMPOTENCY_KEY="ret-2",
            HTTP_IF_MATCH=str(decision.id),
        )

        assert response.status_code == 403
        assert link_of(issue).routing_state == RoutingState.ASSIGNED


@pytest.mark.unit
class TestAppRoutesAgreeWithD2:
    def test_member_may_assign_by_area_an_item_with_no_area(
        self, member_client, workspace_with_members, setup, make_issue, unit
    ):
        project, _, _ = setup
        issue = make_issue(project)

        response = member_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 200, response.data
        assert link_of(issue).organizational_unit_id == unit.id

    @pytest.mark.parametrize("state", [RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED])
    def test_member_may_run_policy_on_unowned_item_in_its_area(
        self, member_client, workspace_with_members, setup, make_issue, state
    ):
        project, unit, _ = setup
        issue = make_issue(project)
        set_responsibility(issue, unit, requested_mode="manual")
        IssueOrganizationalUnit.objects.filter(issue=issue).update(routing_state=state, primary_executor=None)

        response = member_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id), {}, format="json"
        )

        assert response.status_code == 200, response.data

    def test_member_may_not_move_item_in_allocation_failed_to_another_area(
        self, member_client, workspace_with_members, setup, make_issue
    ):
        project, unit, second_unit = setup
        issue = make_issue(project)
        set_responsibility(issue, unit, requested_mode="manual")
        IssueOrganizationalUnit.objects.filter(issue=issue).update(
            routing_state=RoutingState.ALLOCATION_FAILED, primary_executor=None
        )

        response = member_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(second_unit.id)},
            format="json",
        )

        assert response.status_code == 403
        assert link_of(issue).organizational_unit_id == unit.id


@pytest.mark.unit
class TestEdgeActors:
    def post_unit(self, client, slug, project, issue, target):
        return client.post(
            issue_unit_url(slug, project.id, issue.id), {"organizational_unit_id": str(target.id)}, format="json"
        )

    def test_workspace_admin_may_transfer(self, admin_client, workspace_with_members, setup, assigned):
        project, _, second_unit = setup
        issue, _ = assigned
        response = self.post_unit(admin_client, workspace_with_members.slug, project, issue, second_unit)
        assert response.status_code == 200
        assert link_of(issue).organizational_unit_id == second_unit.id

    def test_project_admin_who_is_not_workspace_admin_is_refused(
        self, member_client, workspace_with_members, setup, assigned, grant_manual_access, plain_user
    ):
        project, unit, second_unit = setup
        issue, _ = assigned
        ProjectMember.objects.filter(project=project, member=plain_user).update(role=ROLE_ADMIN)
        response = self.post_unit(member_client, workspace_with_members.slug, project, issue, second_unit)
        assert response.status_code == 403
        assert link_of(issue).organizational_unit_id == unit.id

    def test_guest_is_refused(
        self, guest_client, workspace_with_members, setup, assigned, grant_manual_access, guest_user
    ):
        project, unit, second_unit = setup
        issue, _ = assigned
        grant_manual_access(project, guest_user, ROLE_GUEST)
        response = self.post_unit(guest_client, workspace_with_members.slug, project, issue, second_unit)
        assert response.status_code == 403
        assert link_of(issue).organizational_unit_id == unit.id

    def test_coordinator_who_is_not_a_member_of_the_area_may_move_it(
        self, member_client, workspace_with_members, setup, make_issue, add_coordinator, plain_user, second_user
    ):
        project, unit, second_unit = setup
        issue = make_issue(project)
        set_responsibility(issue, second_unit, requested_mode="manual")
        add_coordinator(second_unit, plain_user)
        response = self.post_unit(member_client, workspace_with_members.slug, project, issue, unit)
        assert response.status_code == 200
        assert link_of(issue).organizational_unit_id == unit.id

    def test_coordinator_of_target_area_only_is_refused(
        self, member_client, workspace_with_members, setup, assigned, add_coordinator, plain_user
    ):
        project, unit, second_unit = setup
        issue, _ = assigned
        add_coordinator(second_unit, plain_user)
        response = self.post_unit(member_client, workspace_with_members.slug, project, issue, second_unit)
        assert response.status_code == 403
        assert link_of(issue).organizational_unit_id == unit.id

    def test_repost_of_same_area_without_mode_is_a_noop_for_a_member(
        self, member_client, workspace_with_members, setup, assigned, second_user
    ):
        project, unit, _ = setup
        issue, _ = assigned
        response = self.post_unit(member_client, workspace_with_members.slug, project, issue, unit)
        assert response.status_code == 200
        assert link_of(issue).primary_executor_id == second_user.id

    def test_deactivated_area_only_a_workspace_admin_can_move_the_item_out(
        self,
        member_client,
        admin_client,
        workspace_with_members,
        setup,
        assigned,
        add_coordinator,
        plain_user,
    ):
        project, unit, second_unit = setup
        issue, _ = assigned
        add_coordinator(unit, plain_user)
        unit.is_active = False
        unit.save(update_fields=["is_active"])

        # A coordinator of a deactivated area no longer coordinates it.
        refused = self.post_unit(member_client, workspace_with_members.slug, project, issue, second_unit)
        assert refused.status_code == 403
        assert link_of(issue).organizational_unit_id == unit.id

        allowed = self.post_unit(admin_client, workspace_with_members.slug, project, issue, second_unit)
        assert allowed.status_code == 200, allowed.data
        assert link_of(issue).organizational_unit_id == second_unit.id


@pytest.mark.unit
class TestCanChangeResponsibilityFlag:
    def get_flag(self, client, slug, project, issue):
        response = client.get(issue_unit_url(slug, project.id, issue.id))
        assert response.status_code == 200
        return response.data["routing"]["can_change_responsibility"]

    def test_flag_follows_the_gate(
        self, member_client, admin_client, workspace_with_members, setup, assigned, add_coordinator, plain_user
    ):
        project, unit, _ = setup
        issue, _ = assigned
        slug = workspace_with_members.slug
        assert self.get_flag(member_client, slug, project, issue) is False
        assert self.get_flag(admin_client, slug, project, issue) is True
        add_coordinator(unit, plain_user)
        assert self.get_flag(member_client, slug, project, issue) is True


@pytest.mark.unit
class TestProcessInstanceHiddenSteps:
    def make_run(self, workspace, items):
        instance = ProcessInstanceReference.objects.create(
            workspace=workspace,
            external_source=SOURCE,
            external_instance_id="run-1",
            template_name="Onboarding",
            template_version="1.0",
        )
        for issue, key in items:
            ProcessInstanceItem.objects.create(
                workspace=workspace, process_instance=instance, issue=issue, step_key=key
            )
        return instance

    def test_progress_counts_only_visible_steps(
        self,
        settings,
        workspace_with_members,
        project,
        second_project,
        grant_manual_access,
        make_issue,
        plain_user,
        token_client,
    ):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = True
        grant_manual_access(project, plain_user, ROLE_MEMBER)
        self.make_run(
            workspace_with_members,
            [(make_issue(project), "a"), (make_issue(second_project), "b"), (make_issue(second_project), "c")],
        )
        response = token_client(plain_user).get(
            public_process_instance_url(workspace_with_members.slug, SOURCE, "run-1")
        )
        assert response.status_code == 200
        assert response.data["instance"]["progress"] == {"done": 0, "total": 1}

    def test_run_with_no_visible_steps_answers_like_a_missing_run(
        self, settings, workspace_with_members, second_project, make_issue, plain_user, token_client
    ):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = True
        self.make_run(workspace_with_members, [(make_issue(second_project), "a")])
        client = token_client(plain_user)
        hidden = client.get(public_process_instance_url(workspace_with_members.slug, SOURCE, "run-1"))
        missing = client.get(public_process_instance_url(workspace_with_members.slug, SOURCE, "nope"))
        assert hidden.status_code == missing.status_code == 404
        assert hidden.data["error_message"] == missing.data["error_message"]


@pytest.mark.unit
@pytest.mark.django_db
class TestScimReplaceCannotEmptyAGroupByAccident:
    @pytest.fixture
    def member_row(self, bound_unit, make_identity, put_in_group):
        ident = make_identity("plain@plane.so")
        put_in_group(bound_unit, ident)
        return ident

    def count(self, unit):
        from plane.db.models import OrganizationalDirectoryGroupMembership

        return OrganizationalDirectoryGroupMembership.objects.filter(organizational_unit=unit).count()

    @pytest.mark.parametrize("value", ["", {}, [{"display": "x"}], "abc", 5])
    def test_patch_replace_with_malformed_value_is_rejected(
        self, scim_client, workspace_with_members, bound_unit, member_row, value
    ):
        response = scim_client.patch(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {"schemas": [SCHEMA_PATCH_OP], "Operations": [{"op": "replace", "path": "members", "value": value}]},
            format="json",
        )
        assert response.status_code == 400
        assert self.count(bound_unit) == 1

    def test_pathless_replace_with_malformed_members_is_rejected(
        self, scim_client, workspace_with_members, bound_unit, member_row
    ):
        response = scim_client.patch(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {"schemas": [SCHEMA_PATCH_OP], "Operations": [{"op": "replace", "value": {"members": [{"display": "x"}]}}]},
            format="json",
        )
        assert response.status_code == 400
        assert self.count(bound_unit) == 1

    def test_put_with_malformed_members_is_rejected(self, scim_client, workspace_with_members, bound_unit, member_row):
        response = scim_client.put(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {"schemas": [SCHEMA_GROUP], "displayName": "G", "members": ""},
            format="json",
        )
        assert response.status_code == 400
        assert self.count(bound_unit) == 1

    def test_explicit_empty_list_still_clears_the_group(
        self, scim_client, workspace_with_members, bound_unit, member_row
    ):
        response = scim_client.patch(
            scim_group_url(workspace_with_members.slug, bound_unit.id),
            {"schemas": [SCHEMA_PATCH_OP], "Operations": [{"op": "replace", "path": "members", "value": []}]},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert self.count(bound_unit) == 0
