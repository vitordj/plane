# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Processes projected into Plane, and steps claiming to be finished
(items 4.2, 4.3, 4.5).

Three things are being defended here, and they are the three ways this feature
could quietly lie:

* **a retry is not a second instance.** The orchestrator redelivers; a run that
  produced two `ProcessInstanceReference` rows would show as two half-finished
  onboardings and nobody would know which was real;
* **a claim that a step is finished is not the same as the step being
  finished.** `manual` refuses outright, `automatic_with_review` stops where a
  person will see it, and every claim is recorded either way — including the
  refused ones, because a robot that keeps claiming a manual step is a fact
  worth seeing;
* **the deadlines an item started with survive.** Without `original_*`, "we
  always deliver in four hours" is unfalsifiable: every breach can be answered
  by moving the deadline, and nothing remembers that it moved.
"""

import pytest

from plane.app.services.orca import complete_step, instance_payload, project_process, record_service_level
from plane.app.services.orca.errors import CompletionManualOnly, ProcessProjectionDisabled
from plane.app.services.orca.webhook_payload import extend_issue_payload
from plane.db.models import (
    CompletionMode,
    IssueOrganizationalUnit,
    IssueServiceLevel,
    Label,
    ProcessCompletionEvent,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ProcessInstanceStatus,
    ServiceLevelSource,
    State,
    StateGroup,
)

from .conftest import ROLE_MEMBER, public_work_items_url


@pytest.fixture
def projection_on(settings):
    """The fourth switch on; it ships off (RFC §7.2)."""
    settings.ORCA_ORG_UNITS_ENABLED = True
    settings.ORCA_PROCESS_PROJECTION_ENABLED = True
    return settings


@pytest.fixture
def world(unit, project, link_project, add_member, grant_manual_access, plain_user):
    link_project(unit, project, ROLE_MEMBER)
    add_member(unit, plain_user)
    grant_manual_access(project, plain_user)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    State.objects.create(project=project, name="Done", group=StateGroup.COMPLETED.value, sequence=90000)
    return unit


def block(step="compliance.kyc", mode=CompletionMode.AUTOMATIC, instance="cliente-123", version="3"):
    return {
        "source": "espo-onboarding",
        "instance_id": instance,
        "template_name": "onboarding-cliente",
        "template_version": version,
        "step_key": step,
        "completion_mode": mode,
    }


@pytest.mark.unit
@pytest.mark.django_db
class TestProjectingAnInstance:
    def test_the_first_step_creates_the_instance(self, projection_on, world, project, make_issue):
        issue = make_issue(project)

        item = project_process(issue, block(), workspace_id=project.workspace_id)

        assert item.step_key == "compliance.kyc"
        assert item.process_instance.template_version == "3"
        assert ProcessInstanceReference.objects.count() == 1

    def test_a_second_step_of_the_same_run_joins_it(self, projection_on, world, project, make_issue):
        first = project_process(make_issue(project), block("compliance.kyc"), workspace_id=project.workspace_id)
        second = project_process(make_issue(project), block("legal.review"), workspace_id=project.workspace_id)

        assert first.process_instance_id == second.process_instance_id
        assert ProcessInstanceReference.objects.count() == 1

    def test_a_replay_of_the_same_step_finds_the_same_row(self, projection_on, world, project, make_issue):
        issue = make_issue(project)

        first = project_process(issue, block(), workspace_id=project.workspace_id)
        second = project_process(issue, block(), workspace_id=project.workspace_id)

        assert first.id == second.id
        assert ProcessInstanceItem.objects.count() == 1

    def test_the_instance_keeps_the_version_it_started_under(self, projection_on, world, project, make_issue):
        # A template that changed mid-run has instances that ran under two
        # rules; the instance records the one it actually started with.
        project_process(make_issue(project), block(version="3"), workspace_id=project.workspace_id)
        project_process(make_issue(project), block("legal.review", version="4"), workspace_id=project.workspace_id)

        assert ProcessInstanceReference.objects.get().template_version == "3"

    def test_the_switch_off_refuses(self, settings, world, project, make_issue):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = False

        with pytest.raises(ProcessProjectionDisabled):
            project_process(make_issue(project), block(), workspace_id=project.workspace_id)


@pytest.mark.unit
@pytest.mark.django_db
class TestTheServiceLevel:
    def test_the_originals_are_written_once(self, world, project, make_issue):
        from django.utils import timezone
        from datetime import timedelta

        issue = make_issue(project)
        first = timezone.now() + timedelta(hours=4)
        later = timezone.now() + timedelta(days=4)

        record_service_level(issue, completion_due_at=first, source=ServiceLevelSource.PROCESS, source_version="3")
        record_service_level(issue, completion_due_at=later, source=ServiceLevelSource.MANUAL, reason="renegotiated")

        level = IssueServiceLevel.objects.get(issue=issue)
        assert level.completion_due_at == later
        # The promise the item started with, which is the whole point of the
        # column: a breach cannot be answered by moving the deadline.
        assert level.original_completion_due_at == first
        assert level.source == ServiceLevelSource.MANUAL
        assert level.change_reason == "renegotiated"

    def test_a_caller_cannot_rewrite_the_original(self, world, project, make_issue):
        from django.utils import timezone

        issue = make_issue(project)
        record_service_level(issue, completion_due_at=timezone.now(), source=ServiceLevelSource.UNIT)
        level = IssueServiceLevel.objects.get(issue=issue)
        original = level.original_completion_due_at

        level.original_completion_due_at = timezone.now()
        level.save()

        level.refresh_from_db()
        assert level.original_completion_due_at == original


@pytest.mark.unit
@pytest.mark.django_db
class TestCompletingAStep:
    def test_an_automatic_step_moves_to_a_completed_state(self, projection_on, world, project, make_issue):
        issue = make_issue(project)
        project_process(issue, block(mode=CompletionMode.AUTOMATIC), workspace_id=project.workspace_id)

        event, item = complete_step(issue, evidence={"score": 0.98}, rule_version="r7", source="espo")

        issue.refresh_from_db()
        assert issue.state.group == StateGroup.COMPLETED.value
        assert issue.completed_at is not None
        assert event.applied is True
        assert event.evidence == {"score": 0.98}
        assert item.step_key == "compliance.kyc"

    def test_a_step_needing_review_is_labelled_and_left_open(self, projection_on, world, project, make_issue):
        issue = make_issue(project)
        project_process(issue, block(mode=CompletionMode.AUTOMATIC_WITH_REVIEW), workspace_id=project.workspace_id)

        event, _ = complete_step(issue)

        issue.refresh_from_db()
        assert issue.state.group != StateGroup.COMPLETED.value
        assert event.applied is True
        assert Label.objects.filter(project=project, name="aguardando-validacao").exists()

    def test_a_review_state_is_used_when_the_area_named_one(self, projection_on, world, project, unit, make_issue):
        from plane.db.models import OrganizationalUnitAssignmentPolicy

        review = State.objects.create(project=project, name="In review", group=StateGroup.STARTED.value)
        policy = OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit, workspace=project.workspace, review_state=review
        )
        issue = make_issue(project)
        project_process(issue, block(mode=CompletionMode.AUTOMATIC_WITH_REVIEW), workspace_id=project.workspace_id)

        complete_step(issue, policy=policy)

        issue.refresh_from_db()
        assert issue.state_id == review.id
        assert not Label.objects.filter(project=project, name="aguardando-validacao").exists()

    def test_a_manual_step_refuses_and_records_the_claim(self, projection_on, world, project, make_issue):
        issue = make_issue(project)
        project_process(issue, block(mode=CompletionMode.MANUAL), workspace_id=project.workspace_id)

        with pytest.raises(CompletionManualOnly):
            complete_step(issue, source="espo", event_id="evt-9")

        # Recorded before refusing: a robot that keeps claiming a manual step
        # is a fact worth being able to see.
        event = ProcessCompletionEvent.objects.get(issue=issue)
        assert event.applied is False
        assert event.event_id == "evt-9"

    def test_an_item_that_is_not_a_process_step_is_manual_by_default(self, projection_on, world, project, make_issue):
        issue = make_issue(project)

        with pytest.raises(CompletionManualOnly):
            complete_step(issue)

    def test_the_instance_closes_when_its_last_step_does(self, projection_on, world, project, make_issue):
        first, second = make_issue(project), make_issue(project)
        project_process(first, block("a"), workspace_id=project.workspace_id)
        project_process(second, block("b"), workspace_id=project.workspace_id)

        complete_step(first)
        assert ProcessInstanceReference.objects.get().completed_at is None

        complete_step(second)
        instance = ProcessInstanceReference.objects.get()
        assert instance.status == ProcessInstanceStatus.COMPLETED
        assert instance.completed_at is not None

    def test_the_switch_off_refuses_the_claim(self, settings, world, project, make_issue):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = False

        with pytest.raises(ProcessProjectionDisabled):
            complete_step(make_issue(project))


@pytest.mark.unit
@pytest.mark.django_db
class TestReadingAnInstance:
    def test_the_status_comes_from_the_steps_not_from_the_column(self, projection_on, world, project, unit, make_issue):
        issue = make_issue(project)
        project_process(issue, block(), workspace_id=project.workspace_id)
        IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )

        payload = instance_payload(ProcessInstanceReference.objects.get())
        assert payload["status"] == ProcessInstanceStatus.RUNNING
        assert payload["steps"][0]["unit"] == unit.slug
        assert payload["steps"][0]["routing_state"] == "queued"

        # Closed in the interface, not by the orchestrator: the instance is
        # finished all the same.
        issue.state = State.objects.get(project=project, name="Done")
        issue.save()

        assert instance_payload(ProcessInstanceReference.objects.get())["status"] == ProcessInstanceStatus.COMPLETED


@pytest.mark.unit
@pytest.mark.django_db
class TestTheWebhookPayload:
    def test_an_item_with_an_area_carries_it(self, projection_on, world, project, unit, make_issue):
        issue = make_issue(project)
        IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )
        project_process(issue, block(), workspace_id=project.workspace_id)

        payload = extend_issue_payload({"id": str(issue.id)}, issue.id)

        assert payload["orca"]["unit_slug"] == unit.slug
        assert payload["orca"]["routing_state"] == "queued"
        assert payload["orca"]["process"]["step_key"] == "compliance.kyc"
        assert payload["orca"]["process"]["instance_id"] == "cliente-123"

    def test_an_item_with_no_area_is_left_exactly_as_it_was(self, world, project, make_issue):
        issue = make_issue(project)

        payload = extend_issue_payload({"id": str(issue.id)}, issue.id)

        # A workspace not using areas sees the payload it saw before this
        # existed — not one with an empty `orca` key in it.
        assert payload == {"id": str(issue.id)}


@pytest.mark.unit
@pytest.mark.django_db
class TestOverHttp:
    @pytest.fixture
    def caller(self, world, admin_user, project, grant_manual_access, token_client):
        grant_manual_access(project, admin_user)
        return token_client(admin_user)

    def body(self, **overrides):
        payload = {
            "external": {"source": "espo-onboarding", "id": "cliente-1"},
            "work_item": {"name": "Validate registration documents"},
            "responsibility": {"unit": "compliance", "assignment": {"mode": "default"}},
        }
        payload.update(overrides)
        return payload

    def test_a_process_block_is_refused_while_the_projection_is_off(self, caller, project, world):
        response = caller.post(
            public_work_items_url(project.workspace.slug, project.id),
            self.body(process=block()),
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-1",
        )

        assert response.status_code == 400
        assert response.data["error_message"] == "ORG_PROCESS_PROJECTION_DISABLED"

    def test_a_process_block_is_projected_when_it_is_on(self, projection_on, caller, project, world):
        response = caller.post(
            public_work_items_url(project.workspace.slug, project.id),
            self.body(process=block()),
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-2",
        )

        assert response.status_code == 201, response.data
        item = ProcessInstanceItem.objects.get()
        assert str(item.issue_id) == response.data["work_item"]["id"]
        assert item.process_instance.external_instance_id == "cliente-123"

    def test_a_completion_deadline_is_refused_while_the_projection_is_off(self, caller, project, world):
        response = caller.post(
            public_work_items_url(project.workspace.slug, project.id),
            self.body(
                responsibility={
                    "unit": "compliance",
                    "assignment": {"mode": "default"},
                    "completion_due_at": "2026-09-30T12:00:00Z",
                }
            ),
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-3",
        )

        assert response.status_code == 400

    def test_a_completion_deadline_is_stored_when_it_is_on(self, projection_on, caller, project, world):
        response = caller.post(
            public_work_items_url(project.workspace.slug, project.id),
            self.body(
                responsibility={
                    "unit": "compliance",
                    "assignment": {"mode": "default"},
                    "completion_due_at": "2026-09-30T12:00:00Z",
                }
            ),
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-4",
        )

        assert response.status_code == 201, response.data
        level = IssueServiceLevel.objects.get(issue_id=response.data["work_item"]["id"])
        assert level.completion_due_at is not None
        assert level.original_completion_due_at == level.completion_due_at

    def test_completing_a_step_over_http(self, projection_on, caller, project, world):
        created = caller.post(
            public_work_items_url(project.workspace.slug, project.id),
            self.body(process=block(mode=CompletionMode.AUTOMATIC)),
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-5",
        )
        issue_id = created.data["work_item"]["id"]

        response = caller.post(
            f"{public_work_items_url(project.workspace.slug, project.id)}{issue_id}/complete/",
            {"evidence": {"ok": True}, "rule_version": "r1", "source": "espo", "event_id": "evt-1"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-6",
        )

        assert response.status_code == 200, response.data
        assert response.data["completion"]["applied"] is True
        assert response.data["completion"]["mode"] == CompletionMode.AUTOMATIC

    def test_a_replayed_completion_answers_the_first_result(self, projection_on, caller, project, world):
        created = caller.post(
            public_work_items_url(project.workspace.slug, project.id),
            self.body(process=block(mode=CompletionMode.AUTOMATIC)),
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-7",
        )
        issue_id = created.data["work_item"]["id"]
        url = f"{public_work_items_url(project.workspace.slug, project.id)}{issue_id}/complete/"
        payload = {"evidence": {}, "rule_version": "r1", "source": "espo", "event_id": "evt-2"}

        first = caller.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="k-8")
        second = caller.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="k-8")

        assert first.status_code == 200
        assert second.data["operation"]["replay"] is True
        # One claim, not two: the replay answered from the receipt.
        assert ProcessCompletionEvent.objects.filter(issue_id=issue_id).count() == 1

    def test_reading_the_instance_back(self, projection_on, caller, project, world):
        caller.post(
            public_work_items_url(project.workspace.slug, project.id),
            self.body(process=block()),
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-9",
        )

        response = caller.get(
            f"/api/v1/orca/workspaces/{project.workspace.slug}/process-instances/espo-onboarding/cliente-123/"
        )

        assert response.status_code == 200, response.data
        assert response.data["template"]["version"] == "3"
        assert [step["step_key"] for step in response.data["steps"]] == ["compliance.kyc"]

    def test_an_instance_that_does_not_exist_is_a_404(self, projection_on, caller, project, world):
        response = caller.get(
            f"/api/v1/orca/workspaces/{project.workspace.slug}/process-instances/espo-onboarding/nope/"
        )

        assert response.status_code == 404
