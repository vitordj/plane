# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Counters for the assignment layer, emitted as structured logs.

@description RFC §11 names the measurements the layer has to expose — how
often allocation fails, how often an automatic choice is overturned — long
before this fork has anywhere to send them. So they are emitted here as INFO
logs carrying a ``metric`` field and the labels the RFC lists, which a log
pipeline can aggregate today and a Prometheus or StatsD client can replace
tomorrow by changing this module and nothing else.

Two rules hold for every entry: the label names are the RFC's, so a dashboard
written against the spec works against the logs, and the values are ids and
enum members only — never an e-mail, a name, or an item's title.
"""

# Python imports
import logging
from typing import Optional

logger = logging.getLogger("plane.orca.metrics")

# The metric names from RFC §11, so a typo fails to match a dashboard rather
# than silently inventing a series.
ASSIGNMENT_OUTCOME = "orca.assignment.outcome"
DECISION_SUPERSEDED = "orca.decision.superseded"
NO_CANDIDATE = "orca.assignment.no_candidate"


def _emit(metric: str, **labels) -> None:
    """@description One counter increment, as a log line. Empty labels are dropped."""
    payload = {key: value for key, value in labels.items() if value is not None}
    logger.info(metric, extra={"metric": metric, **payload})


def record_assignment_outcome(
    *,
    mode: str,
    outcome: str,
    trigger: str,
    workspace_id=None,
    unit_id=None,
    issue_id=None,
    decision_id=None,
) -> None:
    """
    @description ``orca.assignment.outcome`` — every decision the service
    writes, labelled by the mode that governed it, how it ended and what set it
    off. The rate of ``allocation_failed`` in here is the layer's health.
    """
    _emit(
        ASSIGNMENT_OUTCOME,
        mode=str(mode),
        outcome=str(outcome),
        trigger=str(trigger),
        workspace_id=_as_str(workspace_id),
        unit_id=_as_str(unit_id),
        issue_id=_as_str(issue_id),
        decision_id=_as_str(decision_id),
    )


def record_no_candidate(*, unit_id, workspace_id=None, project_id=None, considered: int = 0) -> None:
    """
    @description ``orca.assignment.no_candidate`` — the ranking ran and had
    nobody to offer. Separate from the outcome counter because the fix is
    different: this one is an area to staff, not an allocator to debug.
    @param considered: How many people the ranking looked at before excluding
        them all, which distinguishes an empty area from an over-loaded one.
    """
    _emit(
        NO_CANDIDATE,
        unit_id=_as_str(unit_id),
        workspace_id=_as_str(workspace_id),
        project_id=_as_str(project_id),
        considered=considered,
    )


def record_decision_superseded(*, unit_id, previous_mode: Optional[str], workspace_id=None, issue_id=None) -> None:
    """
    @description ``orca.decision.superseded`` — an earlier choice was
    overturned. Rising against ``least_loaded`` means the ranking is picking
    people the coordinators keep correcting, which is the signal that the
    algorithm, not the coordinator, is wrong.
    """
    _emit(
        DECISION_SUPERSEDED,
        unit_id=_as_str(unit_id),
        previous_mode=str(previous_mode) if previous_mode else None,
        workspace_id=_as_str(workspace_id),
        issue_id=_as_str(issue_id),
    )


def _as_str(value):
    return str(value) if value is not None else None


__all__ = [
    "ASSIGNMENT_OUTCOME",
    "DECISION_SUPERSEDED",
    "NO_CANDIDATE",
    "record_assignment_outcome",
    "record_decision_superseded",
    "record_no_candidate",
]
