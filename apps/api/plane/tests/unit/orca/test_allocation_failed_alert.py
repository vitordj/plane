# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The immediate alert when an allocation ends with nobody assigned.

``allocation_failed`` is the state where the caller has been told the work was
handed over and it is sitting there with no owner — and if the area set no
assignment SLA, the sweep has no deadline to notice either. So the service
raises it at once (decision M10).

Two properties are the reason this file exists rather than a single happy-path
assertion.

**It fires after the commit, not inside it** (RFC §12, decision F10). An alert
sent from inside the transaction is an alert sent for allocations that then
roll back, and the area chases an item that does not exist.

**It cannot take the allocation down with it.** ``transaction.on_commit``
callbacks run after the commit on the caller's own thread, so an exception
from one escapes into the caller. That is exactly the defect PR #13 found in
the public API: with the broker down the exception escaped, the work item was
created but the response was a 500, the idempotency receipt was marked
``failed``, and every retry of that key replayed the same 500 forever. The
tests below run the callbacks with the broker raising and assert that the
allocation still lands in ``allocation_failed``, that the HTTP call is still a
201, and that the receipt still succeeds.
"""

import pytest
from kombu.exceptions import OperationalError

from plane.app.services.orca import allocate, return_to_queue
from plane.bgtasks import organizational_queue_task
from plane.db.models import (
    AssignmentMode,
    AutomationOperation,
    AutomationOperationStatus,
    DecisionOutcome,
    IssueOrganizationalUnit,
    Notification,
    OrganizationalUnitAssignmentPolicy,
    QueueReason,
    RoutingState,
    State,
    StateGroup,
)

from .conftest import ROLE_MEMBER, public_work_items_url


@pytest.fixture
def empty_area(unit, project, link_project, admin_user, grant_manual_access):
    """The area covers the project and has nobody who can hold its work."""
    link_project(unit, project, ROLE_MEMBER)
    State.objects.create(project=project, name="Backlog", group=StateGroup.BACKLOG.value, default=True)
    # The caller may create work; nobody may be assigned it.
    grant_manual_access(project, admin_user)
    return unit


@pytest.fixture
def ranking_policy(unit, workspace_with_members):
    """An area that hands work out by the ranking, so failing to is possible."""
    return OrganizationalUnitAssignmentPolicy.objects.create(
        organizational_unit=unit,
        workspace=workspace_with_members,
        default_mode=AssignmentMode.LEAST_LOADED,
        allowed_modes=[AssignmentMode.LEAST_LOADED.value],
    )


@pytest.fixture
def broken_broker(mocker):
    """A broker that is down, as the worker's queue call would see it."""
    broken = mocker.Mock()
    broken.delay.side_effect = OperationalError("broker is not reachable")
    return mocker.patch.object(organizational_queue_task, "notify_allocation_failed", broken)


def _link_for(issue, unit, project):
    return IssueOrganizationalUnit.objects.create(
        issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
    )


def _alerts_for(issue):
    return Notification.objects.filter(entity_identifier=issue.id, sender="in_app:orca:allocation_failed")


@pytest.mark.unit
@pytest.mark.django_db
class TestTheHookFiresOnCommit:
    def test_a_failed_allocation_alerts_the_coordinator(
        self,
        unit,
        project,
        empty_area,
        ranking_policy,
        make_issue,
        plain_user,
        add_coordinator,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        _link_for(issue, unit, project)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.ALLOCATION_FAILED
        assert len(callbacks) == 1
        assert _alerts_for(issue).get().receiver_id == plain_user.id

    def test_nothing_is_sent_before_the_transaction_commits(
        self,
        unit,
        project,
        empty_area,
        ranking_policy,
        make_issue,
        plain_user,
        add_coordinator,
        django_capture_on_commit_callbacks,
    ):
        # Captured but not executed: this is the state of the world between the
        # allocation and the commit. An alert written here would be an alert
        # sent for a transaction that may still roll back (F10).
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        _link_for(issue, unit, project)

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            allocate(issue, unit)

        assert len(callbacks) == 1
        assert not _alerts_for(issue).exists()

    def test_a_successful_allocation_alerts_nobody(
        self,
        unit,
        project,
        make_issue,
        add_member,
        grant_manual_access,
        second_user,
        plain_user,
        add_coordinator,
        link_project,
        workspace_with_members,
        django_capture_on_commit_callbacks,
    ):
        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, second_user)
        grant_manual_access(project, second_user)
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
        )
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        _link_for(issue, unit, project)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            result = allocate(issue, unit)

        assert result.outcome == DecisionOutcome.ASSIGNED
        assert callbacks == []
        assert not Notification.objects.filter(entity_identifier=issue.id).exists()

    def test_an_ordinary_queued_item_alerts_nobody(
        self,
        unit,
        project,
        empty_area,
        make_issue,
        plain_user,
        add_coordinator,
        django_capture_on_commit_callbacks,
    ):
        # With no policy the mode is manual, so the item waits for a human —
        # which is the queue working, not the allocator failing. Alerting here
        # would mean an alert per created work item.
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        _link_for(issue, unit, project)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            result = allocate(issue, unit)

        assert result.link.routing_state == RoutingState.QUEUED
        assert callbacks == []
        assert not Notification.objects.filter(entity_identifier=issue.id).exists()

    def test_a_return_to_the_queue_alerts_nobody(
        self,
        unit,
        project,
        make_issue,
        add_member,
        grant_manual_access,
        second_user,
        plain_user,
        add_coordinator,
        link_project,
        django_capture_on_commit_callbacks,
    ):
        # ``return_to_queue`` also goes through ``_apply_queued``, with state
        # ``queued``: a person handing an item back is not an allocator failing.
        link_project(unit, project, ROLE_MEMBER)
        add_member(unit, second_user)
        grant_manual_access(project, second_user)
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        _link_for(issue, unit, project)
        allocate(issue, unit, explicit_executor=second_user)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            result = return_to_queue(issue, actor=second_user, queue_reason=QueueReason.MANUALLY_RETURNED)

        assert result.link.routing_state == RoutingState.QUEUED
        assert callbacks == []
        assert not Notification.objects.filter(entity_identifier=issue.id).exists()

    def test_the_alert_does_not_stamp_the_sla_window(
        self,
        unit,
        project,
        empty_area,
        ranking_policy,
        make_issue,
        plain_user,
        add_coordinator,
        django_capture_on_commit_callbacks,
    ):
        # ``last_alerted_at`` is the sweep's re-alert window for a *passed
        # deadline*. Spending it here would mean an item that failed allocation
        # and then breached its SLA reports only the first of the two.
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        link = _link_for(issue, unit, project)

        with django_capture_on_commit_callbacks(execute=True):
            allocate(issue, unit)

        link.refresh_from_db()
        assert link.last_alerted_at is None


