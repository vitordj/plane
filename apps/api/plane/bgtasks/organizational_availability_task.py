# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The hourly pass that finds work nobody can do (RFC §6.9, item 3.4).

An assignment does not expire. The person on an item can go on holiday, leave
the area, be deactivated in the workspace or lose access to the project, and
the item keeps saying their name — which is worse than being in a queue,
because a queue is something a coordinator looks at and an assignment is not.

Hourly, not every fifteen minutes: the states this notices change on a human
timescale (a holiday starts, an admin deactivates somebody), and the sweep
writes decisions and notifications, which is not something to do four times an
hour for no new information.

It never chooses a replacement. Putting the item back in the queue is the whole
of its job; who takes it next is a person's decision (RFC §1.2), and the
suggestion the queue shows next to a returned item is a suggestion.
"""

# Python imports
import logging

# Third party imports
from celery import shared_task

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sweep_unavailable_executors(self):
    """
    Return work held by people who are no longer available to do it.

    @description Runs on the beat schedule. Only writes when
    ``ORCA_AVAILABILITY_ENABLED`` is on: with availability off, absences do not
    affect the ranking, so acting on one here would be the product enforcing a
    feature the operator switched off.

    One item failing must not abort the pass — the service logs and continues —
    and the task only retries when something fails outside the loop.
    @returns The number of items returned to a queue.
    """
    from plane.app.services.orca import organizational_units_enabled
    from plane.app.services.orca.availability_sweep import return_stranded_items, sweep_is_allowed

    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the availability sweep.")
        return 0
    if not sweep_is_allowed():
        logger.info("Availability disabled (ORCA_AVAILABILITY_ENABLED=0); skipping the availability sweep.")
        return 0

    try:
        results = return_stranded_items(write=True)
    except Exception as exception:
        log_exception(exception)
        raise self.retry(exc=exception)

    returned = sum(1 for entry in results if entry["returned"])
    if results:
        logger.info(
            "Availability sweep: %s stranded item(s), %s returned to a queue.",
            len(results),
            returned,
        )
    return returned
