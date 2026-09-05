# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The routing audit: the invariants no database constraint can hold.

An item the area considers assigned must have its executor as a live assignee
(I3), and that executor must still be an active member of the area and of the
project (I4). Both span tables a CHECK cannot see, and both are broken by
ordinary Plane operations — removing an assignee in the work item, taking
somebody off a project, a directory sync withdrawing a membership. So they are
audited rather than assumed, and the command repairs only what it is asked to.
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from plane.app.services.orca.routing_audit import (
    ASSIGNED_WITHOUT_ASSIGNEE,
    EXECUTOR_NOT_ELIGIBLE,
    POLICY_CONTRADICTS_ITSELF,
    QUEUED_WITH_ASSIGNEE,
    audit_routing,
)
from plane.db.models import (
    AssignmentMode,
    DecisionTrigger,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitMembership,
    ProjectMember,
    QueueReason,
    RoutingState,
)

from .conftest import ROLE_MEMBER


@pytest.fixture
def covered(unit, project, link_project):
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def staffed(covered, unit, project, add_member, grant_manual_access, plain_user):
    add_member(unit, plain_user)
    grant_manual_access(project, plain_user)
    return plain_user


@pytest.fixture
def assigned_item(unit, project, make_issue, staffed, workspace_with_members):
    """An item the area considers assigned, with everything in order."""
    issue = make_issue(project)
    IssueAssignee.objects.create(issue=issue, assignee=staffed, project=project, workspace=workspace_with_members)
    IssueOrganizationalUnit.objects.create(
        issue=issue,
        organizational_unit=unit,
        project=project,
        workspace=workspace_with_members,
        routing_state=RoutingState.ASSIGNED,
        primary_executor=staffed,
    )
    return issue


def run(slug, *args):
    out = StringIO()
    call_command("audit_organizational_routing", "--workspace", slug, *args, stdout=out)
    return out.getvalue()


def kinds(findings):
    return [finding.kind for finding in findings]


@pytest.mark.unit
class TestWhatTheAuditFinds:
    def test_a_healthy_workspace_has_nothing_to_report(self, workspace_with_members, assigned_item):
        assert audit_routing(workspace_with_members.id) == []

    def test_an_executor_who_is_no_longer_an_assignee(self, workspace_with_members, assigned_item, staffed):
        """Someone removed the assignee in the work item; the area still
        believes the item is being worked (I3)."""
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        findings = audit_routing(workspace_with_members.id)

        assert kinds(findings) == [ASSIGNED_WITHOUT_ASSIGNEE]
        assert findings[0].issue_id == assigned_item.id
        assert findings[0].repaired is False

    def test_an_executor_who_left_the_area(self, workspace_with_members, assigned_item, unit, staffed):
        OrganizationalUnitMembership.objects.filter(organizational_unit=unit, workspace_member__member=staffed).update(
            is_active=False
        )

        findings = audit_routing(workspace_with_members.id)

        assert kinds(findings) == [EXECUTOR_NOT_ELIGIBLE]
        assert findings[0].detail == "not_a_unit_member"

    def test_an_executor_who_lost_project_access(self, workspace_with_members, assigned_item, project, staffed):
        ProjectMember.objects.filter(project=project, member=staffed).update(is_active=False)

        findings = audit_routing(workspace_with_members.id)

        assert kinds(findings) == [EXECUTOR_NOT_ELIGIBLE]
        assert findings[0].detail == "not_an_assignable_project_member"

    def test_a_queued_item_that_somebody_is_already_on(
        self, workspace_with_members, unit, project, make_issue, staffed
    ):
        """Reported, never repaired: the assignee may be a collaborator a
        coordinator put there on purpose."""
        issue = make_issue(project)
        IssueAssignee.objects.create(issue=issue, assignee=staffed, project=project, workspace=workspace_with_members)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            routing_state=RoutingState.QUEUED,
            queue_reason=QueueReason.AWAITING_COORDINATOR,
        )

        findings = audit_routing(workspace_with_members.id)

        assert kinds(findings) == [QUEUED_WITH_ASSIGNEE]

    def test_a_policy_that_forbids_its_own_default(self, workspace_with_members, unit):
        """``clean()`` catches this on the way in, so a row with it was written
        around the model — a fixture, a data migration, a shell."""
        policy = OrganizationalUnitAssignmentPolicy(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.MANUAL.value],
        )
        policy.save()

        findings = audit_routing(workspace_with_members.id)

        assert kinds(findings) == [POLICY_CONTRADICTS_ITSELF]
        assert findings[0].policy_id == policy.id

    def test_findings_from_another_workspace_are_not_reported(
        self, workspace_with_members, other_workspace, assigned_item, staffed
    ):
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        assert audit_routing(other_workspace.id) == []