@pytest.mark.unit
@pytest.mark.django_db
class TestABrokerThatIsDown:
    def test_the_allocation_still_lands_in_allocation_failed(
        self,
        unit,
        project,
        empty_area,
        ranking_policy,
        make_issue,
        plain_user,
        add_coordinator,
        broken_broker,
        django_capture_on_commit_callbacks,
    ):
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        _link_for(issue, unit, project)

        # No ``pytest.raises``: the point is that nothing is raised. Running
        # the callbacks is what PR #13 proved escapes into the caller.
        with django_capture_on_commit_callbacks(execute=True):
            result = allocate(issue, unit)

        # The broker really was reached for, and really did raise: without
        # this the test would pass just as well with no hook at all.
        broken_broker.delay.assert_called_once()
        assert result.outcome == DecisionOutcome.ALLOCATION_FAILED
        link = IssueOrganizationalUnit.objects.get(issue=issue)
        assert link.routing_state == RoutingState.ALLOCATION_FAILED
        assert link.queue_reason == QueueReason.NO_ELIGIBLE_MEMBER
        # The alert did not go out. That is the accepted loss; the sweep picks
        # the item up once its deadline passes.
        assert not _alerts_for(issue).exists()

    def test_the_public_api_still_answers_201_and_the_receipt_succeeds(
        self,
        unit,
        project,
        empty_area,
        ranking_policy,
        caller_client,
        broken_broker,
        django_capture_on_commit_callbacks,
    ):
        # The PR #13 shape exactly: a robot creates work, the allocation fails
        # to find anybody, the broker is down. Before M10 there was no hook at
        # all here; the rule this pins is that adding one must not turn a
        # correct 201 into a 500 whose idempotency key replays forever.
        payload = {
            "external": {"source": "espo-onboarding", "id": "cliente-1"},
            "work_item": {"name": "Validate registration documents"},
            "responsibility": {"unit": unit.slug, "assignment": {"mode": "least_loaded"}},
        }

        with django_capture_on_commit_callbacks(execute=True):
            response = caller_client.post(
                public_work_items_url(project.workspace.slug, project.id),
                payload,
                format="json",
                HTTP_IDEMPOTENCY_KEY="key-1",
            )

        broken_broker.delay.assert_called_once()
        assert response.status_code == 201, response.data
        assert response.data["responsibility"]["routing_state"] == RoutingState.ALLOCATION_FAILED
        operation = AutomationOperation.objects.get(idempotency_key="key-1")
        assert operation.status == AutomationOperationStatus.SUCCEEDED

    def test_the_failure_is_logged_and_swallowed(
        self,
        unit,
        project,
        empty_area,
        ranking_policy,
        make_issue,
        plain_user,
        add_coordinator,
        broken_broker,
        caplog,
        django_capture_on_commit_callbacks,
    ):
        # Swallowed is not the same as ignored: an alert that never goes out
        # has to leave a trace somebody can find.
        add_coordinator(unit, plain_user)
        issue = make_issue(project)
        _link_for(issue, unit, project)

        with caplog.at_level("ERROR", logger="plane.orca.alerts"):
            with django_capture_on_commit_callbacks(execute=True):
                allocate(issue, unit)

        assert any("allocation-failed alert" in record.message for record in caplog.records)


@pytest.fixture
def caller_client(admin_user, token_client, project, grant_manual_access):
    """An API-key client whose user may create work in the project."""
    return token_client(admin_user)
