# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
An area may only own work in the projects it covers (invariant I2).

Access flows the other way round: a person is a project member *because* their
area is linked to that project. So an area marked responsible for work in a
project it does not cover is asking for something the access model cannot
give — either nobody is eligible, or the work lands on someone who then cannot
see it. The engine used to hide this by adding the project to the area's own
list before ranking, which is exactly the second case.
"""

import pytest
from django.utils import timezone
from rest_framework import status

from plane.app.services.orca import candidates_for, unit_covers_project
from plane.utils.orca_error_codes import ORCA_ERROR_CODES

from .conftest import issue_assign_url, issue_unit_url


@pytest.mark.unit
@pytest.mark.django_db
class TestTheCoverageRule:
    def test_a_linked_project_is_covered(self, unit, project, link_project):
        link_project(unit, project)

        assert unit_covers_project(unit, project.id) is True

    def test_an_unlinked_project_is_not(self, unit, project):
        assert unit_covers_project(unit, project.id) is False

    def test_an_inactive_area_covers_nothing(self, unit, project, link_project):
        link_project(unit, project)
        unit.is_active = False
        unit.save(update_fields=["is_active"])

        assert unit_covers_project(unit, project.id) is False

    def test_an_archived_project_is_not_covered(self, unit, project, link_project):
        link_project(unit, project)
        project.archived_at = timezone.now()
        project.save(update_fields=["archived_at"])

        assert unit_covers_project(unit, project.id) is False


@pytest.mark.unit
@pytest.mark.django_db
class TestSettingTheResponsibleArea:
    def test_i2_an_area_covering_the_project_is_accepted(
        self, admin_client, workspace_with_members, unit, project, link_project, make_issue
    ):
        link_project(unit, project)
        issue = make_issue(project)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data

    def test_i2_unit_not_covering_project_rejected(
        self, admin_client, workspace_with_members, unit, project, make_issue
    ):
        """The defect: this used to be accepted, and then assigned to someone."""
        issue = make_issue(project)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]
        assert response.data["error_message"] == "ORG_UNIT_NOT_COVERING_PROJECT"

    def test_an_inactive_area_is_rejected(
        self, admin_client, workspace_with_members, unit, project, link_project, make_issue
    ):
        link_project(unit, project)
        unit.is_active = False
        unit.save(update_fields=["is_active"])
        issue = make_issue(project)

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]

    def test_an_archived_project_is_rejected(
        self, admin_client, workspace_with_members, unit, project, link_project, make_issue
    ):
        link_project(unit, project)
        issue = make_issue(project)
        project.archived_at = timezone.now()
        project.save(update_fields=["archived_at"])

        response = admin_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]


@pytest.mark.unit
@pytest.mark.django_db
class TestAssigningFromAnAreaThatDoesNotCover:
    def test_the_assign_endpoint_refuses(
        self, admin_client, workspace_with_members, unit, project, link_project, make_issue, add_member, plain_user
    ):
        # Linked, area marked responsible, then the link is removed: the work
        # item keeps its area, so the assign path has to check coverage too.
        link = link_project(unit, project)
        add_member(unit, plain_user)
        issue = make_issue(project)
        admin_client.post(
            issue_unit_url(workspace_with_members.slug, project.id, issue.id),
            {"organizational_unit_id": str(unit.id)},
            format="json",
        )
        link.delete()

        response = admin_client.post(
            issue_assign_url(workspace_with_members.slug, project.id, issue.id), {}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_COVERING_PROJECT"]

    def test_the_engine_finds_nobody(self, unit, project, add_member, plain_user):
        """
        No candidates rather than a candidate with no access: the engine used
        to append the project to the area's list and rank as if it were covered.
        """
        add_member(unit, plain_user)

        assert candidates_for(unit, project.id) == []
