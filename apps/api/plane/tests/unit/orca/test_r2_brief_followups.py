# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the R2 brief technical follow-ups:

1. ``can_change_responsibility`` is populated (never ``None``) on claim, return,
   reassign, transfer, and assign responses.
2. Public create-or-find ``_place`` re-checks the D2 coordinator gate under row
   lock, closing the TOCTOU window after ``_would_move_without_standing``.
3. Bulk state reopen (``BulkIssueOperationsEndpoint``) restarts the waiting
   clock on queued items just like single-row ``Issue.save()``.
4. Process progress excludes steps belonging to archived projects.
5. R2.07 edge cases: overlapping windows, extended windows (``updated_at``),
   and bounded expiration for overrides on open-ended (``unavailable_until=None``)
   windows.
6. ``sweep_routing_drift`` respects a per-run repair cap.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from plane.api.views.orca.work_items import WorkItemAutomationEndpoint
from plane.app.services.orca import (
    instance_progress,
    process_payloads_for,
    reassign,
    set_responsibility,
)
from plane.app.services.orca.availability import covering_windows_for
from plane.bgtasks.organizational_availability_task import (
    UNBOUNDED_OVERRIDE_GRACE,
    unusable_executor_links,
)
from plane.bgtasks.organizational_queue_task import sweep_routing_drift
from plane.db.models import (
    AssignmentMode,
    ExternalWorkItemBinding,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    ProcessInstanceItem,
    ProcessInstanceReference,
    RoutingState,
    State,
    StateGroup,
    WorkspaceMember,
    WorkspaceMemberAvailability,
)

from .conftest import (
    ROLE_MEMBER,
    issue_assign_url,
    issue_claim_url,
    issue_reassign_url,
    issue_return_url,
    issue_transfer_url,
    public_process_instance_url,
    public_work_items_url,
)


@pytest.fixture
def setup(
    workspace_with_members,
    project,
    unit,
    second_unit,
    link_project,
    add_member,
    grant_manual_access,
    plain_user,
    second_user,
    admin_user,
):
    link_project(unit, project, ROLE_MEMBER)
    link_project(second_unit, project, ROLE_MEMBER)
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user, ROLE_MEMBER)
    grant_manual_access(project, admin_user, ROLE_MEMBER)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    OrganizationalUnitAssignmentPolicy.objects.create(
        workspace=workspace_with_members,
        organizational_unit=unit,
        default_mode=AssignmentMode.MANUAL,
        allowed_modes=[
            AssignmentMode.MANUAL,
            AssignmentMode.SELF_CLAIM,
            AssignmentMode.LEAST_LOADED,
        ],
        assignment_sla_seconds=3600,
    )


@pytest.mark.unit
@pytest.mark.django_db
class TestCanChangeResponsibilityOnActionRoutes:
    """Every routing action response includes a boolean ``can_change_responsibility``."""

    def test_claim_return_reassign_transfer_and_assign_populate_flag(
        self,
        setup,
        workspace_with_members,
        project,
        unit,
        second_unit,
        make_issue,
        second_user,
        admin_user,
        member_client,
        admin_client,
    ):
        issue = make_issue(project, name="Action routes flag")
        set_responsibility(issue, unit, actor=admin_user, requested_mode=AssignmentMode.MANUAL)

        claim_resp = member_client.post(issue_claim_url(workspace_with_members.slug, project.id, issue.id))
        assert claim_resp.status_code == 200
        assert claim_resp.data["can_change_responsibility"] is False

        return_resp = member_client.post(
            issue_return_url(workspace_with_members.slug, project.id, issue.id), {}, format="json"
        )
        assert return_resp.status_code == 200
        assert return_resp.data["can_change_responsibility"] is False

        reassign_resp = admin_client.post(
            issue_reassign_url(workspace_with_members.slug, project.id, issue.id),
            {"executor_id": str(second_user.id)},
            format="json",
        )
        assert reassign_resp.status_code == 200
        assert reassign_resp.data["can_change_responsibility"] is True

        assign_noop_resp = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )
        assert assign_noop_resp.status_code == 200
        assert assign_noop_resp.data["routing"]["can_change_responsibility"] is True

        transfer_resp = admin_client.post(
            issue_transfer_url(workspace_with_members.slug, project.id, issue.id),
            {"unit_id": str(second_unit.id)},
            format="json",
        )
        assert transfer_resp.status_code == 200
        assert transfer_resp.data["can_change_responsibility"] is True


