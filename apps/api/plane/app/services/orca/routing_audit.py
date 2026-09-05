# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Auditing the routing state of an area's work.

@description Two of the layer's invariants cannot be expressed as database
constraints, because they span tables the constraint cannot see: an item the
area considers assigned must have the executor as a live ``IssueAssignee``
(I3), and that executor must still be an active member of the area and of the
project (I4). Anything that writes those rows outside the service — Plane's own
assignee UI removing somebody, an admin taking a person off a project, a
directory sync withdrawing a membership — can break them without ever touching
the routing row.

So they are checked here, on demand, rather than trusted. The command reports
by default and repairs only when asked.
"""

# Python imports
from dataclasses import dataclass, replace
from typing import Optional

# Module imports
from plane.db.models import (
    AssignmentMode,
    DecisionTrigger,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    QueueReason,
    RoutingState,
)

from .assignment_service import _assert_eligible, return_to_queue
from .errors import ExecutorNotEligible, InvalidTransition

# What each finding means, and whether ``--write`` can do anything about it.
ASSIGNED_WITHOUT_ASSIGNEE = "assigned_without_assignee"
EXECUTOR_NOT_ELIGIBLE = "executor_not_eligible"
QUEUED_WITH_ASSIGNEE = "queued_with_assignee"
POLICY_CONTRADICTS_ITSELF = "policy_contradicts_itself"

# Findings the command knows how to repair: both mean the item is not actually
# being worked by the person the area thinks is on it, and the queue is where
# an item with nobody on it belongs.
REPAIRABLE = (ASSIGNED_WITHOUT_ASSIGNEE, EXECUTOR_NOT_ELIGIBLE)


@dataclass(frozen=True)
class Finding:
    """One violation, and what was done about it."""

    kind: str
    issue_id: Optional[object] = None
    unit_id: Optional[object] = None
    project_id: Optional[object] = None
    executor_id: Optional[object] = None
    policy_id: Optional[object] = None
    detail: str = ""
    repaired: bool = False

    def __str__(self) -> str:
        subject = f"issue={self.issue_id}" if self.issue_id else f"policy={self.policy_id}"
        parts = [f"{self.kind:<28}", subject]
        if self.unit_id:
            parts.append(f"unit={self.unit_id}")
        if self.executor_id:
            parts.append(f"executor={self.executor_id}")
        if self.detail:
            parts.append(self.detail)
        if self.repaired:
            parts.append("[returned to queue]")
        return " ".join(parts)


def _assigned_findings(workspace_id, *, write, actor):
    """@description I3 and I4 over every link the area considers assigned."""
    links = (
        IssueOrganizationalUnit.objects.filter(workspace_id=workspace_id, routing_state=RoutingState.ASSIGNED)
        .select_related("organizational_unit", "issue")
        .order_by("created_at")
    )

    for link in links:
        if link.primary_executor_id is None:
            # The CHECK constraint forbids this, so a row that has it is older
            # than the constraint or was written around it.
            yield Finding(
                kind=ASSIGNED_WITHOUT_ASSIGNEE,
                issue_id=link.issue_id,
                unit_id=link.organizational_unit_id,
                project_id=link.project_id,
                detail="assigned with no primary executor",
            )
            continue

        holds_item = IssueAssignee.objects.filter(issue_id=link.issue_id, assignee_id=link.primary_executor_id).exists()
        if not holds_item:
            yield _repair(
                Finding(
                    kind=ASSIGNED_WITHOUT_ASSIGNEE,
                    issue_id=link.issue_id,
                    unit_id=link.organizational_unit_id,
                    project_id=link.project_id,
                    executor_id=link.primary_executor_id,
                    detail="executor is not an assignee of the work item",
                ),
                link,
                write=write,
                actor=actor,
            )
            continue

        try:
            _assert_eligible(link.organizational_unit, link.project_id, link.primary_executor_id)
        except ExecutorNotEligible as exc:
            yield _repair(
                Finding(
                    kind=EXECUTOR_NOT_ELIGIBLE,
                    issue_id=link.issue_id,
                    unit_id=link.organizational_unit_id,
                    project_id=link.project_id,
                    executor_id=link.primary_executor_id,
                    detail=exc.payload.get("reason", ""),
                ),
                link,
                write=write,
                actor=actor,
            )


def _repair(finding, link, *, write, actor):
    """
    @description Put the item back in the queue, through the service, so the
    repair is a decision like any other rather than an UPDATE nobody can trace.
    @returns The finding, marked repaired when it was.
    """
    if not write:
        return finding
    try:
        return_to_queue(
            link.issue,
            actor=actor,
            reason=f"audit: {finding.kind}",
            queue_reason=QueueReason.EXECUTOR_UNAVAILABLE,
            trigger=DecisionTrigger.COMMAND,
        )
    except InvalidTransition:
        # Something moved the item between the read and the write. The next
        # run sees whatever it is now.
        return finding
    return replace(finding, repaired=True)


def _queued_findings(workspace_id):
    """
    @description Items the area is still waiting to hand out that somebody is
    already on. Reported and never repaired: the assignee may be a
    collaborator the coordinator added on purpose, and deciding otherwise is a
    person's call.
    """
    links = IssueOrganizationalUnit.objects.filter(
        workspace_id=workspace_id,
        routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED),
    ).order_by("created_at")

    for link in links:
        assignee_ids = list(IssueAssignee.objects.filter(issue_id=link.issue_id).values_list("assignee_id", flat=True))
        if assignee_ids:
            yield Finding(
                kind=QUEUED_WITH_ASSIGNEE,
                issue_id=link.issue_id,
                unit_id=link.organizational_unit_id,
                project_id=link.project_id,
                detail=f"{len(assignee_ids)} assignee(s) on a {link.routing_state} item",
            )


def _policy_findings(workspace_id):
    """
    @description A policy whose ``default_mode`` is outside its own
    ``allowed_modes`` refuses the very mode it falls back to, so every
    allocation under it fails. ``clean()`` catches it on the way in; this
    catches the rows that predate the rule or were written by a fixture.
    """
    for policy in OrganizationalUnitAssignmentPolicy.objects.filter(workspace_id=workspace_id, is_active=True):
        modes = policy.allowed_modes or []
        unknown = [mode for mode in modes if mode not in AssignmentMode.values]
        if unknown:
            yield Finding(
                kind=POLICY_CONTRADICTS_ITSELF,
                policy_id=policy.id,
                unit_id=policy.organizational_unit_id,
                detail=f"unknown modes: {', '.join(sorted(unknown))}",
            )
        elif modes and policy.default_mode not in modes:
            yield Finding(
                kind=POLICY_CONTRADICTS_ITSELF,
                policy_id=policy.id,
                unit_id=policy.organizational_unit_id,
                detail=f"default_mode={policy.default_mode} not in allowed_modes",
            )


def audit_routing(workspace_id, *, write=False, actor=None) -> list:
    """
    @description Check every routing invariant that lives outside the database
    for one workspace (RFC §6.1 I3, I4).
    @param workspace_id: The workspace to audit.
    @param write: Return the repairable findings' items to the queue. Off by
        default: the command previews, like the reconciler does.
    @param actor: Who is running it, recorded on the decisions written.
    @returns A list of ``Finding``, in the order they were found.
    """
    findings = list(_assigned_findings(workspace_id, write=write, actor=actor))
    findings.extend(_queued_findings(workspace_id))
    findings.extend(_policy_findings(workspace_id))
    return findings


__all__ = [
    "ASSIGNED_WITHOUT_ASSIGNEE",
    "EXECUTOR_NOT_ELIGIBLE",
    "Finding",
    "POLICY_CONTRADICTS_ITSELF",
    "QUEUED_WITH_ASSIGNEE",
    "REPAIRABLE",
    "audit_routing",
]
