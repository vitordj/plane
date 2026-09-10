# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Orca fields on a native issue webhook, without rewriting the serializer.

The native payload already carries ``workspace_slug`` (the fork added it)
and, for work items created through ``/api/v1/orca/``, ``external_source`` /
``external_id`` — those are columns on ``Issue``, so ``IssueExpandSerializer``
emits them. What it cannot emit is the area: that lives in a sidecar table
the upstream serializer has no business knowing about.

The orchestrator (item 4.4) still has to react to "this step moved". Native
``issue`` webhooks fire on Orca creates the same way they fire on UI creates
(``model_activity`` after commit). This module only attaches the three fields
the orchestrator cannot reconstruct from the native body alone: which area
owns the item, how it is routed, and who the primary executor is.

The native ``data`` object is copied, not mutated. A missing area is ``orca:
null``, so a receiver can tell "not an Orca item" from "the hook failed".
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger("plane.orca.webhooks")


def attach_orca_issue_sidecar(event_data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """
    @description Copy a serialized issue and attach ``orca``, or return the
    input unchanged when there is nothing to attach to (a delete payload is
    just ``{"id": …}`` and still gets the key so the shape is stable).
    @param event_data: ``IssueExpandSerializer.data``, or ``None``.
    @returns A plain dict with an ``orca`` key, or ``None`` when the input
        was ``None``.
    """
    if event_data is None:
        return None

    payload = dict(event_data)
    issue_id = payload.get("id")
    payload["orca"] = _sidecar_for(issue_id) if issue_id else None
    return payload


def _sidecar_for(issue_id) -> dict[str, Any] | None:
    """
    @description The three Orca fields, or ``None`` when this work item has
    no area. Failures stay off the webhook: losing the sidecar is better than
    dropping the delivery the orchestrator uses to resume a run.
    """
    # Imported here so ``webhook_task`` can call us without the services
    # package importing Celery on the way in.
    from plane.db.models import IssueOrganizationalUnit

    try:
        link = IssueOrganizationalUnit.objects.filter(issue_id=issue_id).select_related("organizational_unit").first()
    except Exception:
        logger.exception("orca webhook sidecar: failed to read routing for issue %s", issue_id)
        return None
    if link is None:
        return None
    return {
        "unit_slug": link.organizational_unit.slug,
        "routing_state": link.routing_state,
        "primary_executor": str(link.primary_executor_id) if link.primary_executor_id else None,
    }