@pytest.mark.unit
@pytest.mark.django_db
class TestPublicCreateOrFindLockedGate:
    """``_place`` enforces the D2 coordinator gate under row lock even if the pre-check missed a race."""

    def test_member_cannot_move_item_if_link_appears_after_pre_check(
        self,
        setup,
        workspace_with_members,
        project,
        unit,
        second_unit,
        make_issue,
        plain_user,
        admin_user,
        token_client,
    ):
        issue = make_issue(project, name="Race target")
        ExternalWorkItemBinding.objects.create(
            workspace=workspace_with_members,
            issue=issue,
            external_source="crm",
            external_id="race-1",
        )
        set_responsibility(issue, unit, actor=admin_user, requested_mode=AssignmentMode.MANUAL)
        client = token_client(plain_user, workspace_with_members)

        body = {
            "external": {"source": "crm", "id": "race-1"},
            "work_item": {"name": "Race target"},
            "responsibility": {"unit": second_unit.slug},
        }
        with patch.object(WorkItemAutomationEndpoint, "_would_move_without_standing", return_value=False):
            response = client.post(
                public_work_items_url(workspace_with_members.slug, project.id),
                body,
                format="json",
                HTTP_IDEMPOTENCY_KEY="race-key-1",
            )

        assert response.status_code == 403
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.organizational_unit_id == unit.id


@pytest.mark.unit
@pytest.mark.django_db
class TestBulkReopenRestartsWaitingClock:
    """Reopening a queued item via BulkIssueOperationsEndpoint restarts its waiting clock (R2.06)."""

    def test_bulk_reopen_resets_queued_at_deadline_and_last_alerted_at(
        self,
        setup,
        workspace_with_members,
        project,
        unit,
        make_issue,
        admin_user,
        admin_client,
    ):
        done_state = State.objects.create(project=project, name="Done", group=StateGroup.COMPLETED.value)
        open_state = State.objects.filter(project=project, group=StateGroup.BACKLOG.value).first()
        issue = make_issue(project, name="Closed queued item")
        issue.state = done_state
        issue.save(update_fields=["state"])
        result = set_responsibility(issue, unit, actor=admin_user, requested_mode=AssignmentMode.MANUAL)

        old_time = timezone.now() - timedelta(days=5)
        IssueOrganizationalUnit.objects.filter(pk=result.link.pk).update(
            queued_at=old_time,
            assignment_due_at=old_time + timedelta(hours=1),
            last_alerted_at=old_time + timedelta(hours=2),
        )

        url = f"/api/workspaces/{workspace_with_members.slug}/projects/{project.id}/bulk-operation-issues/"
        response = admin_client.post(
            url,
            {"issue_ids": [str(issue.id)], "properties": {"state_id": str(open_state.id)}},
            format="json",
        )
        assert response.status_code == 204

        refreshed = IssueOrganizationalUnit.objects.get(pk=result.link.pk)
        assert refreshed.queued_at > old_time
        assert refreshed.assignment_due_at is not None and refreshed.assignment_due_at > timezone.now()
        assert refreshed.last_alerted_at is None


@pytest.mark.unit
@pytest.mark.django_db
class TestProcessProgressExcludesArchivedProjects:
    """Steps in archived projects are excluded from process progress and detail listings."""

    def test_archived_project_steps_are_excluded(
        self,
        setup,
        workspace_with_members,
        project,
        second_project,
        unit,
        make_issue,
        admin_user,
        token_client,
        settings,
    ):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = True
        archived_state = State.objects.create(project=second_project, name="Done", group=StateGroup.COMPLETED.value)
        open_state = State.objects.filter(project=project, group=StateGroup.BACKLOG.value).first()

        active_issue = make_issue(project, name="Active step")
        active_issue.state = open_state
        active_issue.save(update_fields=["state"])
        archived_issue = make_issue(second_project, name="Archived step")
        archived_issue.state = archived_state
        archived_issue.save(update_fields=["state"])
        link = set_responsibility(active_issue, unit, actor=admin_user, requested_mode=AssignmentMode.MANUAL).link

        instance = ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="bpm",
            external_instance_id="run-arch-1",
            template_name="Onboarding",
            template_version="1",
        )
        ProcessInstanceItem.objects.create(
            workspace=workspace_with_members, process_instance=instance, issue=active_issue, step_key="step-active"
        )
        ProcessInstanceItem.objects.create(
            workspace=workspace_with_members, process_instance=instance, issue=archived_issue, step_key="step-archived"
        )

        second_project.archived_at = timezone.now()
        second_project.save(update_fields=["archived_at"])

        assert instance_progress(instance) == {"done": 0, "total": 1}
        payloads = process_payloads_for([link])
        assert payloads[active_issue.id]["total"] == 1
        assert payloads[active_issue.id]["done"] == 0

        client = token_client(admin_user, workspace_with_members)
        resp = client.get(public_process_instance_url(workspace_with_members.slug, "bpm", "run-arch-1"))
        assert resp.status_code == 200
        assert resp.data["instance"]["progress"] == {"done": 0, "total": 1}
        assert [row["step_key"] for row in resp.data["items"]] == ["step-active"]


