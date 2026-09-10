# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What an automation needs to know about an area before it sends work to one.

Two reads, and both are shaped by what a client does next. ``units`` answers
"which areas exist, over which projects, accepting work how" — an integration
reads it once at startup and caches it, so the policy travels with the area
rather than requiring a second call per project. ``queue`` answers "what is
waiting", which is the coordinator's board seen from outside.

The policy is **resolved**, not stored: the project's policy over the area's
over the fallback (RFC §6.3). A client that read the raw rows would have to
reimplement that precedence, and would get it wrong the first time a project
policy appeared.
"""

# Module imports
from plane.app.services.orca import resolve_policy


def unit_payload(unit, unit_projects):
    """
    @description One area, with every project it covers and the assignment
    policy in force there.
    @param unit: The ``OrganizationalUnit``.
    @param unit_projects: Its live ``OrganizationalUnitProject`` rows, already
        fetched with their projects — passed in rather than queried here so a
        listing stays one query per page instead of one per area.
    @returns A JSON-serializable dict.
    """
    projects = []
    for link in unit_projects:
        resolution = resolve_policy(unit, link.project_id)
        projects.append(
            {
                "project_id": str(link.project_id),
                "identifier": link.project.identifier,
                "default_role": link.default_role,
                "policy": {
                    "default_mode": resolution.effective_mode,
                    "allowed_modes": list(resolution.allowed_modes),
                },
            }
        )
    return {"id": str(unit.id), "slug": unit.slug, "name": unit.name, "projects": projects}


def queue_row(link, *, now, process=None):
    """
    @description One item waiting on an area.
    @param link: An ``IssueOrganizationalUnit`` with its issue and executor
        already selected.
    @param now: The moment the page was read, so every row on a page is judged
        overdue against the same instant.
    @param process: The run this item is a step of, or ``None``. Batched by
        the caller (``process_payloads_for``) so a page does not query once
        per row. Always present on the payload so the UI can group without
        testing for the key.
    @returns A JSON-serializable dict.
    """
    executor = link.primary_executor
    overdue = bool(link.assignment_due_at and link.assignment_due_at < now)
    return {
        "issue_id": str(link.issue_id),
        "sequence_id": link.issue.sequence_id,
        "name": link.issue.name,
        "project_id": str(link.project_id),
        "routing_state": link.routing_state,
        "queue_reason": link.queue_reason,
        "queued_at": link.queued_at.isoformat() if link.queued_at else None,
        "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
        "assignment_overdue": overdue,
        "age_seconds": int((now - link.queued_at).total_seconds()) if link.queued_at else None,
        "primary_executor": None
        if executor is None
        else {"id": str(executor.id), "email": executor.email, "display_name": executor.display_name},
        "process": process,
    }