@pytest.mark.unit
class TestWhatWriteRepairs:
    def test_dry_run_changes_nothing(self, workspace_with_members, assigned_item, staffed):
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        audit_routing(workspace_with_members.id)

        link = IssueOrganizationalUnit.objects.get(issue=assigned_item)
        assert link.routing_state == RoutingState.ASSIGNED
        assert link.primary_executor_id == staffed.id

    def test_write_returns_the_item_to_the_queue(self, workspace_with_members, assigned_item, staffed):
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        findings = audit_routing(workspace_with_members.id, write=True)

        assert findings[0].repaired is True
        link = IssueOrganizationalUnit.objects.get(issue=assigned_item)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.EXECUTOR_UNAVAILABLE
        assert link.primary_executor_id is None

    def test_the_repair_is_a_decision_like_any_other(self, workspace_with_members, assigned_item, staffed):
        """Not an UPDATE nobody can trace: the trail says the command did it."""
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        audit_routing(workspace_with_members.id, write=True)

        decision = IssueOrganizationalUnit.objects.get(issue=assigned_item).current_assignment_decision
        assert decision.trigger == DecisionTrigger.COMMAND
        assert decision.previous_primary_executor_id == staffed.id
        assert decision.reason.startswith("audit:")

    def test_a_queued_item_with_an_assignee_is_left_alone(
        self, workspace_with_members, unit, project, make_issue, staffed
    ):
        issue = make_issue(project)
        IssueAssignee.objects.create(issue=issue, assignee=staffed, project=project, workspace=workspace_with_members)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            routing_state=RoutingState.QUEUED,
            queue_reason=QueueReason.AWAITING_COORDINATOR,
        )

        audit_routing(workspace_with_members.id, write=True)

        assert IssueAssignee.objects.filter(issue=issue).count() == 1
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.QUEUED

    def test_running_it_twice_finds_nothing_the_second_time(self, workspace_with_members, assigned_item, staffed):
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        audit_routing(workspace_with_members.id, write=True)

        assert audit_routing(workspace_with_members.id) == []


@pytest.mark.unit
class TestTheCommand:
    def test_it_reports_the_findings(self, workspace_with_members, assigned_item, staffed):
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        output = run(workspace_with_members.slug)

        assert ASSIGNED_WITHOUT_ASSIGNEE in output
        assert "Re-run with --write" in output
        assert IssueOrganizationalUnit.objects.get(issue=assigned_item).routing_state == RoutingState.ASSIGNED

    def test_write_repairs_and_says_so(self, workspace_with_members, assigned_item, staffed):
        IssueAssignee.objects.filter(issue=assigned_item, assignee=staffed).delete(soft=False)

        output = run(workspace_with_members.slug, "--write")

        assert "returned to the queue" in output
        assert IssueOrganizationalUnit.objects.get(issue=assigned_item).routing_state == RoutingState.QUEUED

    def test_a_clean_workspace_says_so(self, workspace_with_members, assigned_item):
        assert "No routing violations found." in run(workspace_with_members.slug)

    def test_an_unknown_workspace_is_an_error(self, db):
        with pytest.raises(CommandError):
            run("nope")

    def test_the_kill_switch_closes_the_command(self, settings, workspace_with_members, assigned_item):
        """Same rule as the reconciler: while the layer is off, nothing runs on
        its behalf."""
        settings.ORCA_ORG_UNITS_ENABLED = False

        with pytest.raises(CommandError):
            run(workspace_with_members.slug)
