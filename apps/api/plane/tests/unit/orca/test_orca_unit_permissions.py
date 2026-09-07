# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The permission helpers behind every route of item 2.2.

These are unit tests of the predicates themselves rather than of the routes,
because the routes are many and the rule is one: if ``is_unit_coordinator``
answers wrongly here, every endpoint that guards on it is wrong in the same
way, and the HTTP matrix in ``test_organizational_queue_http.py`` would report
that once per route without ever saying which predicate failed.

The cases that matter are the negative ones — an area's coordinator is not
another area's coordinator, a lead is not a coordinator, a guest is nobody —
so those get one test each.
"""

import pytest

from plane.app.permissions.organizational_unit import (
    UNIT_COORDINATOR,
    UNIT_MEMBER,
    _has_standing,
    is_unit_coordinator,
    is_unit_member,
    is_workspace_admin,
    may_see_queue,
    unit_for_issue,
    viewer_standing,
)
from plane.db.models import IssueOrganizationalUnit, OrganizationalUnitMemberRole


@pytest.mark.unit
class TestWorkspaceAdmin:
    def test_an_admin_is_an_admin(self, workspace_with_members, admin_user):
        assert is_workspace_admin(admin_user, workspace_with_members.id) is True

    def test_a_member_is_not(self, workspace_with_members, plain_user):
        assert is_workspace_admin(plain_user, workspace_with_members.id) is False

    def test_an_admin_of_another_workspace_is_not(self, workspace_with_members, outsider_user, other_workspace):
        """Admin is per tenant. Reading it otherwise would make every tenant's admin ours."""
        assert is_workspace_admin(outsider_user, workspace_with_members.id) is False

    def test_a_deactivated_admin_is_not(self, workspace_with_members, admin_user):
        membership = workspace_with_members.workspace_member.get(member=admin_user)
        membership.is_active = False
        membership.save()

        assert is_workspace_admin(admin_user, workspace_with_members.id) is False


@pytest.mark.unit
class TestUnitCoordinator:
    def test_the_coordinator_of_the_area(self, unit, plain_user, add_coordinator):
        add_coordinator(unit, plain_user)
        assert is_unit_coordinator(plain_user, unit) is True

    def test_the_coordinator_of_another_area_is_not(self, unit, second_unit, plain_user, add_coordinator):
        """Coordination is over one area. This is the escalation the routes must refuse."""
        add_coordinator(second_unit, plain_user)
        assert is_unit_coordinator(plain_user, unit) is False

    def test_a_lead_without_coordination_is_not(self, unit, plain_user, add_member):
        """Leading an area governs its people, not its work item routing."""
        add_member(unit, plain_user, role=OrganizationalUnitMemberRole.LEAD)
        assert is_unit_coordinator(plain_user, unit) is False

    def test_an_inactive_coordination_is_not(self, unit, plain_user, add_coordinator):
        coordinator = add_coordinator(unit, plain_user)
        coordinator.is_active = False
        coordinator.save()

        assert is_unit_coordinator(plain_user, unit) is False

    def test_a_coordinator_removed_from_the_workspace_is_not(
        self, workspace_with_members, unit, plain_user, add_coordinator, workspace_member_of
    ):
        """
        Authority follows workspace membership. Reading the coordinator row on
        its own would keep a departed person handing work out.
        """
        add_coordinator(unit, plain_user)
        membership = workspace_member_of(plain_user)
        membership.is_active = False
        membership.save()

        assert is_unit_coordinator(plain_user, unit) is False

    def test_a_coordinator_need_not_belong_to_the_area(self, unit, plain_user, add_coordinator):
        add_coordinator(unit, plain_user)

        assert is_unit_coordinator(plain_user, unit) is True
        assert is_unit_member(plain_user, unit) is False


