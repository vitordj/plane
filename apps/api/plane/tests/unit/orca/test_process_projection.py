# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Runs of a process, projected into Plane (items 4.2 and 4.3).

The template lives outside the product, so what is pinned here is the join
between the two: an orchestrator that restarts halfway through a run has to be
able to reconnect to it, and a step it closes has to be closable only in the
way the template said.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from plane.app.services.orca import set_responsibility
from plane.app.services.orca.service_level import record_service_level
from plane.db.models import (
    IssueServiceLevel,
    Label,
    OrganizationalUnitAssignmentPolicy,
    ProcessCompletionEvent,
    ProcessInstanceItem,
    ProcessInstanceReference,
    ResponsibilitySource,
    State,
    StateGroup,
)

from .conftest import (
    public_complete_url,
    public_process_instance_url,
    public_work_items_url,
)


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
        default=True,
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
def caller(admin_user, workspace_with_members, project, grant_manual_access, token_client):
    grant_manual_access(project, admin_user)
    return token_client(admin_user, workspace=workspace_with_members)


@pytest.fixture
def executor(covered_unit, project, workspace_with_members, add_member, grant_manual_access, plain_user):
    add_member(covered_unit, plain_user)
    grant_manual_access(project, plain_user)
    return plain_user


def work_items_url(workspace, project):
    return public_work_items_url(workspace.slug, project.id)


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
class TestTheProjectionTables:
    def test_a_run_is_unique_in_its_workspace(self, workspace_with_members):
        ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="espo",
            external_instance_id="c-1",
            template_name="onboarding",
            template_version="3",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            ProcessInstanceReference.objects.create(
                workspace=workspace_with_members,
                external_source="espo",
                external_instance_id="c-1",
                template_name="onboarding",
                template_version="3",
            )

    def test_a_work_item_is_a_step_of_at_most_one_run(self, workspace_with_members, project, make_issue):
        instance = ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="espo",
            external_instance_id="c-1",
            template_name="onboarding",
            template_version="3",
        )
        issue = make_issue(project)
        ProcessInstanceItem.objects.create(
            process_instance=instance,
            issue=issue,
            workspace=workspace_with_members,
            step_key="kyc",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            ProcessInstanceItem.objects.create(
                process_instance=instance,
                issue=issue,
                workspace=workspace_with_members,
                step_key="interview",
            )

    def test_a_step_key_is_unique_inside_a_run(self, workspace_with_members, project, make_issue):
        instance = ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="espo",
            external_instance_id="c-1",
            template_name="onboarding",
            template_version="3",
        )
        ProcessInstanceItem.objects.create(
            process_instance=instance,
            issue=make_issue(project, name="first"),
            workspace=workspace_with_members,
            step_key="kyc",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            ProcessInstanceItem.objects.create(
                process_instance=instance,
                issue=make_issue(project, name="second"),
                workspace=workspace_with_members,
                step_key="kyc",
            )

    def test_an_invalid_completion_mode_is_refused(self, workspace_with_members, project, make_issue):
        instance = ProcessInstanceReference.objects.create(
            workspace=workspace_with_members,
            external_source="espo",
            external_instance_id="c-1",
            template_name="onboarding",
            template_version="3",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            ProcessInstanceItem.objects.create(
                process_instance=instance,
                issue=make_issue(project),
                workspace=workspace_with_members,
                step_key="kyc",
                completion_mode="whenever",
            )

    def test_the_original_dates_never_move(self, workspace_with_members, project, make_issue):
        """The gap between the original and the current is the only thing that
        makes a report about lateness mean anything."""
        first = timezone.now()
        later = first + timedelta(days=10)
        row = IssueServiceLevel.objects.create(
            issue=make_issue(project),
            workspace=workspace_with_members,
            assignment_due_at=first,
            completion_due_at=first,
            source="manual",
        )
        assert row.original_completion_due_at == first

        row.completion_due_at = later
        row.original_completion_due_at = later
        row.save()
        row.refresh_from_db()

        assert row.original_completion_due_at == first
        assert row.completion_due_at == later


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAssignmentServiceFillsTheServiceLevel:
    def test_a_queued_item_keeps_the_policy_deadline(
        self, covered_unit, project, make_issue, workspace_with_members, add_member, grant_manual_access, plain_user
    ):
        add_member(covered_unit, plain_user)
        grant_manual_access(project, plain_user)
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=covered_unit,
            workspace=workspace_with_members,
            default_mode="manual",
            allowed_modes=["manual"],
            assignment_sla_seconds=1800,
        )
        issue = make_issue(project)

        set_responsibility(issue, covered_unit, source=ResponsibilitySource.INTERNAL_API)

        row = IssueServiceLevel.objects.get(issue=issue)
        assert row.source == "unit"
        assert row.assignment_due_at is not None
        assert row.original_assignment_due_at == row.assignment_due_at

    def test_an_explicit_deadline_is_the_source_of_the_promise(
        self, covered_unit, project, make_issue, add_member, grant_manual_access, plain_user
    ):
        add_member(covered_unit, plain_user)
        grant_manual_access(project, plain_user)
        issue = make_issue(project)
        due = timezone.now() + timedelta(hours=2)

        set_responsibility(
            issue,
            covered_unit,
            source=ResponsibilitySource.PUBLIC_API,
            assignment_due_at=due,
            completion_due_at=due,
        )

        row = IssueServiceLevel.objects.get(issue=issue)
        assert row.assignment_due_at == due
        assert row.completion_due_at == due
        assert row.original_completion_due_at == due

    def test_a_process_claim_overrides_the_allocation_source(self, project, make_issue):
        """Allocation writes the row first; the template still owns the dates."""
        issue = make_issue(project)
        due = timezone.now() + timedelta(days=1)
        record_service_level(issue, assignment_due_at=due, completion_due_at=due, source="manual")
        record_service_level(
            issue,
            assignment_due_at=due,
            completion_due_at=due,
            source="process",
            source_version="3",
        )

        row = IssueServiceLevel.objects.get(issue=issue)
        assert row.source == "process"
        assert row.source_version == "3"

        record_service_level(issue, assignment_due_at=due, completion_due_at=due, source="manual")
        row.refresh_from_db()
        assert row.source == "process"


@pytest.mark.unit
@pytest.mark.django_db
class TestBuildingAnInstance:
    def test_four_steps_make_one_run(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        url = work_items_url(workspace_with_members, project)
        for index in range(4):
            response = post(
                caller,
                url,
                step_payload(covered_unit.slug, f"step-{index}"),
                key=f"espo-onboarding:client-1:step-{index}:evt-1",
            )
            assert response.status_code == status.HTTP_201_CREATED, response.data

        instance = ProcessInstanceReference.objects.get(external_instance_id="client-1")
        assert ProcessInstanceItem.objects.filter(process_instance=instance).count() == 4
        assert instance.template_version == "3"
        assert instance.status == "running"

    def test_the_response_says_which_step_it_is(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        response = post(
            caller,
            work_items_url(workspace_with_members, project),
            step_payload(covered_unit.slug, "kyc"),
            key="k1",
        )

        assert response.data["process"]["step_key"] == "kyc"
        assert response.data["process"]["template_version"] == "3"

    def test_replaying_the_same_event_does_not_add_a_step(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        url = work_items_url(workspace_with_members, project)
        body = step_payload(covered_unit.slug, "kyc")
        first = post(caller, url, body, key="same-key")
        assert first.status_code == status.HTTP_201_CREATED, first.data

        response = post(caller, url, body, key="same-key")

        # A replay answers the recorded response, 201 included — status is
        # not what distinguishes it from a second create (RFC §6.7).
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert response["Idempotent-Replay"] == "true"
        assert response.data["operation"]["replay"] is True
        assert response.data["work_item"]["id"] == first.data["work_item"]["id"]
        assert ProcessInstanceItem.objects.count() == 1

    def test_the_template_version_is_required(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        body = step_payload(covered_unit.slug, "kyc")
        del body["process"]["template_version"]

        response = post(caller, work_items_url(workspace_with_members, project), body, key="k1")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_invalid_completion_mode_is_refused_on_the_api(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        body = step_payload(covered_unit.slug, "kyc", mode="whenever")

        response = post(caller, work_items_url(workspace_with_members, project), body, key="k1")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ProcessInstanceItem.objects.exists()

    def test_the_block_is_refused_while_projection_is_off(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor, settings
    ):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = False

        response = post(
            caller, work_items_url(workspace_with_members, project), step_payload(covered_unit.slug, "kyc"), key="k1"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4929

    def test_the_process_dates_land_in_the_service_level(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        response = post(
            caller,
            work_items_url(workspace_with_members, project),
            step_payload(covered_unit.slug, "kyc", completion_due_at="2026-10-01T12:00:00Z"),
            key="k1",
        )

        service_level = IssueServiceLevel.objects.get(issue_id=response.data["work_item"]["id"])
        assert service_level.source == "process"
        assert service_level.source_version == "3"
        assert service_level.completion_due_at.isoformat().startswith("2026-10-01T12:00")
        assert service_level.original_completion_due_at == service_level.completion_due_at


@pytest.mark.unit
@pytest.mark.django_db
class TestClosingAStep:
    def _step(self, caller, workspace, project, unit_slug, mode, key="k1"):
        response = post(caller, work_items_url(workspace, project), step_payload(unit_slug, "kyc", mode=mode), key=key)
        assert response.status_code == status.HTTP_201_CREATED, response.data
        return response.data["work_item"]["id"]

    def test_automatic_moves_it_to_done(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(caller, workspace_with_members, project, covered_unit.slug, "automatic")

        response = caller.post(
            public_complete_url(workspace_with_members.slug, project.id, issue_id),
            {"source": "espo", "event_id": "evt-9", "rule_version": "1", "evidence": {"doc": "ok"}},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["work_item"]["state"] == str(done_state.id)
        assert response.data["process"]["progress"] == {"done": 1, "total": 1}

    def test_automatic_with_review_does_not_close_it(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(caller, workspace_with_members, project, covered_unit.slug, "automatic_with_review")

        response = caller.post(
            public_complete_url(workspace_with_members.slug, project.id, issue_id),
            {"source": "espo", "event_id": "evt-9"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["work_item"]["state"] == str(backlog_state.id)
        assert Label.objects.filter(project=project, name="aguardando-validacao").exists()

    def test_a_review_state_is_used_when_the_area_has_one(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
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
        issue_id = self._step(caller, workspace_with_members, project, covered_unit.slug, "automatic_with_review")

        response = caller.post(
            public_complete_url(workspace_with_members.slug, project.id, issue_id),
            {"source": "espo"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.data["work_item"]["state"] == str(review_state.id)

    def test_manual_is_refused(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(caller, workspace_with_members, project, covered_unit.slug, "manual")

        response = caller.post(
            public_complete_url(workspace_with_members.slug, project.id, issue_id),
            {"source": "espo"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="complete-1",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == 4930
        assert response.data["error_message"] == "ORG_COMPLETION_MANUAL_ONLY"

    def test_the_same_event_twice_records_one_completion(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(caller, workspace_with_members, project, covered_unit.slug, "automatic")
        url = public_complete_url(workspace_with_members.slug, project.id, issue_id)
        body = {"source": "espo", "event_id": "evt-9"}

        caller.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY="complete-1")
        caller.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY="complete-2")

        assert ProcessCompletionEvent.objects.filter(issue_id=issue_id).count() == 1

    def test_a_completion_event_cannot_be_rewritten(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        issue_id = self._step(caller, workspace_with_members, project, covered_unit.slug, "automatic")
        caller.post(
            public_complete_url(workspace_with_members.slug, project.id, issue_id),
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
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        url = work_items_url(workspace_with_members, project)
        first = post(caller, url, step_payload(covered_unit.slug, "a", mode="automatic"), key="k1").data["work_item"][
            "id"
        ]
        second = post(caller, url, step_payload(covered_unit.slug, "b", mode="automatic"), key="k2").data["work_item"][
            "id"
        ]

        caller.post(
            public_complete_url(workspace_with_members.slug, project.id, first),
            {"source": "espo"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="c1",
        )
        instance = ProcessInstanceReference.objects.get(external_instance_id="client-1")
        assert instance.status == "running"

        caller.post(
            public_complete_url(workspace_with_members.slug, project.id, second),
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
    def test_it_reports_every_step_with_its_state(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        url = work_items_url(workspace_with_members, project)
        for index in range(3):
            post(caller, url, step_payload(covered_unit.slug, f"s{index}"), key=f"k{index}")

        response = caller.get(public_process_instance_url(workspace_with_members.slug, "espo-onboarding", "client-1"))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert len(response.data["items"]) == 3
        assert response.data["instance"]["progress"] == {"done": 0, "total": 3}
        assert response.data["items"][0]["responsibility"]["unit"] == covered_unit.slug

    def test_it_follows_a_state_changed_in_the_app(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        from plane.db.models import Issue

        issue_id = post(
            caller,
            work_items_url(workspace_with_members, project),
            step_payload(covered_unit.slug, "only"),
            key="k1",
        ).data["work_item"]["id"]
        issue = Issue.objects.get(pk=issue_id)
        issue.state = done_state
        issue.save()

        response = caller.get(public_process_instance_url(workspace_with_members.slug, "espo-onboarding", "client-1"))

        assert response.data["instance"]["status"] == "completed"
        assert response.data["instance"]["progress"] == {"done": 1, "total": 1}

    def test_an_unknown_run_is_a_404(self, caller, workspace_with_members):
        response = caller.get(public_process_instance_url(workspace_with_members.slug, "espo-onboarding", "nobody"))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_the_route_is_gone_while_projection_is_off(self, caller, workspace_with_members, settings):
        settings.ORCA_PROCESS_PROJECTION_ENABLED = False

        response = caller.get(public_process_instance_url(workspace_with_members.slug, "espo-onboarding", "client-1"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == 4929

    def test_a_step_cannot_belong_to_two_runs(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        url = work_items_url(workspace_with_members, project)
        body = step_payload(covered_unit.slug, "kyc", instance="client-1")
        post(caller, url, body, key="k1")

        second = step_payload(covered_unit.slug, "kyc", instance="client-2")
        second["external"]["id"] = body["external"]["id"]
        response = post(caller, url, second, key="k2")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert ProcessInstanceItem.objects.count() == 1


@pytest.mark.unit
@pytest.mark.django_db
class TestReprocessingARun:
    """Item 4.7: the same events twice change nothing, and a failure in the
    middle of a run is something a replay can finish."""

    def test_twenty_events_replayed_leave_the_counts_alone(
        self, caller, workspace_with_members, project, covered_unit, backlog_state, executor
    ):
        from plane.db.models import Issue

        url = work_items_url(workspace_with_members, project)
        # Five runs of four steps is twenty create events, which is the
        # contract the runbook quotes: re-delivering the same webhooks must
        # not grow the projection.
        keys = []
        for run in range(5):
            for step in range(4):
                instance = f"client-{run}"
                key = f"espo-onboarding:{instance}:step-{step}:evt-1"
                keys.append(key)
                response = post(
                    caller,
                    url,
                    step_payload(covered_unit.slug, f"step-{step}", instance=instance, mode="automatic"),
                    key=key,
                )
                assert response.status_code == status.HTTP_201_CREATED, response.data

        counts = (
            Issue.objects.count(),
            ProcessInstanceItem.objects.count(),
            ProcessInstanceReference.objects.count(),
        )
        assert counts == (20, 20, 5)

        for run in range(5):
            for step in range(4):
                instance = f"client-{run}"
                key = f"espo-onboarding:{instance}:step-{step}:evt-1"
                response = post(
                    caller,
                    url,
                    step_payload(covered_unit.slug, f"step-{step}", instance=instance, mode="automatic"),
                    key=key,
                )
                assert response.status_code == status.HTTP_201_CREATED
                assert response["Idempotent-Replay"] == "true"

        assert (
            Issue.objects.count(),
            ProcessInstanceItem.objects.count(),
            ProcessInstanceReference.objects.count(),
        ) == counts

    def test_a_failure_on_step_three_is_finished_by_a_replay(
        self,
        caller,
        workspace_with_members,
        project,
        covered_unit,
        backlog_state,
        done_state,
        executor,
    ):
        url = work_items_url(workspace_with_members, project)
        issue_ids = []
        for index, step in enumerate(("intake", "kyc", "interview", "welcome")):
            response = post(
                caller,
                url,
                step_payload(covered_unit.slug, step, mode="automatic"),
                key=f"run:client-1:{step}:create",
            )
            assert response.status_code == status.HTTP_201_CREATED, response.data
            issue_ids.append(response.data["work_item"]["id"])

        def complete(issue_id, event_id, key):
            return caller.post(
                public_complete_url(workspace_with_members.slug, project.id, issue_id),
                {"source": "espo", "event_id": event_id},
                format="json",
                HTTP_IDEMPOTENCY_KEY=key,
            )

        assert complete(issue_ids[0], "e1", "complete-1").status_code == status.HTTP_200_OK
        assert complete(issue_ids[1], "e2", "complete-2").status_code == status.HTTP_200_OK

        # Inject a failure at step 3: the project has no completed state, so
        # automatic close cannot land. A 4xx spends that idempotency key
        # (RFC §6.7), so the retry after the project is fixed uses a new one.
        done_group = done_state.group
        done_state.group = StateGroup.STARTED.value
        done_state.save(update_fields=["group"])

        failed = complete(issue_ids[2], "e3", "complete-3-failed")
        assert failed.status_code == status.HTTP_400_BAD_REQUEST

        done_state.group = done_group
        done_state.save(update_fields=["group"])

        replayed = complete(issue_ids[2], "e3-retry", "complete-3-retry")
        assert replayed.status_code == status.HTTP_200_OK, replayed.data
        last = complete(issue_ids[3], "e4", "complete-4")
        assert last.status_code == status.HTTP_200_OK, last.data

        instance = ProcessInstanceReference.objects.get(external_instance_id="client-1")
        assert instance.status == "completed"
        assert ProcessCompletionEvent.objects.filter(issue_id__in=issue_ids).count() == 4
        assert ProcessInstanceItem.objects.filter(process_instance=instance).count() == 4
