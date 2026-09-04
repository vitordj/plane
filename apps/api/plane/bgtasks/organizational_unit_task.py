# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Celery task for large organizational-access reconciliations.

Small mutations reconcile inline inside the request; anything wider than
``ORCA_ORG_SYNC_MAX_EDGES`` edges is queued here by
``plane.app.services.orca.dispatch_reconciliation`` after the transaction
commits. The task is idempotent and batched, so a retry re-derives the same
state instead of compounding changes.
"""

# Python imports
import logging

# Third party imports
from celery import shared_task

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")

# Projects reconciled per batch, keeping each transaction bounded.
PROJECT_BATCH_SIZE = 25


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def reconcile_organizational_access(self, workspace_id, member_ids=None, project_ids=None):
    """
    Reconcile inherited project access for a workspace scope.

    @description Splits the work into project batches so a large unit does not
    hold one long transaction. Each batch is independently idempotent.

    @param workspace_id: Workspace to reconcile.
    @param member_ids: Optional workspace member ids to narrow the scope.
    @param project_ids: Optional project ids to narrow the scope.
    """
    from plane.app.services.orca import organizational_units_enabled, reconcile_access
    from plane.app.services.orca.org_unit_reconciler import projects_in_workspace

    # A task queued before the layer was switched off must not land after it.
    # The switch is read here, at execution time, rather than at dispatch, so
    # the worker honours the setting as it stands when the write would happen.
    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping reconciliation for workspace %s.", workspace_id)
        return

    try:
        targets = list(project_ids) if project_ids else projects_in_workspace(workspace_id)
        if not targets:
            reconcile_access(workspace_id, member_ids, None)
            return

        for index in range(0, len(targets), PROJECT_BATCH_SIZE):
            batch = targets[index : index + PROJECT_BATCH_SIZE]
            reconcile_access(workspace_id, member_ids, batch)
    except Exception as exception:
        log_exception(exception)
        raise self.retry(exc=exception)
