# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The numbers worth watching about assignment.

Structured log lines for now, one function per event, with the names from the
RFC's observability section. Deliberately the only place that knows how a
metric is emitted: adopting Prometheus or StatsD later is a change to this
file and to nothing else, and until then a log aggregator can already answer
"how often does allocation find nobody?" — which is the question that tells an
area its membership is wrong.
"""

# Python imports
import logging

logger = logging.getLogger("plane.orca.metrics")

ASSIGNMENT_OUTCOME = "orca.assignment.outcome"
NO_CANDIDATE = "orca.assignment.no_candidate"
DECISION_SUPERSEDED = "orca.assignment.decision_superseded"


def record_assignment_outcome(mode, outcome, trigger, **context):
    """
    @description One allocation finished, whatever it decided.
    @param mode: The effective assignment mode.
    @param outcome: assigned, queued, allocation_failed or rejected.
    @param trigger: What set it off.
    """
    logger.info(
        ASSIGNMENT_OUTCOME,
        extra={
            "metric": ASSIGNMENT_OUTCOME,
            "mode": str(mode),
            "outcome": str(outcome),
            "trigger": str(trigger),
            **context,
        },
    )


def record_no_candidate(unit_id, **context):
    """
    @description Automatic allocation found nobody eligible. Rare and
    important: it usually means the area's membership or its project links are
    wrong, not that everyone is busy.
    @param unit_id: The area that came up empty.
    """
    logger.warning(
        NO_CANDIDATE,
        extra={"metric": NO_CANDIDATE, "unit_id": str(unit_id), **context},
    )


def record_decision_superseded(unit_id, previous_mode, **context):
    """
    @description A human replaced an earlier decision. Watching how often
    automatic choices get overridden is how you find out whether the ranking
    matches what the area actually wants.
    @param unit_id: The area.
    @param previous_mode: The mode of the decision being replaced.
    """
    logger.info(
        DECISION_SUPERSEDED,
        extra={"metric": DECISION_SUPERSEDED, "unit_id": str(unit_id), "previous_mode": str(previous_mode), **context},
    )
