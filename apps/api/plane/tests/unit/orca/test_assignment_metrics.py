# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What the assignment layer says about itself.

RFC §11 names the measurements before the fork has anywhere to send them, so
they are structured logs for now. What is pinned here is the part a dashboard
depends on and a refactor can silently break: the metric names, the label
names, and that no entry ever carries an e-mail, a display name or a title.
"""

import logging

import pytest

from plane.app.services.orca import allocate, reassign, return_to_queue, set_responsibility
from plane.app.services.orca.metrics import ASSIGNMENT_OUTCOME, DECISION_SUPERSEDED, NO_CANDIDATE
from plane.db.models import (
    AssignmentMode,
    DecisionOutcome,
    DecisionTrigger,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
)

from .conftest import ROLE_MEMBER

METRICS_LOGGER = "plane.orca.metrics"


@pytest.fixture
def covered(unit, project, link_project):
    return link_project(unit, project, ROLE_MEMBER)


@pytest.fixture
def staffed(covered, unit, project, add_member, grant_manual_access, plain_user, second_user):
    for user in (plain_user, second_user):
        add_member(unit, user)
        grant_manual_access(project, user)
    return plain_user, second_user


@pytest.fixture
def auto_policy(unit, workspace_with_members):
    return OrganizationalUnitAssignmentPolicy.objects.create(
        organizational_unit=unit,
        workspace=workspace_with_members,
        default_mode=AssignmentMode.LEAST_LOADED,
        allowed_modes=[AssignmentMode.LEAST_LOADED.value, AssignmentMode.MANUAL.value],
    )


@pytest.fixture
def make_link(unit, project, make_issue):
    def _make(issue=None):
        issue = issue or make_issue(project)
        return IssueOrganizationalUnit.objects.create(
            issue=issue, organizational_unit=unit, project=project, workspace=project.workspace
        )

    return _make


def emitted(caplog, metric):
    """The records for one metric, with their labels."""
    return [record for record in caplog.records if getattr(record, "metric", None) == metric]


@pytest.fixture
def capture(caplog):
    caplog.set_level(logging.INFO, logger=METRICS_LOGGER)
    return caplog


@pytest.mark.unit
class TestTheOutcomeCounter:
    def test_every_decision_is_counted_with_the_rfc_labels(
        self, capture, unit, project, make_issue, staffed, auto_policy, make_link
    ):
        issue = make_issue(project)
        make_link(issue)

        allocate(issue, unit, trigger=DecisionTrigger.INTERNAL_API)

        record = emitted(capture, ASSIGNMENT_OUTCOME)[-1]
        assert record.mode == AssignmentMode.LEAST_LOADED
        assert record.outcome == DecisionOutcome.ASSIGNED
        assert record.trigger == DecisionTrigger.INTERNAL_API
        assert record.unit_id == str(unit.id)
        assert record.issue_id == str(issue.id)
        assert record.decision_id

    def test_a_queued_item_is_counted_too(self, capture, unit, project, make_issue, staffed, make_link):
        """Without this, "nothing is being assigned" and "nothing is being
        asked for" look the same on a dashboard."""
        issue = make_issue(project)
        make_link(issue)

        allocate(issue, unit)

        record = emitted(capture, ASSIGNMENT_OUTCOME)[-1]
        assert record.outcome == DecisionOutcome.QUEUED
        assert record.mode == AssignmentMode.MANUAL

    def test_no_entry_carries_a_name_or_an_address(
        self, capture, unit, project, make_issue, staffed, auto_policy, make_link, plain_user
    ):
        issue = make_issue(project, name="Rename the staging database")
        make_link(issue)

        allocate(issue, unit)

        for record in capture.records:
            rendered = record.getMessage() + str(record.__dict__)
            assert plain_user.email not in rendered
            assert plain_user.display_name not in rendered
            assert "Rename the staging database" not in rendered


@pytest.mark.unit
class TestTheNoCandidateCounter:
    def test_an_area_with_nobody_eligible_is_reported_on_its_own(
        self, capture, unit, project, covered, make_issue, make_link, auto_policy
    ):
        """Separate from the outcome counter because the fix is different: an
        area to staff, not an allocator to debug."""
        issue = make_issue(project)
        make_link(issue)

        allocate(issue, unit)

        record = emitted(capture, NO_CANDIDATE)[-1]
        assert record.unit_id == str(unit.id)
        assert record.project_id == str(project.id)
        assert record.considered == 0

    def test_the_count_of_people_it_looked_at_comes_through(
        self, capture, unit, project, make_issue, staffed, make_link, workspace_with_members
    ):
        """An area whose members are all over the cap is a different problem
        from an empty one, and the label is what tells them apart."""
        OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.LEAST_LOADED.value],
            max_open_items_per_member=0,
        )
        issue = make_issue(project)
        make_link(issue)

        allocate(issue, unit)

        assert emitted(capture, NO_CANDIDATE)[-1].considered == 2

    def test_a_successful_allocation_reports_nothing(
        self, capture, unit, project, make_issue, staffed, auto_policy, make_link
    ):
        issue = make_issue(project)
        make_link(issue)

        allocate(issue, unit)

        assert emitted(capture, NO_CANDIDATE) == []


@pytest.mark.unit
class TestTheSupersededCounter:
    def test_taking_the_work_off_the_chosen_person_counts(
        self, capture, unit, project, make_issue, staffed, auto_policy, make_link
    ):
        """Rising against `least_loaded` means the ranking keeps picking people
        the coordinators correct — the algorithm is wrong, not the human."""
        first, second = staffed
        issue = make_issue(project)
        make_link(issue)
        allocate(issue, unit)
        chosen = IssueOrganizationalUnit.objects.get(issue=issue).primary_executor_id
        other = second if chosen == first.id else first

        reassign(issue, other)

        record = emitted(capture, DECISION_SUPERSEDED)[-1]
        assert record.unit_id == str(unit.id)
        assert record.previous_mode == AssignmentMode.LEAST_LOADED
        assert record.issue_id == str(issue.id)

    def test_returning_an_item_to_the_queue_counts(
        self, capture, unit, project, make_issue, staffed, auto_policy, make_link
    ):
        issue = make_issue(project)
        make_link(issue)
        allocate(issue, unit)

        return_to_queue(issue)

        assert emitted(capture, DECISION_SUPERSEDED)[-1].previous_mode == AssignmentMode.LEAST_LOADED

    def test_allocating_a_queued_item_supersedes_nothing(
        self, capture, unit, project, make_issue, staffed, make_link, workspace_with_members
    ):
        """The first decision queued the item; the second gave it to somebody.
        Nobody was overruled."""
        issue = make_issue(project)
        make_link(issue)
        allocate(issue, unit)

        set_responsibility(issue, unit, requested_mode=AssignmentMode.LEAST_LOADED.value)

        assert emitted(capture, DECISION_SUPERSEDED) == []
