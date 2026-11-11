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


def _sweep_targets(workspace_id) -> list:
    """
    Every project a full-workspace reconciliation has to visit.

    @description The live projects of the workspace, plus any project that
    still carries a claim of the layer (an ``OrganizationalProjectAccessState``
    with ``last_applied_role``) even though it is archived now: an unscoped
    ``reconcile_access`` visits those pairs to withdraw their access, so a
    batched run over live projects alone would silently skip them.

    @param workspace_id: Workspace being swept.
    @returns: Distinct project ids, in a stable order.
    """
    from plane.app.services.orca.org_unit_reconciler import projects_in_workspace
    from plane.db.models import OrganizationalProjectAccessState

    claimed = OrganizationalProjectAccessState.objects.filter(
        workspace_id=workspace_id, last_applied_role__isnull=False
    ).values_list("project_id", flat=True)
    return sorted(set(projects_in_workspace(workspace_id)) | set(claimed), key=str)


def _sweep_workspace_ids() -> list:
    """
    Workspaces the periodic sweep has to look at.

    @description Those with at least one active organizational unit linked to a
    project, plus those where the layer still holds a claim on some project
    (so access left behind by the last unit being deactivated or unlinked is
    also repaired).

    @returns: Distinct workspace ids, in a stable order.
    """
    from plane.db.models import OrganizationalProjectAccessState, OrganizationalUnitProject

    linked = OrganizationalUnitProject.objects.filter(organizational_unit__is_active=True).values_list(
        "workspace_id", flat=True
    )
    claimed = OrganizationalProjectAccessState.objects.filter(last_applied_role__isnull=False).values_list(
        "workspace_id", flat=True
    )
    return sorted(set(linked) | set(claimed), key=str)


@shared_task
def reconcile_all_organizational_workspaces():
    """
    Periodic safety net: re-run the full reconciliation of every workspace.

    @description A large fan-out is queued to ``reconcile_organizational_access``
    after the membership change commits; if that task is lost or exhausts its
    retries, nothing else would ever re-run it and a deprovisioned person would
    keep project access until somebody ran the management command by hand. This
    task is that somebody: for each workspace using the layer it runs the same
    ``reconcile_access`` the command's ``--apply`` uses, in project batches.
    Reconciliation is idempotent, so a healthy workspace costs reads only and
    logs nothing. One workspace failing is logged and does not stop the rest.

    @returns: ``{workspace_id: changed pair count}`` for the workspaces where
        something changed (empty while the layer is switched off).
    """
    from plane.app.services.orca import organizational_units_enabled, reconcile_access

    # Same guard as the queued task, read at execution time: with the layer off
    # this must write nothing.
    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the periodic reconciliation.")
        return {}

    repaired = {}
    for workspace_id in _sweep_workspace_ids():
        try:
            targets = _sweep_targets(workspace_id)
            changed = 0
            for index in range(0, len(targets), PROJECT_BATCH_SIZE):
                batch = targets[index : index + PROJECT_BATCH_SIZE]
                changed += sum(1 for change in reconcile_access(workspace_id, None, batch) if change.action != "none")
            if changed:
                repaired[str(workspace_id)] = changed
                logger.info(
                    "Periodic reconciliation repaired %s access pair(s) in workspace %s.", changed, workspace_id
                )
        except Exception as exception:
            log_exception(exception)
    return repaired
