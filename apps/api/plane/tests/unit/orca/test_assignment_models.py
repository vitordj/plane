# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The assignment policy and the audit trail behind it.

Two properties are worth pinning here, because both are the kind that decay
quietly: an area cannot end up with two policies competing to answer the same
question, and a decision already written cannot be edited afterwards. An audit
trail that can be rewritten answers "who was this assigned to?" with whatever
the last writer preferred.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from plane.db.models import (
    AssignmentDecision,
    IssueResponsibilityEvent,
    OrganizationalUnitAssignmentPolicy,
)


@pytest.fixture
def unit_project(unit, project, link_project):
    return link_project(unit, project)


@pytest.fixture
def make_policy(workspace_with_members, unit):
    def _make(unit_project=None, default_mode="manual", allowed_modes=None, **kwargs):
        return OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit,
            unit_project=unit_project,
            workspace=workspace_with_members,
            default_mode=default_mode,
            allowed_modes=allowed_modes if allowed_modes is not None else [default_mode],
            **kwargs,
        )

    return _make


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
class TestPolicyUniqueness:
    def test_one_default_policy_per_area(self, make_policy):
        make_policy()

        with pytest.raises(IntegrityError), transaction.atomic():
            make_policy()

    def test_one_policy_per_area_and_project(self, make_policy, unit_project):
        make_policy(unit_project=unit_project)

        with pytest.raises(IntegrityError), transaction.atomic():
            make_policy(unit_project=unit_project)

    def test_a_default_and_a_project_policy_coexist(self, make_policy, unit_project):
        """The whole point of the two levels: an area rule with an exception."""
        default = make_policy(default_mode="manual")
        per_project = make_policy(unit_project=unit_project, default_mode="least_loaded")

        assert default.pk != per_project.pk


@pytest.mark.unit
@pytest.mark.django_db
class TestPolicyRules:
    def test_a_default_outside_the_allowed_modes_is_refused(self, make_policy):
        policy = make_policy(default_mode="manual", allowed_modes=["least_loaded"])

        with pytest.raises(ValidationError):
            policy.clean()

    def test_allowed_modes_defaults_to_the_default_mode(self, workspace_with_members, unit):
        policy = OrganizationalUnitAssignmentPolicy.objects.create(
            organizational_unit=unit, workspace=workspace_with_members, default_mode="self_claim"
        )

        assert policy.allowed_modes == ["self_claim"]

    def test_the_version_increments_on_every_save(self, make_policy):
        policy = make_policy()
        assert policy.version == 1

        policy.assignment_sla_seconds = 3600
        policy.save()
        policy.save()

        assert policy.version == 3


@pytest.mark.unit
@pytest.mark.django_db
class TestTheAuditTrailIsAppendOnly:
    def make_decision(self, workspace, unit, project, issue):
        return AssignmentDecision.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace,
            trigger="internal_api",
            effective_mode="manual",
            policy_source="fallback",
            outcome="queued",
        )

    def test_a_decision_cannot_be_edited(self, workspace_with_members, unit, project, make_issue):
        decision = self.make_decision(workspace_with_members, unit, project, make_issue(project))

        decision.reason = "rewriting history"
        with pytest.raises(ValueError):
            decision.save()

    def test_a_responsibility_event_cannot_be_edited(self, workspace_with_members, unit, project, make_issue):
        event = IssueResponsibilityEvent.objects.create(
            issue=make_issue(project), workspace=workspace_with_members, to_unit=unit, source="ui"
        )

        event.reason = "rewriting history"
        with pytest.raises(ValueError):
            event.save()

    def test_a_decision_can_supersede_another(self, workspace_with_members, unit, project, make_issue):
        issue = make_issue(project)
        first = self.make_decision(workspace_with_members, unit, project, issue)
        second = AssignmentDecision.objects.create(
            issue=issue,
            organizational_unit=unit,
            project=project,
            workspace=workspace_with_members,
            trigger="reassign",
            effective_mode="manual",
            policy_source="fallback",
            outcome="queued",
            supersedes=first,
        )

        assert second.supersedes_id == first.pk
        assert first.superseded_by.first() == second
