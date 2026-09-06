# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Replaying a whole process, twice, and after a failure (item 4.7).

The per-endpoint tests say each call is idempotent. These say the *process* is:
an orchestrator that redelivers its entire backlog — because it restarted, or
because its queue guarantees at-least-once and did — must end with the same
number of work items, the same instance, and the same steps. Not "roughly the
same": identical counts, which is the only version of this property that can
be checked.

The second scenario is the one that actually happens. A run fails halfway; the
operator fixes whatever broke and replays from the beginning. Steps one and two
must not be created a second time, and steps three and four must appear.
"""

import pytest

from plane.db.models import (
    AssignmentDecision,
    AutomationOperation,
    Issue,
    IssueOrganizationalUnit,
    ProcessInstanceItem,
    ProcessInstanceReference,
    State,
    StateGroup,
)

from .conftest import ROLE_MEMBER, public_work_items_url

# Five steps across four instances is enough to be a real backlog and small
# enough to read the failure when one of them is wrong.
STEPS = ["intake", "compliance.kyc", "legal.review", "finance.setup", "handover"]
INSTANCES = ["cliente-1", "cliente-2", "cliente-3", "cliente-4"]


@pytest.fixture
def projection_on(settings):
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


@pytest.fixture
def caller(world, admin_user, project, grant_manual_access, token_client):
    grant_manual_access(project, admin_user)
    return token_client(admin_user)


def events():
    """
    The orchestrator's backlog: one event per (instance, step).

    @description The idempotency key is derived from the event, not generated
    per attempt — which is the rule the whole contract rests on, and the reason
    a redelivery is answerable from the receipt.
    """
    for instance in INSTANCES:
        for index, step in enumerate(STEPS):
            yield {
                "key": f"espo-onboarding:{instance}:{step}:evt-1",
                "body": {
                    "external": {"source": "espo-onboarding", "id": f"{instance}:{step}"},
                    "work_item": {"name": f"{step} for {instance}"},
                    "responsibility": {"unit": "compliance", "assignment": {"mode": "default"}},
                    "process": {
                        "source": "espo-onboarding",
                        "instance_id": instance,
                        "template_name": "onboarding-cliente",
                        "template_version": "3",
                        "step_key": step,
                        "completion_mode": "manual",
                    },
                },
            }


def deliver(caller, project, event):
    return caller.post(
        public_work_items_url(project.workspace.slug, project.id),
        event["body"],
        format="json",
        HTTP_IDEMPOTENCY_KEY=event["key"],
    )


def counts():
    return {
        "issues": Issue.objects.count(),
        "instances": ProcessInstanceReference.objects.count(),
        "steps": ProcessInstanceItem.objects.count(),
        "links": IssueOrganizationalUnit.objects.count(),
        "decisions": AssignmentDecision.objects.count(),
        "operations": AutomationOperation.objects.count(),
    }


@pytest.mark.unit
@pytest.mark.django_db
class TestReplayingTheWholeBacklog:
    def test_twenty_events_delivered_twice_produce_one_of_everything(self, projection_on, caller, project, world):
        backlog = list(events())
        assert len(backlog) == 20

        for event in backlog:
            assert deliver(caller, project, event).status_code == 201

        after_first = counts()
        assert after_first["issues"] == 20
        assert after_first["instances"] == 4
        assert after_first["steps"] == 20

        for event in backlog:
            response = deliver(caller, project, event)
            assert response.status_code == 201
            assert response.data["operation"]["replay"] is True

        # Identical, not "about the same": every count including the receipts,
        # because a second receipt would mean a second operation ran.
        assert counts() == after_first

    def test_a_run_that_failed_halfway_completes_on_replay(self, projection_on, caller, project, world):
        instance = "cliente-9"
        backlog = [
            {
                "key": f"espo-onboarding:{instance}:{step}:evt-1",
                "body": {
                    "external": {"source": "espo-onboarding", "id": f"{instance}:{step}"},
                    "work_item": {"name": f"{step} for {instance}"},
                    "responsibility": {"unit": "compliance", "assignment": {"mode": "default"}},
                    "process": {
                        "source": "espo-onboarding",
                        "instance_id": instance,
                        "template_name": "onboarding-cliente",
                        "template_version": "3",
                        "step_key": step,
                        "completion_mode": "manual",
                    },
                },
            }
            for step in STEPS[:4]
        ]

        # The run dies after the second step — the orchestrator crashed, the
        # broker went away, whatever it was.
        for event in backlog[:2]:
            assert deliver(caller, project, event).status_code == 201
        assert ProcessInstanceItem.objects.count() == 2

        # It is restarted and replays from the beginning, which is the only
        # thing an at-least-once queue can promise.
        for event in backlog:
            assert deliver(caller, project, event).status_code == 201

        assert ProcessInstanceReference.objects.count() == 1
        assert sorted(ProcessInstanceItem.objects.values_list("step_key", flat=True)) == sorted(STEPS[:4])
        assert Issue.objects.count() == 4

    def test_a_redelivery_with_a_changed_body_is_refused_rather_than_applied(
        self, projection_on, caller, project, world
    ):
        event = next(iter(events()))
        assert deliver(caller, project, event).status_code == 201

        changed = {**event, "body": {**event["body"], "work_item": {"name": "Something else entirely"}}}
        response = deliver(caller, project, changed)

        # The key is spent on the first payload. A second, different payload
        # under it is a bug in the caller, and answering 409 is what makes the
        # bug visible instead of silently choosing one of the two.
        assert response.status_code == 409
        assert response.data["error_message"] == "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH"
        assert Issue.objects.count() == 1