@pytest.mark.unit
@pytest.mark.django_db
class TestR207AvailabilityEdgeCases:
    """Overlapping windows, extended windows, and open-ended override expiration (R2.07)."""

    def test_new_overlapping_window_overrides_prior_intentional_assignment(
        self,
        setup,
        workspace_with_members,
        project,
        unit,
        make_issue,
        plain_user,
        admin_user,
        settings,
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        now = timezone.now()
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)

        # Window A existed before the coordinator assigned plain_user.
        window_a = WorkspaceMemberAvailability.objects.create(
            workspace=workspace_with_members,
            workspace_member=wm,
            unavailable_from=now - timedelta(hours=6),
            unavailable_until=now + timedelta(days=2),
            reason="vacation",
        )
        WorkspaceMemberAvailability.objects.filter(pk=window_a.pk).update(
            created_at=now - timedelta(hours=6),
            updated_at=now - timedelta(hours=6),
        )

        issue = make_issue(project, name="Overlapping absence item")
        set_responsibility(issue, unit, actor=admin_user, requested_mode=AssignmentMode.MANUAL)
        reassign(issue, plain_user.id, actor=admin_user, reason="urgent")

        # Only window A: intentional override protects the item.
        assert unusable_executor_links(now=now) == []

        # Window B is added AFTER the assignment and overlaps now.
        window_b = WorkspaceMemberAvailability.objects.create(
            workspace=workspace_with_members,
            workspace_member=wm,
            unavailable_from=now - timedelta(hours=1),
            unavailable_until=now + timedelta(days=5),
            reason="sick_leave",
        )
        WorkspaceMemberAvailability.objects.filter(pk=window_b.pk).update(
            created_at=now + timedelta(minutes=1),
            updated_at=now + timedelta(minutes=1),
        )

        # Window B was not overridden -> item must be flagged as unusable.
        flagged = unusable_executor_links(now=now + timedelta(minutes=2))
        assert [link.issue_id for link in flagged] == [issue.id]

        # Deterministic selection in covering_windows_for picks latest unavailable_until (window_b).
        chosen = covering_windows_for([wm.id], at=now + timedelta(minutes=2))
        assert chosen[wm.id].id == window_b.id

    def test_unbounded_window_override_expires_after_grace_period(
        self,
        setup,
        workspace_with_members,
        project,
        unit,
        make_issue,
        plain_user,
        admin_user,
        settings,
    ):
        settings.ORCA_AVAILABILITY_ENABLED = True
        now = timezone.now()
        wm = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)

        window = WorkspaceMemberAvailability.objects.create(
            workspace=workspace_with_members,
            workspace_member=wm,
            unavailable_from=now - timedelta(days=10),
            unavailable_until=None,
            reason="leave",
        )
        WorkspaceMemberAvailability.objects.filter(pk=window.pk).update(
            created_at=now - timedelta(days=10),
            updated_at=now - timedelta(days=10),
        )

        issue = make_issue(project, name="Unbounded override item")
        set_responsibility(issue, unit, actor=admin_user, requested_mode=AssignmentMode.MANUAL)
        reassign(issue, plain_user.id, actor=admin_user, reason="temporary cover")

        # Within grace period -> protected.
        assert unusable_executor_links(now=now + timedelta(days=1)) == []

        # Past UNBOUNDED_OVERRIDE_GRACE -> expires and returns to queue.
        expired_at = now + UNBOUNDED_OVERRIDE_GRACE + timedelta(hours=1)
        flagged = unusable_executor_links(now=expired_at)
        assert [link.issue_id for link in flagged] == [issue.id]


@pytest.mark.unit
@pytest.mark.django_db
class TestDriftSweepPerRunLimit:
    """``sweep_routing_drift`` respects its per-run repair cap."""

    def test_sweep_stops_at_limit_and_leaves_remainder_for_next_run(
        self,
        setup,
        project,
        unit,
        make_issue,
        plain_user,
        admin_user,
    ):
        issue1 = make_issue(project, name="Drifted 1")
        issue2 = make_issue(project, name="Drifted 2")
        for issue in (issue1, issue2):
            set_responsibility(issue, unit, actor=admin_user, requested_mode=AssignmentMode.MANUAL)
            reassign(issue, plain_user.id, actor=admin_user)
            IssueAssignee.objects.filter(issue=issue, assignee=plain_user).delete()

        first = sweep_routing_drift(limit=1)
        assert first["repaired"] == 1
        assert first["capped"] is True
        assert (
            IssueOrganizationalUnit.objects.filter(
                issue_id__in=[issue1.id, issue2.id], routing_state=RoutingState.QUEUED
            ).count()
            == 1
        )

        second = sweep_routing_drift(limit=1)
        assert second["repaired"] == 1
        assert (
            IssueOrganizationalUnit.objects.filter(
                issue_id__in=[issue1.id, issue2.id], routing_state=RoutingState.QUEUED
            ).count()
            == 2
        )
