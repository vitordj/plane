# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Native issue webhooks carry the Orca sidecar (item 4.5).

Creation through ``/api/v1/orca/`` already queues ``model_activity`` after
commit — that is the same path a person creating a work item in the UI
takes, so the orchestrator can subscribe to ordinary ``issue`` webhooks.
What this file pins is the body those webhooks grow: ``external_source`` /
``external_id`` (native columns) and ``orca: {unit_slug, routing_state,
primary_executor}`` (sidecar, because the area is not a column on ``Issue``).
"""

import pytest

from plane.app.services.orca.webhook_payload import attach_orca_issue_sidecar
from plane.bgtasks.webhook_task import get_model_data
from plane.db.models import IssueOrganizationalUnit, QueueReason, RoutingState


@pytest.mark.unit
@pytest.mark.django_db
class TestTheOrcaWebhookSidecar:
    def test_an_item_with_an_area_names_the_area(self, workspace_with_members, project, unit, make_issue, link_project):
        link_project(unit, project)
        issue = make_issue(project, name="KYC")
        issue.external_source = "espo-onboarding"
        issue.external_id = "cliente-1:kyc"
        issue.save(update_fields=["external_source", "external_id"])
        IssueOrganizationalUnit.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            routing_state=RoutingState.QUEUED,
            queue_reason=QueueReason.NEW_ITEM,
            queued_at=issue.created_at,
        )

        data = get_model_data("issue", str(issue.id))

        assert data["external_source"] == "espo-onboarding"
        assert data["external_id"] == "cliente-1:kyc"
        assert data["orca"] == {
            "unit_slug": unit.slug,
            "routing_state": RoutingState.QUEUED,
            "primary_executor": None,
        }

    def test_an_item_with_no_area_has_a_null_sidecar(self, project, make_issue):
        issue = make_issue(project)

        data = get_model_data("issue", str(issue.id))

        assert data["orca"] is None

    def test_the_native_fields_are_copied_not_replaced(self, project, make_issue):
        issue = make_issue(project, name="Keep me")

        data = attach_orca_issue_sidecar({"id": str(issue.id), "name": "Keep me"})

        assert data["name"] == "Keep me"
        assert data["id"] == str(issue.id)
        assert "orca" in data
