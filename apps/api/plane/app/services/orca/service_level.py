# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What was promised about a work item.

``target_date`` on a Plane work item is a field anybody on it can change, which
makes it a useful thing to look at and a useless thing to report on: by the
time a work item is late, the date it was late against has often already
moved. So the promise is kept beside it, in ``IssueServiceLevel``, where the
first values are written once and never again (RFC F22).

Everything that sets a due date comes through here, for the same reason
everything that assigns comes through the assignment service: two writers
means two answers, and the second is the one nobody tested.
"""

# Module imports
from plane.db.models import IssueServiceLevel, ServiceLevelSource
from plane.db.models.organizational_assignment import PolicySource

# How the policy resolution's own vocabulary maps onto the service level's.
# `request` and `fallback` both mean "not a rule of the area's", which from a
# service level's point of view is somebody deciding by hand.
SOURCE_FOR_POLICY = {
    PolicySource.UNIT_PROJECT: ServiceLevelSource.UNIT_PROJECT,
    PolicySource.UNIT: ServiceLevelSource.UNIT,
    PolicySource.REQUEST: ServiceLevelSource.MANUAL,
    PolicySource.FALLBACK: ServiceLevelSource.MANUAL,
}


def record_service_level(
    issue,
    *,
    assignment_due_at=None,
    completion_due_at=None,
    source=ServiceLevelSource.MANUAL,
    source_version="",
    changed_by=None,
    reason="",
):
    """
    Write, or move, what was promised about this work item.

    @description Creates the row on the first date seen and updates the current
    dates afterwards. The originals are the model's own business — it refuses
    to let them move, which is what makes the difference between the original
    and the current worth reporting on.

    A ``None`` date does not clear anything. Allocation runs many times over a
    work item's life and most of those runs have nothing to say about the
    completion date; treating silence as "no longer promised" would erase the
    promise every time somebody was reassigned.
    @param issue: The work item.
    @param assignment_due_at: When somebody should have taken it.
    @param completion_due_at: When it should be done.
    @param source: Which rule produced the dates.
    @param source_version: The version of that rule.
    @param changed_by: Who moved them, when a person did.
    @param reason: Free text for the change.
    @returns: The ``IssueServiceLevel``, or ``None`` when there was nothing to
        record and no row already existed.
    """
    row = IssueServiceLevel.objects.filter(issue=issue).first()
    if row is None:
        if assignment_due_at is None and completion_due_at is None:
            return None
        return IssueServiceLevel.objects.create(
            issue=issue,
            workspace_id=issue.workspace_id,
            assignment_due_at=assignment_due_at,
            completion_due_at=completion_due_at,
            source=source,
            source_version=str(source_version or ""),
            changed_by=changed_by,
            change_reason=reason or "",
        )

    changed = False
    if assignment_due_at is not None and assignment_due_at != row.assignment_due_at:
        row.assignment_due_at = assignment_due_at
        changed = True
    if completion_due_at is not None and completion_due_at != row.completion_due_at:
        row.completion_due_at = completion_due_at
        changed = True
    if not changed:
        return row

    row.source = source
    row.source_version = str(source_version or "")
    row.changed_by = changed_by
    row.change_reason = reason or ""
    row.save()
    return row


def record_from_resolution(issue, link, resolution, *, actor=None, reason=""):
    """
    @description The allocation path's shorthand: take the assignment date the
    link now carries and the policy that produced it. Called from the one place
    that writes routing state, so a work item cannot end up with a due date the
    service level never heard about.
    @param issue: The work item.
    @param link: Its ``IssueOrganizationalUnit``, after the state change.
    @param resolution: The ``PolicyResolution`` in force.
    @returns: The ``IssueServiceLevel``, or ``None``.
    """
    return record_service_level(
        issue,
        assignment_due_at=link.assignment_due_at,
        source=SOURCE_FOR_POLICY.get(resolution.policy_source, ServiceLevelSource.MANUAL),
        source_version=resolution.policy_version or "",
        changed_by=actor,
        reason=reason,
    )
