# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Absences and per-area limits over HTTP, and the sweep that acts on them
(items 3.3, 3.4).

Who may write what is the whole design here, so that is what most of these
check. The rest are about the sweep, whose failure mode is not a wrong answer
but a *silent* one: work that stays assigned to somebody who is not there
looks fine on every screen until a coordinator wonders why nothing is moving.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.app.services.orca import claim
from plane.app.services.orca.availability_sweep import (
    REASON_AWAY,
    REASON_NOT_AN_ASSIGNEE,
    REASON_NOT_IN_UNIT,
    REASON_NOT_IN_WORKSPACE,
    return_stranded_items,
    stranded_items,
)
from plane.bgtasks.organizational_availability_task import sweep_unavailable_executors
from plane.db.models import (
    AssignmentDecision,
    IssueAssignee,
    IssueOrganizationalUnit,
    MembershipAllocationSettings,
    Notification,
    RoutingState,
    WorkspaceMemberAvailability,
)
from plane.utils.orca_error_codes import ORCA_ERROR_CODES

from .conftest import ROLE_MEMBER, units_url


def my_availability_url(slug):
    return f"/api/orca/workspaces/{slug}/availability/me/"


def member_availability_url(slug, workspace_member_id):
    return f"/api/orca/workspaces/{slug}/members/{workspace_member_id}/availability/"


def allocation_url(slug, unit_id, membership_id):
    return f"{units_url(slug)}{unit_id}/members/{membership_id}/allocation/"


@pytest.fixture
def availability_on(settings):
    settings.ORCA_ORG_UNITS_ENABLED = True
    settings.ORCA_AVAILABILITY_ENABLED = True
    return settings


@pytest.fixture
def api_client_for():
    def _make(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _make


@pytest.fixture
def staffed(unit, project, link_project, add_member, grant_manual_access, plain_user, second_user):
    link_project(unit, project, ROLE_MEMBER)
    memberships = {}
    for user in (plain_user, second_user):
        memberships[user] = add_member(unit, user)
        grant_manual_access(project, user)
    return memberships


@pytest.fixture
def assigned_item(unit, project, make_issue, staffed, plain_user):
    """An item the area owns, claimed by the first member."""

    def _make(user=None):
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.QUEUED,
        )
        claim(issue, user or plain_user)
        return issue

    return _make


