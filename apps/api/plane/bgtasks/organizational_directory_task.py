# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Periodic repair pass for directory-provisioned workspaces.

SCIM is event-driven, and the events it delivers are the directory's, not
Plane's. Entra tells Plane when somebody joins an Entra group; nothing tells it
when somebody finally accepts their Plane workspace invitation — and that is
exactly the moment a parked identity should turn into real access.

Rather than reach for signals (which the organizational layer deliberately
avoids, see ``org_unit_reconciler``), this task replays the projection from the
mirror already stored. It calls nothing external, is idempotent, and is cheap
for workspaces with no connection configured because it only visits workspaces
that have one enabled.
"""

# Python imports
import logging

# Third party imports
from celery import shared_task

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def resolve_directory_identities(self):
    """
    Re-resolve parked identities and re-project every provisioned workspace.

    @description Runs on the beat schedule. One workspace failing must not
    abort the rest, so failures are logged per workspace and the pass keeps
    going; the task only retries when something fails outside the loop.
    """
    from plane.app.services.orca import organizational_units_enabled, project_workspace
    from plane.db.models import OrganizationalDirectoryConnection

    # The kill switch has to reach the beat, not just the API. This pass
    # projects directory groups into unit memberships and reconciles them into
    # native ProjectMember rows, so leaving it running while the layer is off
    # would keep granting project access every hour through the one door
    # nobody is watching.
    if not organizational_units_enabled():
        logger.info("Organizational layer disabled; skipping the directory projection pass.")
        return

    try:
        connections = OrganizationalDirectoryConnection.objects.filter(is_enabled=True).values_list(
            "workspace_id", flat=True
        )
        for workspace_id in list(connections):
            try:
                result = project_workspace(workspace_id)
                if result.memberships_created or result.memberships_deactivated:
                    logger.info(
                        "Directory projection for workspace %s: %s",
                        workspace_id,
                        result.as_dict(),
                    )
            except Exception as exception:
                log_exception(exception)
    except Exception as exception:
        log_exception(exception)
        raise self.retry(exc=exception)
