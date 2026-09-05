# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Legacy entry point for unit-based assignment.

@description The ranking, the locking and the decision log moved to
``assignment_service`` (RFC §6). This module stays as the shape the current
callers already use — ``assign_from_unit`` with its ``fill_empty``/``append``
modes and ``workload_snapshot`` for the workload panel — and delegates. Remove
it in Phase 2, once those callers speak to the service directly (D0.6).

Nothing here ranks candidates any more; if you are changing how work is handed
out, ``assignment_service`` is the file.
"""

# Python imports
from dataclasses import dataclass
from typing import Optional

# Django imports
from django.db.models import Count

# Module imports
from plane.app.services.orca.assignment_service import allocate, rank_candidates, resolve_policy
from plane.app.services.orca.errors import OrcaDomainError
from plane.db.models import (
    DecisionTrigger,
    Issue,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    StateGroup,
)

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


def candidates_for(unit: OrganizationalUnit, project_id, exclude_user_ids=()) -> list[AssignmentCandidate]:
    """
    Rank the people a unit could assign work to on one project.

    @description Delegates to ``assignment_service.rank_candidates`` (the
    ``lb-1`` algorithm) and maps the result onto the shape the existing callers
    read. Two differences from what this function used to return, both of them
    the service's rules: load counts only items where the person is the
    **primary executor** (a collaborator left over from an earlier assignment
    is not answerable for the work, and charging them kept pushing them down
    the ranking), and an area that does not cover the project has no candidates
    at all rather than having the project quietly added to its own list.

    @param unit: The responsible organizational unit.
    @param project_id: Project the work item belongs to.
    @param exclude_user_ids: People the ranking must not choose.
    @returns: Candidates ordered best-first; empty when the unit does not cover
        the project, or has nobody eligible on it.
    """
    # No requested mode: resolving one would refuse when the area has no policy
    # allowing least_loaded, and this legacy entry point has always ranked on
    # request. The resolution is read only for the load cap.
    policy = resolve_policy(unit, project_id)
    ranked = rank_candidates(unit, project_id, policy, exclude_user_ids=exclude_user_ids)
    return [
        AssignmentCandidate(
            user_id=candidate.user_id,
            workspace_member_id=candidate.workspace_member_id,
            open_issues=candidate.total_open,
            last_assigned_at=candidate.last_auto_at,
        )
        for candidate in ranked.eligible
    ]


def assign_from_unit(issue: Issue, unit: OrganizationalUnit, mode=MODE_FILL_EMPTY):
    """
    Assign a work item to the least-loaded eligible member of a unit.

    @description Kept for the callers that already use it. The ranking is the
    service's, and when the work item has a responsibility link for this unit
    the whole allocation goes through ``assignment_service.allocate``: row
    lock, routing state and decision log included.

    An item with **no** link is assigned the old way, writing only the
    ``IssueAssignee``. That path is the gap D0.6 closes when the endpoints move
    to the service — an assignment with no recorded responsibility has no
    queue state to update and nothing to write a decision against. It is left
    working rather than removed so this change does not alter what the current
    endpoint does.

    Existing assignees are never replaced: ``fill_empty`` leaves an item that
    already has one alone, and ``append`` asks for somebody who is not on it.

    @param issue: The work item to assign.
    @param unit: The responsible organizational unit.
    @param mode: ``fill_empty`` (default) or ``append``.
    @returns: Tuple of (assigned candidate or ``None``, reason string).
    """
    existing = list(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True))

    if existing and mode == MODE_FILL_EMPTY:
        return None, "already_assigned"

    ranked = candidates_for(unit, issue.project_id, exclude_user_ids=existing)
    if not ranked:
        return None, "no_eligible_member"

    chosen = ranked[0]
    link = IssueOrganizationalUnit.objects.filter(issue=issue, organizational_unit=unit).first()

    if link is not None:
        try:
            allocate(
                issue,
                unit,
                explicit_executor=chosen.user_id,
                trigger=DecisionTrigger.INTERNAL_API,
            )
        except OrcaDomainError:
            return None, "no_eligible_member"
        return chosen, "assigned"

    IssueAssignee.objects.get_or_create(
        issue=issue,
        assignee_id=chosen.user_id,
        defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
    )
    return chosen, "assigned"


def workload_snapshot(unit: OrganizationalUnit) -> list[dict]:
    """
    Current open-work count per unit member, across the unit's projects.

    @description Backs the workload view in the UI and makes the engine's
    ranking inspectable before anyone relies on it.
    """
    unit_project_ids = list(
        OrganizationalUnitProject.objects.filter(
            organizational_unit=unit, project__archived_at__isnull=True
        ).values_list("project_id", flat=True)
    )
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
