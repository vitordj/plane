# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
An area may only own work in a project it covers (defect D1, RFC §2.2).

An area grants project access through ``OrganizationalUnitProject``: those
links are what the reconciler turns into native ``ProjectMember`` rows. So
naming an area responsible for a work item in a project it does not link is
not a harmless label — it names a group whose members have no access there.
Nothing checked it: the endpoint compared workspaces, the interface offered
every active area, and the engine added the target project to the area's own
list when it was missing, which turned "not covered" into "covered" and
counted foreign work toward its members' load.

These tests pin the rule at all three layers.
"""

import pytest

from plane.app.services.orca import assign_from_unit, candidates_for, unit_covers_project
from plane.app.serializers import OrganizationalUnitSerializer
from plane.db.models import IssueOrganizationalUnit, ProjectMember
from plane.utils.orca_error_codes import ORCA_ERROR_CODES

from .conftest import ROLE_ADMIN, ROLE_MEMBER, issue_assign_url, issue_unit_url, units_url

NOT_COVERING = ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]


@pytest.fixture
def project_with_admin(project, workspace_with_members, admin_user):
    """These routes are project-scoped, so the admin must be a project member."""
    ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )
    return project


@pytest.mark.unit
class TestTheCoverageRule:
    def test_a_linked_project_is_covered(self, unit, project, link_project):
        link_project(unit, project, ROLE_MEMBER)

        assert unit_covers_project(unit, project.id) is True

    def test_an_unlinked_project_is_not_covered(self, unit, project):
        assert unit_covers_project(unit, project.id) is False

    def test_a_link_to_another_project_does_not_cover_this_one(self, unit, project, second_project, link_project):
        link_project(unit, second_project, ROLE_MEMBER)

        assert unit_covers_project(unit, project.id) is False

    def test_an_inactive_area_covers_nothing(self, unit, project, link_project):
        """
        An inactive area is one the workspace has stood down; the reconciler
        stops granting access through it, so it cannot own work either.
        """
        link_project(unit, project, ROLE_MEMBER)
        unit.is_active = False
        unit.save()

        assert unit_covers_project(unit, project.id) is False

    def test_an_archived_project_is_not_covered(self, unit, project, link_project):
        """An archived project grants nothing, link or no link."""
        from django.utils import timezone

        link_project(unit, project, ROLE_MEMBER)
        project.archived_at = timezone.now()
        project.save()

        assert unit_covers_project(unit, project.id) is False

    def test_a_removed_link_stops_covering(self, unit, project, link_project):
        """Soft-deleted links are excluded by the default manager."""
        link = link_project(unit, project, ROLE_MEMBER)
        link.delete()

        assert unit_covers_project(unit, project.id) is False

    @pytest.mark.parametrize("missing", ["unit", "project"])
    def test_nothing_is_covered_by_nothing(self, unit, project, missing):
        if missing == "unit":
            assert unit_covers_project(None, project.id) is False
        else:
            assert unit_covers_project(unit, None) is False


@pytest.mark.unit
class TestTheEndpointRefuses:
    def test_an_area_that_does_not_cover_the_project_cannot_be_made_responsible(
        self, admin_client, workspace_with_members, project_with_admin, unit, make_issue
    ):
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error_code"] == NOT_COVERING
        assert not IssueOrganizationalUnit.objects.filter(issue=issue).exists()

    def test_an_inactive_area_cannot_be_made_responsible(
        self, admin_client, workspace_with_members, project_with_admin, unit, link_project, make_issue
    ):
        link_project(unit, project_with_admin, ROLE_MEMBER)
        unit.is_active = False
        unit.save()
        issue = make_issue(project_with_admin)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error_code"] == NOT_COVERING

    def test_assignment_refuses_when_the_project_stopped_being_covered(
        self,
        admin_client,
        workspace_with_members,
        project_with_admin,
        unit,
        link_project,
        add_member,
        plain_user,
        make_issue,
    ):
        """
        The link can go away after the work item was marked as the area's — by
        unlinking the project or archiving it — so the assign route checks
        again rather than trusting the link it finds.
        """
        link = link_project(unit, project_with_admin, ROLE_MEMBER)
        add_member(unit, plain_user)
        issue = make_issue(project_with_admin)
        admin_client.post(
            issue_unit_url(workspace_with_members.slug, project_with_admin.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )
        link.delete()

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project_with_admin.id, issue.id), {}, format="json"
        )

        assert response.status_code == 400
        assert response.data["error_code"] == NOT_COVERING


@pytest.mark.unit
class TestTheEngineFindsNobody:
    def test_an_uncovered_project_has_no_candidates(self, unit, project, add_member, plain_user, grant_manual_access):
        """
        Even someone with project access through another route is not a
        candidate here: the area does not own work in this project, so it has
        nobody to offer for it.
        """
        add_member(unit, plain_user)
        grant_manual_access(project, plain_user)

        assert candidates_for(unit, project.id) == []

    def test_assignment_reports_no_eligible_member(
        self, unit, project, add_member, plain_user, grant_manual_access, make_issue
    ):
        add_member(unit, plain_user)
        grant_manual_access(project, plain_user)
        issue = make_issue(project)

        chosen, reason = assign_from_unit(issue, unit)

        assert chosen is None
        assert reason == "no_eligible_member"

    def test_work_in_an_uncovered_project_stops_counting_toward_load(
        self,
        unit,
        project,
        second_project,
        link_project,
        add_member,
        plain_user,
        second_user,
        grant_manual_access,
        make_issue,
    ):
        """
        The engine used to add the target project to the area's list, so work
        from a project the area does not own inflated a member's load and
        pushed them down the ranking. With coverage required, load is measured
        over the area's own projects only.
        """
        from plane.db.models import IssueAssignee

        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, plain_user)
        add_member(unit, second_user)
        grant_manual_access(project, plain_user)
        grant_manual_access(project, second_user)
        grant_manual_access(second_project, plain_user)

        # Three items for plain_user, all in a project the area does not cover.
        for index in range(3):
            foreign = make_issue(second_project, name=f"Foreign {index}")
            IssueAssignee.objects.create(
                issue=foreign, assignee=plain_user, project=second_project, workspace=foreign.workspace
            )

        ranked = candidates_for(unit, project.id)

        assert [candidate.open_issues for candidate in ranked] == [0, 0]


@pytest.mark.unit
class TestTheSerializerPublishesCoverage:
    def test_it_lists_the_covered_projects(self, unit, project, second_project, link_project):
        link_project(unit, project, ROLE_MEMBER)
        link_project(unit, second_project, ROLE_MEMBER)

        project_ids = OrganizationalUnitSerializer(unit).data["project_ids"]

        assert sorted(project_ids) == sorted([str(project.id), str(second_project.id)])

    def test_an_archived_project_is_left_out(self, unit, project, second_project, link_project):
        """The interface filters on this list, so it has to match the API's rule."""
        from django.utils import timezone

        link_project(unit, project, ROLE_MEMBER)
        link_project(unit, second_project, ROLE_MEMBER)
        second_project.archived_at = timezone.now()
        second_project.save()

        assert OrganizationalUnitSerializer(unit).data["project_ids"] == [str(project.id)]

    def test_the_list_endpoint_carries_it(self, admin_client, workspace_with_members, unit, project, link_project):
        link_project(unit, project, ROLE_MEMBER)

        response = admin_client.get(units_url(workspace_with_members.slug))

        assert response.status_code == 200
        listed = next(item for item in response.data if item["id"] == str(unit.id))
        assert listed["project_ids"] == [str(project.id)]
