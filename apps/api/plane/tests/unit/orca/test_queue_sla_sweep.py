# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Telling somebody that work has been waiting too long.

The restraint is the substance here. An alert that fires every fifteen minutes
for the same late work item produces ninety-six notifications a day and trains
everybody to ignore the whole channel — so the cooldown is tested as carefully
as the alert itself.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from plane.app.services.orca import set_responsibility
from plane.bgtasks.organizational_queue_task import sweep_assignment_sla
from plane.db.models import (
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitAssignmentPolicy,
    OrganizationalUnitCoordinator,
    OrganizationalUnitMembership,
)


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def overdue_item(covered_unit, project, make_issue):
    """A work item the area promised to pick up, and did not."""
    issue = make_issue(project)
    set_responsibility(issue, covered_unit, trigger="internal_api")
    IssueOrganizationalUnit.objects.filter(issue=issue).update(assignment_due_at=timezone.now() - timedelta(hours=2))
    return issue


@pytest.fixture
def coordinator(covered_unit, workspace_with_members, workspace_member_of, plain_user):
    return OrganizationalUnitCoordinator.objects.create(
        organizational_unit=covered_unit,
        workspace_member=workspace_member_of(plain_user),
        workspace=workspace_with_members,
    )


@pytest.mark.unit
@pytest.mark.django_db
class TestTheSweep:
    def test_the_coordinator_is_told_once(self, overdue_item, coordinator, plain_user):
        sweep_assignment_sla()

        notifications = Notification.objects.filter(receiver=plain_user, entity_identifier=overdue_item.id)
        assert notifications.count() == 1

    def test_it_does_not_repeat_within_the_cooldown(self, overdue_item, coordinator, plain_user):
        """Ninety-six notifications a day is the same as none."""
        sweep_assignment_sla()
        sweep_assignment_sla()

        assert Notification.objects.filter(receiver=plain_user, entity_identifier=overdue_item.id).count() == 1

    def test_it_speaks_again_after_the_cooldown(self, overdue_item, coordinator, plain_user):
        sweep_assignment_sla()
        IssueOrganizationalUnit.objects.filter(issue=overdue_item).update(
            last_alerted_at=timezone.now() - timedelta(hours=5)
        )

        sweep_assignment_sla()

        assert Notification.objects.filter(receiver=plain_user, entity_identifier=overdue_item.id).count() == 2

    def test_work_that_is_not_late_is_left_alone(self, covered_unit, project, make_issue, coordinator):
        issue = make_issue(project)
        set_responsibility(issue, covered_unit, trigger="internal_api")
        IssueOrganizationalUnit.objects.filter(issue=issue).update(
            assignment_due_at=timezone.now() + timedelta(hours=2)
        )

        sweep_assignment_sla()

        assert not Notification.objects.filter(entity_identifier=issue.id).exists()

    def test_with_no_coordinator_the_lead_hears_about_it(
        self, overdue_item, covered_unit, workspace_with_members, workspace_member_of, second_user
    ):
        OrganizationalUnitMembership.objects.create(
            organizational_unit=covered_unit,
            workspace_member=workspace_member_of(second_user),
            workspace=workspace_with_members,
            role="lead",
        )

        sweep_assignment_sla()

        assert Notification.objects.filter(receiver=second_user, entity_identifier=overdue_item.id).exists()

    def test_with_the_layer_off_it_says_nothing(self, settings, overdue_item, coordinator):
        settings.ORCA_ORG_UNITS_ENABLED = False

        result = sweep_assignment_sla()

        assert "skipped" in result
        assert not Notification.objects.exists()


@pytest.mark.unit
@pytest.mark.django_db
class TestTheImmediateAlert:
    def test_an_allocation_that_finds_nobody_tells_the_coordinator_at_once(
        self, covered_unit, project, make_issue, coordinator, plain_user, workspace_with_members
    ):
        """
        Not something to leave for the next sweep: it usually means the area's
        membership or its project links are wrong, and nothing moves until
        somebody looks.
        """
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="least_loaded",
            allowed_modes=["least_loaded"],
        )
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, trigger="internal_api")

        assert Notification.objects.filter(receiver=plain_user, entity_identifier=issue.id).exists()
