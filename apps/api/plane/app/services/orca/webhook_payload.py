# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What an outside listener learns about the area, from a native webhook.

Plane's issue webhook already carries everything a subscriber needs about the
work item, ``external_source`` and ``external_id`` included — those are native
columns, and the payload serializer takes every field. What it cannot carry is
the part that does not exist upstream: which area owns the item, where it
stands in that area's queue, and who is on it.

An orchestrator listening for "this step changed" needs exactly that. Without
it, every webhook has to be followed by a read against ``/api/v1/orca/`` just
to learn whether the change matters — a request per event, for a fact the
event already implies.

So one key is added, ``orca``, and only for issue events. Ids and slugs, no
names or emails: a webhook goes to somewhere this instance does not control,
and the queue's own screens are where people's names belong.
"""

# Python imports
import logging

# Module imports
from plane.db.models import IssueOrganizationalUnit, ProcessInstanceItem

logger = logging.getLogger("plane.orca.webhooks")


def extend_issue_payload(data, issue_id):
    """
    @description Add the area's view of a work item to a native issue webhook
    payload (item 4.5).
    @param data: The serialized issue, as the native serializer produced it.
    @param issue_id: The work item.
    @returns The same dict, with an ``orca`` key when an area owns the item and
        untouched when none does — a workspace not using areas sees exactly the
        payload it saw before this existed.
    """
    if not isinstance(data, dict) or not issue_id:
        return data

    try:
        link = IssueOrganizationalUnit.objects.filter(issue_id=issue_id).select_related("organizational_unit").first()
        if link is None:
            return data

        step = ProcessInstanceItem.objects.filter(issue_id=issue_id).select_related("process_instance").first()

        data["orca"] = {
            "unit_id": str(link.organizational_unit_id),
            "unit_slug": link.organizational_unit.slug,
            "routing_state": link.routing_state,
            "queue_reason": link.queue_reason,
            "primary_executor": str(link.primary_executor_id) if link.primary_executor_id else None,
            "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
            "process": (
                {
                    "source": step.process_instance.external_source,
                    "instance_id": step.process_instance.external_instance_id,
                    "template_version": step.process_instance.template_version,
                    "step_key": step.step_key,
                    "completion_mode": step.completion_mode,
                }
                if step is not None
                else None
            ),
        }
    except Exception:  # noqa: BLE001 - a webhook must ship without this rather than not at all
        # The native payload is the contract; this is an addition to it. If the
        # addition fails, the subscriber should still get the event.
        logger.exception("orca could not extend an issue webhook payload")
    return data
