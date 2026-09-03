# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What the organizational layer adds to a native webhook.

An integration that receives ``issue.created`` and has to make a second call
to find out which area owns the work item, and whether anybody is doing it,
will make that call on every event — and then the layer's usefulness is
measured in round trips.

So the payload carries a small ``orca`` block. Deliberately small: the area's
slug, where it stands in the queue, and who is executing it. Anything more and
this becomes a second, undeclared API that has to be versioned like one.

Attached from a single line in ``plane/bgtasks/webhook_task.py`` — the whole
extension lives here, so an upstream sync has one line to reconcile rather
than a serializer to merge.
"""

# Module imports
from plane.db.models import IssueOrganizationalUnit


def orca_webhook_extension(event, event_data):
    """
    The ``orca`` block for one webhook payload, or nothing.

    @description Returns ``{}`` for every event that is not a work item, and
    for work items no area owns — an absent block reads as "not in the
    organizational layer", which is exactly right and cheaper than a block
    full of nulls.

    Never raises. A webhook that failed to send because this could not resolve
    an area would be a worse failure than one missing an optional block.
    @param event: The webhook's event name.
    @param event_data: The serialized payload, as the webhook task built it.
    @returns: ``{"orca": {...}}`` or ``{}``.
    """
    if event != "issue" or not isinstance(event_data, dict):
        return {}

    issue_id = event_data.get("id")
    if not issue_id:
        return {}

    try:
        link = (
            IssueOrganizationalUnit.objects.select_related("organizational_unit", "primary_executor")
            .filter(issue_id=issue_id)
            .first()
        )
        if link is None:
            return {}

        executor = link.primary_executor
        return {
            "orca": {
                "unit_slug": link.organizational_unit.slug,
                "unit_id": str(link.organizational_unit_id),
                "routing_state": link.routing_state,
                "queue_reason": link.queue_reason,
                "primary_executor": (
                    {"id": str(executor.id), "email": executor.email, "display_name": executor.display_name}
                    if executor
                    else None
                ),
            }
        }
    except Exception:  # noqa: BLE001 - an optional block must never lose a webhook
        return {}
