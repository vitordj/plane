# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Runs of a process, projected into Plane.

The template lives outside the product, so what is pinned here is the join
between the two: an orchestrator that restarts halfway through a run has to be
able to reconnect to it, and a step it closes has to be closable only in the
way the template said.
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    IssueServiceLevel,
    Label,
    ProcessCompletionEvent,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ProjectMember,
    State,
    StateGroup,
)

from .conftest import ROLE_ADMIN, ROLE_MEMBER


@pytest.fixture(autouse=True)
def process_api_on(settings):
    settings.ORCA_PUBLIC_API_ENABLED = True
    settings.ORCA_PROCESS_PROJECTION_ENABLED = True


@pytest.fixture
def covered_unit(unit, project, link_project):
    link_project(unit, project)
    return unit


@pytest.fixture
def backlog_state(project, workspace_with_members):
    return State.objects.create(
        name="Backlog",
        group=StateGroup.UNSTARTED.value,
        project=project,
        workspace=workspace_with_members,
        sequence=1,
    )


@pytest.fixture
def done_state(project, workspace_with_members):
    return State.objects.create(
        name="Done",
        group=StateGroup.COMPLETED.value,
        project=project,
        workspace=workspace_with_members,
        sequence=9,
    )


@pytest.fixture
def token_client(admin_user, workspace_with_members, project):
    ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )
    token = APIToken.objects.create(user=admin_user, workspace=workspace_with_members, label="orchestrator")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


@pytest.fixture
def executor(covered_unit, project, workspace_with_members, add_member, plain_user):
    add_member(covered_unit, plain_user)
    ProjectMember.objects.create(
        project=project, member=plain_user, workspace=workspace_with_members, role=ROLE_MEMBER, is_active=True
    )
    return plain_user


@pytest.fixture
def work_items_url(workspace_with_members, project):
    return f"/api/v1/orca/workspaces/{workspace_with_members.slug}/projects/{project.id}/work-items/"


def step_payload(unit_slug, step_key, *, instance="client-1", mode="manual", version="3", **extra):
    body = {
        "external": {"source": "espo", "id": f"{instance}:{step_key}"},
        "work_item": {"name": f"Step {step_key}"},
        "responsibility": {"unit": unit_slug, "assignment": {"mode": "default"}},
        "process": {
            "source": "espo-onboarding",
            "instance_id": instance,
            "template_name": "onboarding",
            "template_version": version,
            "step_key": step_key,
            "completion_mode": mode,
        },
    }
    body["responsibility"].update(extra)
    return body


def post(client, url, body, key):
    return client.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY=key)


