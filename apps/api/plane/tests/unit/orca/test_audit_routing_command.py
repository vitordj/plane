# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The routing audit, and above all its dry run.

An operator who cannot trust the preview will not run the repair, so the
read-only mode has to be genuinely read-only — and the repair has to touch
only the two cases with one obviously correct answer.
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from plane.app.services.orca import set_responsibility
from plane.db.models import (
    AssignmentDecision,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    ProjectMember,
)
from plane.db.models.organizational_unit import QueueReason, RoutingState

from .conftest import ROLE_MEMBER


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


def audit(slug, write=False):
    out = StringIO()
    args = ["audit_organizational_routing", "--workspace", slug]
    if write:
        args.append("--write")
    call_command(*args, stdout=out)
    return out.getvalue()


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAudit:
    def test_a_healthy_workspace_reports_nothing(
        self, workspace_with_members, covered_unit, project, make_issue, executor
    ):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")

        output = audit(workspace_with_members.slug)

        assert "Found 0 violation(s)" in output

    def test_an_executor_removed_from_the_work_item_is_reported(
        self, workspace_with_members, covered_unit, project, make_issue, executor
    ):
        """Somebody clears the assignee in the app; the queue still says assigned."""
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")
        IssueAssignee.objects.filter(issue=issue, assignee=executor).delete()

        output = audit(workspace_with_members.slug)

        assert "executor_not_assignee" in output

    def test_an_executor_who_left_the_project_is_reported(
        self, workspace_with_members, covered_unit, project, make_issue, executor
    ):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")
        ProjectMember.objects.filter(project=project, member=executor).update(is_active=False)

        output = audit(workspace_with_members.slug)

        assert "executor_not_eligible" in output

    def test_a_queued_item_with_an_assignee_is_reported_but_not_repaired(
        self, workspace_with_members, covered_unit, project, make_issue, executor
    ):
        """It may be a collaborator somebody added on purpose."""
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")
        IssueAssignee.objects.create(issue=issue, assignee=executor, project=project, workspace=workspace_with_members)

        output = audit(workspace_with_members.slug, write=True)

        assert "queued_with_assignee" in output
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.QUEUED
        assert IssueAssignee.objects.filter(issue=issue).count() == 1

    def test_a_self_contradicting_policy_is_reported(self, workspace_with_members, covered_unit):
        policy = OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="manual",
            allowed_modes=["manual"],
        )
        # Bypass save(): the point is a row that drifted, however it got there.
        OrganizationalUnitAssignmentPolicy.objects.filter(pk=policy.pk).update(allowed_modes=["least_loaded"])

        output = audit(workspace_with_members.slug)

        assert "policy_default_not_allowed" in output


@pytest.mark.unit
@pytest.mark.django_db
class TestDryRunAndRepair:
    def test_the_dry_run_writes_nothing(self, workspace_with_members, covered_unit, project, make_issue, executor):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")
        IssueAssignee.objects.filter(issue=issue, assignee=executor).delete()
        decisions_before = AssignmentDecision.objects.count()

        audit(workspace_with_members.slug)

        assert AssignmentDecision.objects.count() == decisions_before
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED

    def test_write_returns_the_work_to_the_queue(
        self, workspace_with_members, covered_unit, project, make_issue, executor
    ):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, explicit_executor=executor, trigger="internal_api")
        IssueAssignee.objects.filter(issue=issue, assignee=executor).delete()

        audit(workspace_with_members.slug, write=True)

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == QueueReason.EXECUTOR_UNAVAILABLE
        assert link.primary_executor_id is None

    def test_it_refuses_to_run_with_the_layer_switched_off(self, settings, workspace_with_members):
        settings.ORCA_ORG_UNITS_ENABLED = False

        with pytest.raises(CommandError):
            audit(workspace_with_members.slug)

    def test_an_unknown_workspace_is_an_error(self):
        with pytest.raises(CommandError):
            audit("no-such-workspace")