@pytest.mark.unit
@pytest.mark.django_db
class TestRecordingYourOwnAbsence:
    def test_anybody_can_record_their_own(self, availability_on, member_client, workspace_with_members):
        now = timezone.now()
        response = member_client.post(
            my_availability_url(workspace_with_members.slug),
            {
                "unavailable_from": now.isoformat(),
                "unavailable_until": (now + timedelta(days=7)).isoformat(),
                "reason": "vacation",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert WorkspaceMemberAvailability.objects.count() == 1

    def test_the_read_says_whether_you_are_away_right_now(self, availability_on, member_client, workspace_with_members):
        member_client.post(
            my_availability_url(workspace_with_members.slug),
            {"unavailable_from": (timezone.now() - timedelta(hours=1)).isoformat()},
            format="json",
        )

        response = member_client.get(my_availability_url(workspace_with_members.slug))

        assert response.data["available"] is False
        assert response.data["current"]["reason"] == "vacation"
        assert len(response.data["windows"]) == 1

    def test_a_window_that_ends_before_it_starts_is_refused(
        self, availability_on, member_client, workspace_with_members
    ):
        now = timezone.now()
        response = member_client.post(
            my_availability_url(workspace_with_members.slug),
            {"unavailable_from": now.isoformat(), "unavailable_until": (now - timedelta(days=1)).isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_INVALID_AVAILABILITY_WINDOW"]

    def test_a_missing_start_is_refused(self, availability_on, member_client, workspace_with_members):
        response = member_client.post(my_availability_url(workspace_with_members.slug), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_you_can_delete_your_own(self, availability_on, member_client, workspace_with_members):
        created = member_client.post(
            my_availability_url(workspace_with_members.slug),
            {"unavailable_from": timezone.now().isoformat()},
            format="json",
        )

        response = member_client.delete(f"{my_availability_url(workspace_with_members.slug)}?id={created.data['id']}")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert WorkspaceMemberAvailability.objects.count() == 0

    def test_you_cannot_delete_somebody_elses_through_this_route(
        self, availability_on, member_client, workspace_with_members, workspace_member_of, second_user
    ):
        theirs = WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(second_user), unavailable_from=timezone.now()
        )

        response = member_client.delete(f"{my_availability_url(workspace_with_members.slug)}?id={theirs.id}")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_switch_off_hides_the_route(self, settings, member_client, workspace_with_members):
        settings.ORCA_AVAILABILITY_ENABLED = False

        response = member_client.get(my_availability_url(workspace_with_members.slug))

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
@pytest.mark.django_db
class TestRecordingSomebodyElsesAbsence:
    def test_a_coordinator_of_one_of_their_areas_can(
        self,
        availability_on,
        unit,
        staffed,
        add_coordinator,
        api_client_for,
        workspace_with_members,
        workspace_member_of,
        guest_user,
        plain_user,
    ):
        add_coordinator(unit, guest_user)

        response = api_client_for(guest_user).post(
            member_availability_url(workspace_with_members.slug, workspace_member_of(plain_user).id),
            {"unavailable_from": timezone.now().isoformat(), "reason": "leave"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_an_admin_can(self, availability_on, admin_client, workspace_with_members, workspace_member_of, plain_user):
        response = admin_client.post(
            member_availability_url(workspace_with_members.slug, workspace_member_of(plain_user).id),
            {"unavailable_from": timezone.now().isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_a_coordinator_of_an_area_they_are_not_in_cannot(
        self,
        availability_on,
        second_unit,
        staffed,
        add_coordinator,
        api_client_for,
        workspace_with_members,
        workspace_member_of,
        guest_user,
        plain_user,
    ):
        add_coordinator(second_unit, guest_user)

        response = api_client_for(guest_user).post(
            member_availability_url(workspace_with_members.slug, workspace_member_of(plain_user).id),
            {"unavailable_from": timezone.now().isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_colleague_cannot(
        self,
        availability_on,
        staffed,
        api_client_for,
        workspace_with_members,
        workspace_member_of,
        second_user,
        plain_user,
    ):
        response = api_client_for(second_user).get(
            member_availability_url(workspace_with_members.slug, workspace_member_of(plain_user).id)
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.unit
@pytest.mark.django_db
class TestWhatOneAreaMayPutOnSomebody:
    def test_the_defaults_are_spelled_out_before_anything_is_written(
        self, availability_on, admin_client, workspace_with_members, unit, staffed, plain_user
    ):
        response = admin_client.get(allocation_url(workspace_with_members.slug, unit.id, staffed[plain_user].id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "membership": str(staffed[plain_user].id),
            "member_id": str(plain_user.id),
            "accepts_new_work": True,
            "max_open_items": None,
            "settings": None,
        }

    def test_a_coordinator_sets_both(
        self,
        availability_on,
        unit,
        staffed,
        add_coordinator,
        api_client_for,
        workspace_with_members,
        guest_user,
        plain_user,
    ):
        add_coordinator(unit, guest_user)

        response = api_client_for(guest_user).put(
            allocation_url(workspace_with_members.slug, unit.id, staffed[plain_user].id),
            {"accepts_new_work": False, "max_open_items": 3},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        settings_row = MembershipAllocationSettings.objects.get(membership=staffed[plain_user])
        assert settings_row.accepts_new_work is False
        assert settings_row.max_open_items == 3

    def test_the_person_can_stop_accepting_work(
        self, availability_on, member_client, workspace_with_members, unit, staffed, plain_user
    ):
        response = member_client.put(
            allocation_url(workspace_with_members.slug, unit.id, staffed[plain_user].id),
            {"accepts_new_work": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert MembershipAllocationSettings.objects.get(membership=staffed[plain_user]).accepts_new_work is False

    def test_the_person_can_start_again(
        self, availability_on, member_client, workspace_with_members, unit, staffed, plain_user
    ):
        MembershipAllocationSettings.objects.create(membership=staffed[plain_user], accepts_new_work=False)

        response = member_client.put(
            allocation_url(workspace_with_members.slug, unit.id, staffed[plain_user].id),
            {"accepts_new_work": True},
            format="json",
        )

        assert response.data["accepts_new_work"] is True

    def test_the_person_cannot_set_their_own_ceiling(
        self, availability_on, member_client, workspace_with_members, unit, staffed, plain_user
    ):
        # A number that shapes how the area distributes work is the area's to
        # decide; "not more right now" is the person's.
        response = member_client.put(
            allocation_url(workspace_with_members.slug, unit.id, staffed[plain_user].id),
            {"max_open_items": 1},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_ALLOCATION_LIMIT_FORBIDDEN"]

    def test_a_colleague_can_change_nothing(
        self, availability_on, api_client_for, workspace_with_members, unit, staffed, second_user, plain_user
    ):
        response = api_client_for(second_user).put(
            allocation_url(workspace_with_members.slug, unit.id, staffed[plain_user].id),
            {"accepts_new_work": False},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_ceiling_of_zero_is_refused(
        self, availability_on, admin_client, workspace_with_members, unit, staffed, plain_user
    ):
        response = admin_client.put(
            allocation_url(workspace_with_members.slug, unit.id, staffed[plain_user].id),
            {"max_open_items": 0},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.unit
@pytest.mark.django_db
class TestTheSweep:
    def test_work_held_by_somebody_away_comes_back(
        self, availability_on, assigned_item, workspace_member_of, plain_user, unit, add_coordinator, second_user
    ):
        add_coordinator(unit, second_user)
        issue = assigned_item()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now() - timedelta(hours=1)
        )

        assert sweep_unavailable_executors() == 1

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == "executor_unavailable"
        assert link.primary_executor_id is None
        # The person keeps seeing the item: they were away, not removed.
        assert IssueAssignee.objects.filter(issue=issue, assignee=plain_user).exists()
        # And the area hears about it, because nobody clicked.
        assert Notification.objects.filter(receiver=second_user).count() == 1

    def test_the_return_is_a_decision_with_its_own_trigger(
        self, availability_on, assigned_item, workspace_member_of, plain_user
    ):
        issue = assigned_item()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now() - timedelta(hours=1)
        )

        sweep_unavailable_executors()

        decision = AssignmentDecision.objects.filter(issue=issue).order_by("-created_at").first()
        assert decision.trigger == "availability"
        assert decision.previous_primary_executor_id == plain_user.id

    def test_a_second_pass_finds_nothing_to_do(self, availability_on, assigned_item, workspace_member_of, plain_user):
        assigned_item()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now() - timedelta(hours=1)
        )

        sweep_unavailable_executors()
        assert sweep_unavailable_executors() == 0

    def test_leaving_the_area_strands_the_work_too(self, availability_on, assigned_item, staffed, plain_user):
        assigned_item()
        membership = staffed[plain_user]
        membership.is_active = False
        membership.save()

        assert [reason for _, reason in stranded_items()] == [REASON_NOT_IN_UNIT]

    def test_being_deactivated_in_the_workspace_does(
        self, availability_on, assigned_item, workspace_member_of, plain_user
    ):
        assigned_item()
        member = workspace_member_of(plain_user)
        member.is_active = False
        member.save()

        assert [reason for _, reason in stranded_items()] == [REASON_NOT_IN_WORKSPACE]

    def test_being_taken_off_the_item_natively_does(self, availability_on, assigned_item, plain_user):
        issue = assigned_item()
        # The app's own "change the assignees" path: a queryset delete, which
        # fires no signal at all. This pass is what catches it.
        IssueAssignee.objects.filter(issue=issue, assignee=plain_user).delete()

        assert [reason for _, reason in stranded_items()] == [REASON_NOT_AN_ASSIGNEE]

    def test_the_dry_run_writes_nothing(self, availability_on, assigned_item, workspace_member_of, plain_user):
        issue = assigned_item()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now() - timedelta(hours=1)
        )

        results = return_stranded_items()

        assert [entry["reason"] for entry in results] == [REASON_AWAY]
        assert all(entry["returned"] is False for entry in results)
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED

    def test_the_availability_switch_stops_the_scheduled_pass(
        self, settings, assigned_item, workspace_member_of, plain_user
    ):
        issue = assigned_item()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now() - timedelta(hours=1)
        )
        settings.ORCA_AVAILABILITY_ENABLED = False

        assert sweep_unavailable_executors() == 0
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED

    def test_the_layer_switch_stops_it_as_well(
        self, availability_on, settings, assigned_item, workspace_member_of, plain_user
    ):
        assigned_item()
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now() - timedelta(hours=1)
        )
        settings.ORCA_ORG_UNITS_ENABLED = False

        assert sweep_unavailable_executors() == 0

    def test_finished_work_is_not_swept(
        self, availability_on, unit, project, make_issue, staffed, workspace_member_of, plain_user
    ):
        from plane.db.models import StateGroup

        issue = make_issue(project, state_group=StateGroup.COMPLETED.value)
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=project.workspace,
            routing_state=RoutingState.ASSIGNED,
            primary_executor=plain_user,
        )
        WorkspaceMemberAvailability.objects.create(
            workspace_member=workspace_member_of(plain_user), unavailable_from=timezone.now() - timedelta(hours=1)
        )

        assert stranded_items() == []


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAssigneeSignal:
    def test_removing_the_executor_from_the_item_returns_it(
        self, availability_on, assigned_item, plain_user, django_capture_on_commit_callbacks
    ):
        issue = assigned_item()

        with django_capture_on_commit_callbacks(execute=True):
            IssueAssignee.objects.get(issue=issue, assignee=plain_user).delete()

        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.QUEUED
        assert link.queue_reason == "executor_unavailable"

    def test_removing_a_collaborator_leaves_the_item_alone(
        self, availability_on, assigned_item, project, second_user, plain_user, django_capture_on_commit_callbacks
    ):
        issue = assigned_item()
        collaborator = IssueAssignee.objects.create(
            issue=issue, assignee=second_user, project=project, workspace=project.workspace
        )

        with django_capture_on_commit_callbacks(execute=True):
            collaborator.delete()

        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED

    def test_a_rollback_leaves_no_decision_behind(self, availability_on, assigned_item, plain_user):
        issue = assigned_item()
        before = AssignmentDecision.objects.filter(issue=issue).count()

        # No `django_capture_on_commit_callbacks`: the enclosing test
        # transaction never commits, which is what a rolled-back removal looks
        # like to the callback.
        IssueAssignee.objects.get(issue=issue, assignee=plain_user).delete()

        assert AssignmentDecision.objects.filter(issue=issue).count() == before
        assert IssueOrganizationalUnit.objects.get(issue=issue).routing_state == RoutingState.ASSIGNED