@pytest.mark.unit
@pytest.mark.django_db
class TestBuildingAnInstance:
    def test_four_steps_make_one_run(
        self, token_client, work_items_url, covered_unit, backlog_state, executor, workspace_with_members
    ):
        for index in range(4):
            response = post(
                token_client,
                work_items_url,
                step_payload(covered_unit.slug, f"step-{index}"),
                key=f"espo-onboarding:client-1:step-{index}:evt-1",
            )
            assert response.status_code == status.HTTP_201_CREATED, response.data

        instance = ProcessInstanceReference.objects.get(external_instance_id="client-1")
        assert ProcessInstanceItem.objects.filter(process_instance=instance).count() == 4
        assert instance.template_version == "3"
        assert instance.status == "running"

    def test_the_response_says_which_step_it_is(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        response = post(token_client, work_items_url, step_payload(covered_unit.slug, "kyc"), key="k1")

        assert response.data["process"]["step_key"] == "kyc"
        assert response.data["process"]["template_version"] == "3"

    def test_replaying_the_same_event_does_not_add_a_step(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        body = step_payload(covered_unit.slug, "kyc")
        post(token_client, work_items_url, body, key="same-key")

        response = post(token_client, work_items_url, body, key="same-key")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["operation"]["replay"] is True
        assert ProcessInstanceItem.objects.count() == 1

    def test_the_template_version_is_required(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        """A run whose steps were made under two template versions happens; the
        only way to find out later is if every step says which one made it."""
        body = step_payload(covered_unit.slug, "kyc")
        del body["process"]["template_version"]

        response = post(token_client, work_items_url, body, key="k1")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_block_is_refused_while_projection_is_off(
        self, token_client, work_items_url, covered_unit, backlog_state, executor, settings
    ):
        """Refused, not ignored: an automation that thought it was building a
        process and got four unrelated work items is worse than an error."""
        settings.ORCA_PROCESS_PROJECTION_ENABLED = False

        response = post(token_client, work_items_url, step_payload(covered_unit.slug, "kyc"), key="k1")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4929

    def test_the_process_dates_land_in_the_service_level(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        response = post(
            token_client,
            work_items_url,
            step_payload(covered_unit.slug, "kyc", completion_due_at="2026-10-01T12:00:00Z"),
            key="k1",
        )

        service_level = IssueServiceLevel.objects.get(issue_id=response.data["work_item"]["id"])
        assert service_level.source == "process"
        assert service_level.source_version == "3"
        assert service_level.completion_due_at.isoformat().startswith("2026-10-01T12:00")
        assert service_level.original_completion_due_at == service_level.completion_due_at

    def test_the_original_dates_never_move(self, token_client, work_items_url, covered_unit, backlog_state, executor):
        """The gap between the original and the current is the only thing that
        makes a report about lateness mean anything."""
        response = post(
            token_client,
            work_items_url,
            step_payload(covered_unit.slug, "kyc", completion_due_at="2026-10-01T12:00:00Z"),
            key="k1",
        )
        service_level = IssueServiceLevel.objects.get(issue_id=response.data["work_item"]["id"])
        first = service_level.original_completion_due_at

        service_level.completion_due_at = "2026-12-25T12:00:00Z"
        service_level.original_completion_due_at = "2026-12-25T12:00:00Z"
        service_level.save()
        service_level.refresh_from_db()

        assert service_level.original_completion_due_at == first
        assert service_level.completion_due_at.isoformat().startswith("2026-12-25")


@pytest.mark.unit
@pytest.mark.django_db
class TestClosingAStep:
    def complete_url(self, workspace, project, issue_id):
        return f"/api/v1/orca/workspaces/{workspace.slug}/projects/{project.id}/work-items/{issue_id}/complete/"

    def _step(self, token_client, work_items_url, unit_slug, mode, key="k1"):
        response = post(token_client, work_items_url, step_payload(unit_slug, "kyc", mode=mode), key=key)
        assert response.status_code == status.HTTP_201_CREATED, response.data
        return response.data["work_item"]["id"]

    def test_automatic_moves_it_to_done(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(token_client, work_items_url, covered_unit.slug, "automatic")

        response = token_client.post(
            self.complete_url(workspace_with_members, project, issue_id),
            {"source": "espo", "event_id": "evt-9", "rule_version": "1", "evidence": {"doc": "ok"}},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["work_item"]["state"] == str(done_state.id)
        assert response.data["process"]["progress"] == {"done": 1, "total": 1}

    def test_automatic_with_review_does_not_close_it(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        """Flagging for review and closing are the two things this mode exists
        to keep apart."""
        issue_id = self._step(token_client, work_items_url, covered_unit.slug, "automatic_with_review")

        response = token_client.post(
            self.complete_url(workspace_with_members, project, issue_id),
            {"source": "espo", "event_id": "evt-9"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["work_item"]["state"] == str(backlog_state.id)
        assert Label.objects.filter(project=project, name="aguardando-validacao").exists()

    def test_a_review_state_is_used_when_the_area_has_one(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        from plane.db.models import OrganizationalUnitAssignmentPolicy

        review_state = State.objects.create(
            name="In review",
            group=StateGroup.STARTED.value,
            project=project,
            workspace=workspace_with_members,
            sequence=5,
        )
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="manual",
            allowed_modes=["manual"],
            review_state=review_state,
        )
        issue_id = self._step(token_client, work_items_url, covered_unit.slug, "automatic_with_review")

        response = token_client.post(
            self.complete_url(workspace_with_members, project, issue_id),
            {"source": "espo"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.data["work_item"]["state"] == str(review_state.id)

    def test_manual_is_refused(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        """A step whose template says a person decides is not something an API
        key gets to decide instead."""
        issue_id = self._step(token_client, work_items_url, covered_unit.slug, "manual")

        response = token_client.post(
            self.complete_url(workspace_with_members, project, issue_id),
            {"source": "espo"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == 4921

    def test_the_same_event_twice_records_one_completion(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(token_client, work_items_url, covered_unit.slug, "automatic")
        url = self.complete_url(workspace_with_members, project, issue_id)
        body = {"source": "espo", "event_id": "evt-9"}

        token_client.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY="complete-1")
        token_client.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY="complete-2")

        assert ProcessCompletionEvent.objects.filter(issue_id=issue_id).count() == 1

    def test_the_evidence_is_kept(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        """The first time somebody disputes an automatic closure, the answer
        has to be the event, not a recollection."""
        issue_id = self._step(token_client, work_items_url, covered_unit.slug, "automatic")

        token_client.post(
            self.complete_url(workspace_with_members, project, issue_id),
            {"source": "espo", "rule_version": "2.1", "evidence": {"document": "kyc.pdf", "checked_by": "ocr"}},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        event = ProcessCompletionEvent.objects.get(issue_id=issue_id)
        assert event.rule_version == "2.1"
        assert event.evidence == {"document": "kyc.pdf", "checked_by": "ocr"}
        assert event.mode == "automatic"

    def test_a_completion_event_cannot_be_rewritten(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(token_client, work_items_url, covered_unit.slug, "automatic")
        token_client.post(
            self.complete_url(workspace_with_members, project, issue_id),
            {"source": "espo", "evidence": {"doc": "ok"}},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )
        event = ProcessCompletionEvent.objects.get(issue_id=issue_id)

        event.evidence = {"doc": "actually not"}
        with pytest.raises(ValueError):
            event.save()

    def test_closing_the_last_step_finishes_the_run(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        first = post(
            token_client, work_items_url, step_payload(covered_unit.slug, "a", mode="automatic"), key="k1"
        ).data["work_item"]["id"]
        second = post(
            token_client, work_items_url, step_payload(covered_unit.slug, "b", mode="automatic"), key="k2"
        ).data["work_item"]["id"]

        token_client.post(
            self.complete_url(workspace_with_members, project, first),
            {"source": "espo"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="c1",
        )
        instance = ProcessInstanceReference.objects.get(external_instance_id="client-1")
        assert instance.status == "running"

        token_client.post(
            self.complete_url(workspace_with_members, project, second),
            {"source": "espo"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="c2",
        )

        instance.refresh_from_db()
        assert instance.status == "completed"
        assert instance.completed_at is not None


@pytest.mark.unit
@pytest.mark.django_db
class TestReadingAnInstance:
    def instance_url(self, workspace, instance="client-1"):
        return f"/api/v1/orca/workspaces/{workspace.slug}/process-instances/espo-onboarding/{instance}/"

    def test_it_reports_every_step_with_its_state(
        self, token_client, work_items_url, workspace_with_members, covered_unit, backlog_state, executor
    ):
        for index in range(3):
            post(token_client, work_items_url, step_payload(covered_unit.slug, f"s{index}"), key=f"k{index}")

        response = token_client.get(self.instance_url(workspace_with_members))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert len(response.data["items"]) == 3
        assert response.data["instance"]["progress"] == {"done": 0, "total": 3}
        assert response.data["items"][0]["responsibility"]["unit"] == covered_unit.slug

    def test_it_follows_a_state_changed_in_the_app(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        """No `complete/` call ever arrives when somebody drags the card. The
        run has to read as finished anyway — the app is allowed to be right."""
        from plane.db.models import Issue

        issue_id = post(token_client, work_items_url, step_payload(covered_unit.slug, "only"), key="k1").data[
            "work_item"
        ]["id"]
        issue = Issue.objects.get(pk=issue_id)
        issue.state = done_state
        issue.save()

        response = token_client.get(self.instance_url(workspace_with_members))

        assert response.data["instance"]["status"] == "completed"
        assert response.data["instance"]["progress"] == {"done": 1, "total": 1}

    def test_an_unknown_run_is_a_404(self, token_client, workspace_with_members):
        response = token_client.get(self.instance_url(workspace_with_members, instance="nobody"))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_route_is_gone_while_projection_is_off(self, token_client, workspace_with_members, settings):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = False

        response = token_client.get(self.instance_url(workspace_with_members))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4929


@pytest.mark.unit
@pytest.mark.django_db
class TestReplayingAWholeRun:
    """
    Phase 4's closing check, and the one the orchestrator's contract turns on:
    an orchestrator that dies half-way through a run and restarts must be able
    to replay the whole template and end up with the run it was building — not
    a second one beside it.
    """

    def _send(self, token_client, work_items_url, unit_slug, steps, instance="client-1"):
        responses = []
        for step in steps:
            responses.append(
                post(
                    token_client,
                    work_items_url,
                    step_payload(unit_slug, step, instance=instance, mode="automatic"),
                    key=f"espo-onboarding:{instance}:{step}:evt-1",
                )
            )
        return responses

    def test_twenty_events_twice_leave_the_same_counts(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        steps = [f"step-{index}" for index in range(20)]

        self._send(token_client, work_items_url, covered_unit.slug, steps)
        first = {
            "issues": ProcessInstanceItem.objects.count(),
            "instances": ProcessInstanceReference.objects.count(),
        }
        replays = self._send(token_client, work_items_url, covered_unit.slug, steps)

        assert first == {"issues": 20, "instances": 1}
        assert ProcessInstanceItem.objects.count() == 20
        assert ProcessInstanceReference.objects.count() == 1
        assert all(response.data["operation"]["replay"] is True for response in replays)

    def test_a_failure_at_step_three_of_four_is_completed_by_a_replay(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        """
        The restart case, in the small. Steps one and two land, the
        orchestrator dies, and the replay of the whole template creates only
        what is genuinely missing.
        """
        self._send(token_client, work_items_url, covered_unit.slug, ["a", "b"])
        assert ProcessInstanceItem.objects.count() == 2

        self._send(token_client, work_items_url, covered_unit.slug, ["a", "b", "c", "d"])

        instance = ProcessInstanceReference.objects.get(external_instance_id="client-1")
        assert ProcessInstanceItem.objects.filter(process_instance=instance).count() == 4
        assert ProcessInstanceReference.objects.count() == 1

    def test_a_replay_does_not_undo_a_person_moving_the_work(
        self,
        token_client,
        work_items_url,
        workspace_with_members,
        covered_unit,
        project,
        backlog_state,
        executor,
        second_user,
        add_member,
        grant_manual_access,
    ):
        """
        The rule the whole idempotency design exists for: an automation
        retrying must never overwrite a decision a person made in between.
        """
        from plane.app.services.orca import reassign
        from plane.db.models import IssueOrganizationalUnit

        add_member(covered_unit, second_user)
        grant_manual_access(project, second_user)
        [response] = self._send(token_client, work_items_url, covered_unit.slug, ["only"])
        issue_id = response.data["work_item"]["id"]
        from plane.db.models import Issue

        reassign(Issue.objects.get(pk=issue_id), second_user.id, actor=second_user)

        self._send(token_client, work_items_url, covered_unit.slug, ["only"])

        link = IssueOrganizationalUnit.objects.get(issue_id=issue_id)
        assert link.primary_executor_id == second_user.id

    def test_a_step_cannot_belong_to_two_runs(
        self, token_client, work_items_url, covered_unit, backlog_state, executor
    ):
        """Whichever answer "is this done?" gave would be wrong for the other."""
        body = step_payload(covered_unit.slug, "kyc", instance="client-1")
        post(token_client, work_items_url, body, key="k1")

        second = step_payload(covered_unit.slug, "kyc", instance="client-2")
        # Same external reference, so it finds the same work item — and that
        # work item is already a step of the first run.
        second["external"]["id"] = body["external"]["id"]
        response = post(token_client, work_items_url, second, key="k2")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert ProcessInstanceItem.objects.count() == 1
