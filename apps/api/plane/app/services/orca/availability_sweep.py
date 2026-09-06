# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Work held by somebody who is no longer there (RFC §6.9, item 3.4).

An item can become nobody's while looking like somebody's: the executor went
on holiday, left the area, was deactivated in the workspace, or lost access to
the project. Nothing in Plane notices — the assignment is a row, and rows do
not expire — so the item sits "assigned" while the person it names is away,
which is worse than sitting in a queue: a queue is something a coordinator
looks at.

What this does is put those items back in the area's queue with
``executor_unavailable``, one ``AssignmentDecision`` per item, trigger
``availability``. What it deliberately does not do is choose somebody else.
Redistribution is a human's call (RFC §1.2); the sweep's job is to make the
work visible again, and the suggestion the queue shows next to such an item
(item 3.5) is a suggestion.

The person's ``IssueAssignee`` row stays. They keep seeing the item, and they
have not been taken off anything — they were away, not removed.

Five reasons, and only one of them is an absence. The others are a person
leaving the area, leaving the workspace, losing access to the project, and
being taken off the item through Plane's own assignee field. That last one is
invariant I3 breaking from the native side (RFC §12): the signal in
``signals.py`` catches it the moment a single row is deleted, and this pass
catches the case the signal cannot see, since the app's own "change the
assignees" path deletes them as a queryset and fires no signal.
"""

# Python imports
import logging

# Django imports
from django.utils import timezone

# Module imports
from plane.db.models import (
    DecisionTrigger,
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitMembership,
    ProjectMember,
    QueueReason,
    RoutingState,
    StateGroup,
    WorkspaceMember,
)

from .alerts import alert_work_returned
from .assignment_service import ASSIGNABLE_ROLE, return_to_queue
from .availability import orca_availability_enabled, unavailable_member_ids

logger = logging.getLogger("plane.orca.availability")

# Why one item came back, in the shape the caller reports.
REASON_AWAY = "away"
REASON_NOT_IN_UNIT = "left_the_area"
REASON_NOT_IN_WORKSPACE = "left_the_workspace"
REASON_NOT_IN_PROJECT = "lost_project_access"
# Invariant I3 broken from the native side: somebody took the executor off the
# item through Plane's own assignee field. A signal catches the single-row
# deletes (``services/orca/signals.py``), but the app's own "change the
# assignees" path deletes them as a queryset, which fires no signal at all —
# so this pass is the net that actually closes RFC §12's divergence risk.
REASON_NOT_AN_ASSIGNEE = "no_longer_an_assignee"


def stranded_items(workspace_id=None, at=None) -> list:
    """
    @description Every assigned item whose executor cannot act on it any more,
    with the reason. A pure read: it decides nothing and writes nothing, which
    is what lets the management command show an operator what a write *would*
    do before doing it.
    @param workspace_id: Narrow to one workspace, or ``None`` for all.
    @param at: The instant absences are judged against; defaults to now.
    @returns A list of ``(link, reason)`` pairs.
    """
    at = at or timezone.now()

    links = (
        IssueOrganizationalUnit.objects.filter(
            routing_state=RoutingState.ASSIGNED,
            primary_executor__isnull=False,
            organizational_unit__is_active=True,
        )
        .exclude(issue__state__group__in=[StateGroup.COMPLETED.value, StateGroup.CANCELLED.value])
        .select_related("issue__project", "issue__state", "organizational_unit", "primary_executor")
    )
    if workspace_id is not None:
        links = links.filter(workspace_id=workspace_id)

    links = list(links)
    if not links:
        return []

    executor_ids = {link.primary_executor_id for link in links}
    workspace_ids = {link.workspace_id for link in links}

    # Three batched reads instead of three per item: who is still an active
    # workspace member, who is an active member of the area holding the item,
    # and who can still hold an assignment in its project.
    active_members = {
        (row.member_id, row.workspace_id): row.id
        for row in WorkspaceMember.objects.filter(
            member_id__in=executor_ids, workspace_id__in=workspace_ids, is_active=True
        )
    }
    unit_pairs = set(
        OrganizationalUnitMembership.objects.filter(
            organizational_unit_id__in={link.organizational_unit_id for link in links},
            workspace_member__member_id__in=executor_ids,
            is_active=True,
            workspace_member__is_active=True,
        ).values_list("organizational_unit_id", "workspace_member__member_id")
    )
    project_pairs = set(
        ProjectMember.objects.filter(
            project_id__in={link.project_id for link in links},
            member_id__in=executor_ids,
            is_active=True,
            role__gte=ASSIGNABLE_ROLE,
        ).values_list("project_id", "member_id")
    )
    assignee_pairs = set(
        IssueAssignee.objects.filter(
            issue_id__in={link.issue_id for link in links}, assignee_id__in=executor_ids
        ).values_list("issue_id", "assignee_id")
    )
    away = unavailable_member_ids(active_members.values(), at)

    stranded = []
    for link in links:
        executor_id = link.primary_executor_id
        workspace_member_id = active_members.get((executor_id, link.workspace_id))
        if workspace_member_id is None:
            stranded.append((link, REASON_NOT_IN_WORKSPACE))
        elif (link.organizational_unit_id, executor_id) not in unit_pairs:
            stranded.append((link, REASON_NOT_IN_UNIT))
        elif (link.project_id, executor_id) not in project_pairs:
            stranded.append((link, REASON_NOT_IN_PROJECT))
        elif (link.issue_id, executor_id) not in assignee_pairs:
            stranded.append((link, REASON_NOT_AN_ASSIGNEE))
        elif workspace_member_id in away:
            stranded.append((link, REASON_AWAY))
    return stranded


def return_stranded_items(workspace_id=None, at=None, write=False) -> list:
    """
    @description Put the work of people who are no longer there back in their
    areas' queues.
    @param workspace_id: Narrow to one workspace, or ``None`` for all.
    @param at: The instant absences are judged against; defaults to now.
    @param write: ``False`` reports without touching anything — the management
        command's default, because an operator should see the list before the
        first run moves thirty items.
    @returns A list of ``{"issue_id", "unit_id", "executor_id", "reason",
        "returned"}`` dicts, one per stranded item.
    """
    results = []
    for link, reason in stranded_items(workspace_id=workspace_id, at=at):
        entry = {
            "issue_id": str(link.issue_id),
            "unit_id": str(link.organizational_unit_id),
            "executor_id": str(link.primary_executor_id),
            "reason": reason,
            "returned": False,
        }
        if write:
            try:
                # Through the service, so the return is locked, recorded and
                # indistinguishable from a coordinator's — except in its
                # trigger, which says nobody clicked.
                return_to_queue(
                    link.issue,
                    reason=f"executor {reason}",
                    queue_reason=QueueReason.EXECUTOR_UNAVAILABLE,
                    trigger=DecisionTrigger.AVAILABILITY,
                )
                entry["returned"] = True
                # The area has to hear it: nobody clicked, so without an alert
                # the item reappears in the queue with no explanation.
                link.refresh_from_db()
                alert_work_returned(link, reason=reason)
                logger.info(
                    "orca returned work of an unavailable executor",
                    extra={
                        "workspace_id": str(link.workspace_id),
                        "unit_id": str(link.organizational_unit_id),
                        "issue_id": str(link.issue_id),
                        "reason": reason,
                    },
                )
            except Exception:  # noqa: BLE001 - one item must not abort the pass
                logger.exception("orca could not return an unavailable executor's work")
        results.append(entry)
    return results


def sweep_is_allowed() -> bool:
    """
    @description Whether the sweep may write. Availability off means absences
    do not affect anything, so returning work because of one would be the
    product acting on a feature the operator switched off.
    @returns bool.
    """
    return orca_availability_enabled()
