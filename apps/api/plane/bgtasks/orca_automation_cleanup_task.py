# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Orca (fork): retention for the automation API's idempotency receipts.

``AutomationOperation`` grows by one row per mutation the automation API
accepts, and each row carries the whole response body in ``response_snapshot``
(RFC §6.7) — deliberately, because a replay has to answer what the first call
answered. Nothing removed those rows, so the table grew without a ceiling for
as long as an integration kept calling. Every comparable table in this
codebase has a window: API activity logs and webhook logs at 14 days, email
notification logs at 7 (``cleanup_task.py``).

**Deleting a receipt un-spends its idempotency key**, which is the opposite of
what the table exists to guarantee, so the window is deliberately far longer
than the others rather than shorter. Receipts that an ``AssignmentDecision``
still names are not deleted at all: the FK is ``SET_NULL``, and a hard delete
would rewrite the append-only decision (R1.A3). What actually happens when a
key arrives after an *unreferenced* receipt is gone depends on the operation,
and none of the three duplicates work:

- **Creation** is find-or-create on ``ExternalWorkItemBinding``, and this task
  never touches bindings. The binding still resolves the caller's external key
  to the same work item, and ``_place`` returns early when the area asking is
  the area that already owns it — so the call reports the item's current state
  instead of creating a second one or re-running the allocation. The status is
  ``201`` either way (a replay answers the recorded response, creation
  included), so what a caller can observe is only the missing
  ``Idempotent-Replay`` header and a body describing the present rather than
  the original snapshot.
- **Reassignment** requires ``If-Match``. A retry carrying the decision id from
  the original call is stale by definition, so it is refused with
  ``ORG_DECISION_STALE`` rather than silently reassigning again.
- **Transfer** takes no ``If-Match`` and would re-execute, moving the item to
  the area the body names. If it is already there the destination is unchanged,
  but the transfer is recorded again and the allocation may pick a different
  executor. This is the case the window has to outlast, and at 30 days it
  outlasts any client retry schedule by orders of magnitude.
"""

# Python imports
from datetime import timedelta
import logging

# Django imports
from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

# Third party imports
from celery import shared_task

# Module imports
from plane.bgtasks.cleanup_task import BATCH_SIZE, process_cleanup_task
from plane.db.models import AssignmentDecision, AutomationOperation

logger = logging.getLogger("plane.worker")


def get_orca_automation_operations_queryset():
    """
    Receipts older than the retention window that no assignment decision names.

    @description Keyed on ``created_at`` rather than ``completed_at``, which is
    null while an operation is in progress. A row still ``in_progress`` after
    the window has passed is an operation that died mid-flight: §6.7 already
    treats one older than sixty seconds as abandoned and lets the next caller
    take it over, so at a window measured in days there is no live request
    behind it, and excluding those rows would leak exactly the receipts nothing
    else cleans up.

    Receipts that an ``AssignmentDecision`` still points at are left alone.
    Deleting those would make Django ``SET NULL`` the append-only decision
    row (R1.A3): the answer to "which call did this?" would vanish, and nothing
    in the log would say it had been there. Unreferenced receipts — the ones that
    never produced a decision — are still collected.
    @returns Iterator of primary keys to delete.
    """
    cutoff_time = timezone.now() - timedelta(days=settings.ORCA_AUTOMATION_OPERATION_RETENTION_DAYS)
    logger.info(f"Orca automation operations cutoff time: {cutoff_time}")
    named_by_a_decision = AssignmentDecision.all_objects.filter(automation_operation_id=OuterRef("pk"))
    return (
        AutomationOperation.all_objects.filter(created_at__lte=cutoff_time)
        .filter(~Exists(named_by_a_decision))
        .order_by("created_at")
        .values_list("id", flat=True)
        .iterator(chunk_size=BATCH_SIZE)
    )


@shared_task
def delete_orca_automation_operations():
    """Delete automation API receipts past the retention window."""
    process_cleanup_task(
        queryset_func=get_orca_automation_operations_queryset,
        model=AutomationOperation,
        task_name="Orca Automation Operation",
    )