@pytest.mark.unit
class TestUnitMember:
    def test_a_member_of_the_area(self, unit, plain_user, add_member):
        add_member(unit, plain_user)
        assert is_unit_member(plain_user, unit) is True

    def test_a_member_of_another_area_is_not(self, unit, second_unit, plain_user, add_member):
        add_member(second_unit, plain_user)
        assert is_unit_member(plain_user, unit) is False

    def test_an_inactive_membership_is_not(self, unit, plain_user, add_member):
        membership = add_member(unit, plain_user)
        membership.is_active = False
        membership.save()

        assert is_unit_member(plain_user, unit) is False


@pytest.mark.unit
class TestMaySeeQueue:
    def test_a_member_may(self, unit, plain_user, add_member):
        add_member(unit, plain_user)
        assert may_see_queue(plain_user, unit) is True

    def test_a_coordinator_may(self, unit, plain_user, add_coordinator):
        add_coordinator(unit, plain_user)
        assert may_see_queue(plain_user, unit) is True

    def test_a_workspace_admin_may(self, unit, admin_user):
        assert may_see_queue(admin_user, unit) is True

    def test_a_guest_may_not(self, unit, guest_user):
        """The rows carry the titles of real work, which is not structure."""
        assert may_see_queue(guest_user, unit) is False

    def test_a_member_of_the_workspace_outside_the_area_may_not(self, unit, second_user):
        assert may_see_queue(second_user, unit) is False

    def test_a_coordinator_of_another_area_may_not(self, unit, second_unit, second_user, add_coordinator):
        add_coordinator(second_unit, second_user)
        assert may_see_queue(second_user, unit) is False


@pytest.mark.unit
class TestViewerStanding:
    def test_the_three_facts_the_interface_renders_from(self, unit, plain_user, add_member, add_coordinator):
        add_member(unit, plain_user)
        add_coordinator(unit, plain_user)

        assert viewer_standing(plain_user, unit) == {
            "is_admin": False,
            "is_coordinator": True,
            "is_member": True,
        }

    def test_an_admin_outside_the_area(self, unit, admin_user):
        assert viewer_standing(admin_user, unit) == {
            "is_admin": True,
            "is_coordinator": False,
            "is_member": False,
        }


@pytest.mark.unit
class TestStanding:
    def test_a_workspace_admin_always_passes(self, unit, admin_user):
        """An admin can already do all of this natively; refusing here shows less, not safer."""
        assert _has_standing(admin_user, unit, [UNIT_COORDINATOR]) is True

    def test_a_member_does_not_pass_a_coordinator_only_route(self, unit, plain_user, add_member):
        add_member(unit, plain_user)
        assert _has_standing(plain_user, unit, [UNIT_COORDINATOR]) is False

    def test_a_coordinator_passes_a_member_or_coordinator_route(self, unit, plain_user, add_coordinator):
        add_coordinator(unit, plain_user)
        assert _has_standing(plain_user, unit, [UNIT_MEMBER, UNIT_COORDINATOR]) is True


@pytest.mark.unit
class TestUnitForIssue:
    def test_the_area_that_owns_the_item(self, workspace_with_members, unit, project, make_issue, link_project):
        link_project(unit, project)
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(issue=issue, organizational_unit=unit)

        assert unit_for_issue(issue.id, slug=workspace_with_members.slug, project_id=project.id) == unit

    def test_none_when_no_area_owns_it(self, workspace_with_members, project, make_issue):
        issue = make_issue(project)
        assert unit_for_issue(issue.id, slug=workspace_with_members.slug, project_id=project.id) is None

    def test_none_when_the_route_names_another_project(
        self, workspace_with_members, unit, project, second_project, make_issue, link_project
    ):
        """
        The scoping is the point: an item id from elsewhere must not resolve
        through a route the caller does have access to.
        """
        link_project(unit, project)
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(issue=issue, organizational_unit=unit)

        assert unit_for_issue(issue.id, slug=workspace_with_members.slug, project_id=second_project.id) is None
