# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The assignment policy and the two append-only logs.

An allocation is contested by nature: somebody was chosen and somebody else was
not. A log that can be edited afterwards cannot answer "why does this person
have this?" a week later, which is the only question these tables exist to
answer — so the rows are written once and a change is a new row pointing at the
one it supersedes.

The policy's two partial unique constraints are here for a specific reason:
Postgres treats NULLs as distinct, so a single constraint over
``(unit, unit_project)`` would let one area collect any number of "default"
policies and leave the resolver picking arbitrarily between them.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from plane.db.models import (
    AssignmentDecision,
    AssignmentMode,
    DecisionOutcome,
    DecisionTrigger,
    IssueResponsibilityEvent,
    OrganizationalUnitAssignmentPolicy,
    PolicySource,
    ResponsibilitySource,
)

from .conftest import ROLE_MEMBER


def make_policy(unit, workspace, unit_project=None, **kwargs):
    return OrganizationalUnitAssignmentPolicy.objects.create(
        organizational_unit=unit, workspace=workspace, unit_project=unit_project, **kwargs
    )


@pytest.mark.unit
class TestPolicyUniqueness:
    def test_an_area_has_at_most_one_default_policy(self, unit, workspace_with_members):
        make_policy(unit, workspace_with_members)

        with pytest.raises(IntegrityError), transaction.atomic():
            make_policy(unit, workspace_with_members)

    def test_a_project_override_is_unique_too(self, unit, project, workspace_with_members, link_project):
        unit_project = link_project(unit, project, ROLE_MEMBER)
        make_policy(unit, workspace_with_members, unit_project=unit_project)

        with pytest.raises(IntegrityError), transaction.atomic():
            make_policy(unit, workspace_with_members, unit_project=unit_project)

    def test_a_default_and_a_project_override_live_together(self, unit, project, workspace_with_members, link_project):
        unit_project = link_project(unit, project, ROLE_MEMBER)

        make_policy(unit, workspace_with_members)
        make_policy(unit, workspace_with_members, unit_project=unit_project)

        assert OrganizationalUnitAssignmentPolicy.objects.filter(organizational_unit=unit).count() == 2

    def test_two_areas_each_get_their_own_default(self, unit, second_unit, workspace_with_members):
        make_policy(unit, workspace_with_members)
        make_policy(second_unit, workspace_with_members)

        assert OrganizationalUnitAssignmentPolicy.objects.count() == 2

    def test_a_cleared_policy_frees_the_slot(self, unit, workspace_with_members):
        """Soft delete is how a policy is removed; the next one must fit."""
        policy = make_policy(unit, workspace_with_members)
        policy.delete()

        make_policy(unit, workspace_with_members)

        assert OrganizationalUnitAssignmentPolicy.objects.filter(organizational_unit=unit).count() == 1


@pytest.mark.unit
class TestPolicyValidation:
    def test_allowed_modes_must_contain_the_default(self, unit, workspace_with_members):
        """
        Otherwise every allocation under the policy rejects the very mode it
        falls back to — a failure that would look like a bug in the allocator.
        """
        policy = OrganizationalUnitAssignmentPolicy(
            organizational_unit=unit,
            workspace=workspace_with_members,
            default_mode=AssignmentMode.LEAST_LOADED,
            allowed_modes=[AssignmentMode.MANUAL],
        )

        with pytest.raises(ValidationError):
            policy.clean()

    def test_an_unknown_mode_is_refused(self, unit, workspace_with_members):
        policy = OrganizationalUnitAssignmentPolicy(
            organizational_unit=unit,
            workspace=workspace_with_members,
            allowed_modes=["telepathy"],
        )

        with pytest.raises(ValidationError):
            policy.clean()

    def test_allowed_modes_must_be_a_list(self, unit, workspace_with_members):
        policy = OrganizationalUnitAssignmentPolicy(
            organizational_unit=unit, workspace=workspace_with_members, allowed_modes={"manual": True}
        )

        with pytest.raises(ValidationError):
            policy.clean()

    def test_an_empty_allowed_modes_defaults_to_the_default_mode(self, unit, workspace_with_members):
        policy = make_policy(unit, workspace_with_members, default_mode=AssignmentMode.SELF_CLAIM)

        assert policy.allowed_modes == [AssignmentMode.SELF_CLAIM.value]

    def test_the_version_increments_on_every_save(self, unit, workspace_with_members):
        """
        Decisions freeze the version they were taken under, so it has to move
        whenever the rules do — otherwise the log describes today's policy for
        yesterday's choices.
        """
        policy = make_policy(unit, workspace_with_members)
        assert policy.version == 1

        policy.assignment_sla_seconds = 3600
        policy.save()
        policy.save()

        policy.refresh_from_db()
        assert policy.version == 3


@pytest.fixture
def decision_kwargs(unit, project, workspace_with_members, make_issue):
    return {
        "issue": make_issue(project),
        "organizational_unit": unit,
        "project": project,
        "workspace": workspace_with_members,
        "trigger": DecisionTrigger.INTERNAL_API,
        "effective_mode": AssignmentMode.MANUAL,
        "policy_source": PolicySource.FALLBACK,
        "outcome": DecisionOutcome.QUEUED,
    }


@pytest.mark.unit
class TestTheLogsAreAppendOnly:
    def test_a_decision_cannot_be_edited(self, decision_kwargs):
        decision = AssignmentDecision.objects.create(**decision_kwargs)

        decision.reason = "actually it was someone else"
        with pytest.raises(ValueError):
            decision.save()

    def test_a_decision_cannot_be_soft_deleted(self, decision_kwargs):
        """Soft delete is a write, and these rows do not take writes."""
        decision = AssignmentDecision.objects.create(**decision_kwargs)

        with pytest.raises(ValueError):
            decision.delete()

    def test_a_responsibility_event_cannot_be_edited(self, unit, project, workspace_with_members, make_issue):
        event = IssueResponsibilityEvent.objects.create(
            issue=make_issue(project),
            workspace=workspace_with_members,
            to_unit=unit,
            source=ResponsibilitySource.UI,
        )

        event.reason = "rewriting history"
        with pytest.raises(ValueError):
            event.save()

    def test_superseding_is_how_a_decision_changes(self, decision_kwargs, plain_user):
        first = AssignmentDecision.objects.create(**decision_kwargs)

        second = AssignmentDecision.objects.create(
            **{
                **decision_kwargs,
                "outcome": DecisionOutcome.ASSIGNED,
                "effective_mode": AssignmentMode.EXPLICIT,
                "chosen_assignee": plain_user,
                "supersedes": first,
            }
        )

        assert second.supersedes_id == first.id
        assert list(first.superseded_by.all()) == [second]

    def test_the_snapshot_keeps_only_ids_and_numbers(self, decision_kwargs, plain_user):
        """No names, no emails: the log is auditable without being a profile."""
        snapshot = [{"user_id": str(plain_user.id), "total_open": 3, "unit_open": 1, "last_auto_at": None}]

        decision = AssignmentDecision.objects.create(**{**decision_kwargs, "candidates_snapshot": snapshot})

        decision.refresh_from_db()
        assert decision.candidates_snapshot == snapshot
        assert decision.algorithm_version == "lb-1"
