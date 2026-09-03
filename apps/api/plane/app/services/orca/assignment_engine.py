# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Assignment engine for work items owned by an organizational unit.

Plane assigns work to people, not to groups: the backend drops assignees who
are not active project members. So a unit is marked *responsible* for a work
item (``IssueOrganizationalUnit``) and this engine turns that into a real
person, picking whoever currently carries the least work in the unit.

Workload is counted across the unit's own projects — not the single target
project, and not the whole workspace — so someone already loaded on the unit's
other work does not get more of it.
"""

# Python imports
from dataclasses import dataclass
from typing import Optional

# Django imports
from django.db.models import Count, Max

# Module imports
from plane.db.models import (
    Issue,
    IssueAssignee,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    ProjectMember,
    StateGroup,
)

from .coverage import covered_project_ids, unit_covers_project

# Work that no longer counts toward someone's load.
CLOSED_STATE_GROUPS = [StateGroup.COMPLETED.value, StateGroup.CANCELLED.value]

MODE_FILL_EMPTY = "fill_empty"
MODE_APPEND = "append"


@dataclass
class AssignmentCandidate:
    """One person the engine could assign, with the load that ranked them."""

    user_id: str
    workspace_member_id: str
    open_issues: int
    last_assigned_at: Optional[object]

    def as_dict(self) -> dict:
        return {
            "user_id": str(self.user_id),
            "workspace_member_id": str(self.workspace_member_id),
            "open_issues": self.open_issues,
            "last_assigned_at": self.last_assigned_at,
        }


def candidates_for(unit: OrganizationalUnit, project_id) -> list[AssignmentCandidate]:
    """
    Rank the people a unit could assign work to on one project.

    @description Candidates are active unit members who are also active
    ``ProjectMember``s of the target project — the same constraint the native
    assignee validation applies, checked before assigning rather than after.
    Ranking is least-loaded first, then whoever was assigned longest ago, then
    user id so the order is deterministic.

    @param unit: The responsible organizational unit.
    @param project_id: Project the work item belongs to.
    @returns: Candidates ordered best-first; empty when the unit has nobody
        eligible on that project.
    """
    # An area that does not cover the project has no candidates for it. This
    # used to be papered over by adding the project to the area's own list,
    # which handed the work to someone with no access to it — the defect this
    # guard exists for (invariant I2).
    if not unit_covers_project(unit, project_id):
        return []

    member_user_ids = list(
        OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit,
            is_active=True,
            workspace_member__is_active=True,
        ).values_list("workspace_member__member_id", flat=True)
    )
    if not member_user_ids:
        return []

    eligible = {
        project_member.member_id: project_member
        for project_member in ProjectMember.objects.filter(
            project_id=project_id,
            member_id__in=member_user_ids,
            is_active=True,
            member__is_bot=False,
        )
    }
    if not eligible:
        return []

    # Load is measured over the unit's live projects only.
    unit_project_ids = covered_project_ids(unit)

    load = (
        IssueAssignee.objects.filter(
            assignee_id__in=eligible.keys(),
            project_id__in=unit_project_ids,
        )
        .exclude(issue__state__group__in=CLOSED_STATE_GROUPS)
        .values("assignee_id")
        .annotate(open_issues=Count("id", distinct=True))
    )
    load_by_user = {row["assignee_id"]: row["open_issues"] for row in load}

    last_assigned = (
        IssueAssignee.objects.filter(assignee_id__in=eligible.keys(), project_id__in=unit_project_ids)
        .values("assignee_id")
        .annotate(last_assigned_at=Max("created_at"))
    )
    last_by_user = {row["assignee_id"]: row["last_assigned_at"] for row in last_assigned}

    memberships_by_user = {
        membership.workspace_member.member_id: membership.workspace_member_id
        for membership in OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit, is_active=True
        ).select_related("workspace_member")
    }

    candidates = [
        AssignmentCandidate(
            user_id=user_id,
            workspace_member_id=memberships_by_user.get(user_id),
            open_issues=load_by_user.get(user_id, 0),
            last_assigned_at=last_by_user.get(user_id),
        )
        for user_id in eligible
    ]

    # Least loaded, then assigned longest ago (never-assigned first), then id.
    candidates.sort(
        key=lambda candidate: (
            candidate.open_issues,
            candidate.last_assigned_at is not None,
            candidate.last_assigned_at,
            str(candidate.user_id),
        )
    )
    return candidates


def assign_from_unit(issue: Issue, unit: OrganizationalUnit, mode=MODE_FILL_EMPTY):
    """
    Assign a work item to the least-loaded eligible member of a unit.

    @description Never replaces existing assignees. In the default
    ``fill_empty`` mode an item that already has an assignee is left alone; in
    ``append`` mode a unit member is added alongside the current ones. Automatic
    replacement is deliberately not implemented: it needs a clearer policy on
    who owns a task.

    @param issue: The work item to assign.
    @param unit: The responsible organizational unit.
    @param mode: ``fill_empty`` (default) or ``append``.
    @returns: Tuple of (assigned candidate or ``None``, reason string).
    """
    existing = list(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True))

    if existing and mode == MODE_FILL_EMPTY:
        return None, "already_assigned"

    ranked = candidates_for(unit, issue.project_id)
    available = [candidate for candidate in ranked if candidate.user_id not in existing]
    if not available:
        return None, "no_eligible_member"

    chosen = available[0]
    IssueAssignee.objects.create(
        issue=issue,
        assignee_id=chosen.user_id,
        project_id=issue.project_id,
        workspace_id=issue.workspace_id,
    )
    return chosen, "assigned"


def workload_snapshot(unit: OrganizationalUnit) -> list[dict]:
    """
    Current open-work count per unit member, across the unit's projects.

    @description Backs the workload view in the UI and makes the engine's
    ranking inspectable before anyone relies on it.
    """
    unit_project_ids = covered_project_ids(unit)
    memberships = OrganizationalUnitMembership.objects.filter(
        organizational_unit=unit, is_active=True, workspace_member__is_active=True
    ).select_related("workspace_member", "workspace_member__member")

    if not unit_project_ids:
        return [
            {
                "workspace_member_id": str(membership.workspace_member_id),
                "display_name": membership.workspace_member.member.display_name,
                "role": membership.role,
                "open_issues": 0,
            }
            for membership in memberships
        ]

    load = (
        IssueAssignee.objects.filter(
            assignee_id__in=[membership.workspace_member.member_id for membership in memberships],
            project_id__in=unit_project_ids,
        )
        .exclude(issue__state__group__in=CLOSED_STATE_GROUPS)
        .values("assignee_id")
        .annotate(open_issues=Count("id", distinct=True))
    )
    load_by_user = {row["assignee_id"]: row["open_issues"] for row in load}

    return [
        {
            "workspace_member_id": str(membership.workspace_member_id),
            "display_name": membership.workspace_member.member.display_name,
            "role": membership.role,
            "open_issues": load_by_user.get(membership.workspace_member.member_id, 0),
        }
        for membership in memberships
    ]
