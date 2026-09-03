# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Work whose executor is no longer there.

Somebody goes on holiday, leaves a project, or is deactivated, and the work
they were carrying quietly stops moving — it still says "assigned", so it is
in nobody's inbox and nobody's queue. This sweep finds those and puts them
back in their area's queue, with ``executor_unavailable`` as the reason, and
tells the coordinators.

Three decisions are worth stating, because each of them is a thing this
deliberately does not do:

* **It never picks somebody else.** Handing the work to the next-least-loaded
  person automatically is how a holiday turns into three surprised colleagues.
  A person decides; the sweep only makes sure the decision is being asked for.
* **The native assignee stays.** The person keeps seeing the work item when
  they come back, which is what makes returning from two weeks away survivable.
* **Coming back does nothing.** The item does not spring back to them — by then
  somebody may have done it, or picked it up. Returning is a decision too.

Runs hourly. Everything it does goes through the assignment service, so every
return is an ``AssignmentDecision`` with ``trigger=availability`` and can be
read back off the timeline.
"""

# Python imports
import logging

# Django imports
from django.conf import settings
from django.utils import timezone

# Third party imports
from celery import shared_task

# Module imports
from plane.utils.exception_logger import log_exception

logger = logging.getLogger("plane.worker")

# Minimum project role that may hold work; below it, somebody cannot even see
# the work item, let alone execute it.
PROJECT_MEMBER_ROLE = 15


def unusable_executor_links(now=None):
    """
    The assigned work whose executor cannot do it any more.

    @description Four separate ways for an executor to fall away, and all four
    leave the work in the same silent place: away on an availability interval,
    out of the area, out of the workspace, or out of the project. Read as one
    query set plus one availability lookup rather than four sweeps, so the same
    work item is never returned twice in one pass.
    @param now: The moment to judge availability at; defaults to now.
    @returns: A list of ``IssueOrganizationalUnit`` with a ``reason`` attribute
        naming which of the four applies — for the log, and for the dry run.
    """
    from plane.app.services.orca.availability import unavailable_member_ids
    from plane.db.models import (
        IssueOrganizationalUnit,
        OrganizationalUnitMembership,
        ProjectMember,
        WorkspaceMember,
    )
    from plane.db.models.organizational_unit import RoutingState

    now = now or timezone.now()

    links = list(
        IssueOrganizationalUnit.objects.filter(
            routing_state=RoutingState.ASSIGNED, primary_executor__isnull=False
        ).select_related("issue", "organizational_unit", "primary_executor")
    )
    if not links:
        return []

    executor_ids = {link.primary_executor_id for link in links}
    workspace_ids = {link.workspace_id for link in links}

    # Active workspace membership, and the member row ids availability is keyed
    # by. Somebody with no row at all is out of the workspace.
    member_rows = WorkspaceMember.objects.filter(
        workspace_id__in=workspace_ids, member_id__in=executor_ids, is_active=True
    ).values_list("member_id", "workspace_id", "id")
    active_in_workspace = {(user_id, workspace_id) for user_id, workspace_id, _ in member_rows}
    member_row_of = {(user_id, workspace_id): row_id for user_id, workspace_id, row_id in member_rows}

    away_rows = unavailable_member_ids(member_row_of.values(), at=now)
    away = {pair for pair, row_id in member_row_of.items() if row_id in away_rows}

    in_project = set(
        ProjectMember.objects.filter(
            project_id__in={link.project_id for link in links},
            member_id__in=executor_ids,
            is_active=True,
            role__gte=PROJECT_MEMBER_ROLE,
        ).values_list("member_id", "project_id")
    )
    in_unit = set(
        OrganizationalUnitMembership.objects.filter(
            organizational_unit_id__in={link.organizational_unit_id for link in links},
            workspace_member__member_id__in=executor_ids,
            is_active=True,
        ).values_list("workspace_member__member_id", "organizational_unit_id")
    )

    unusable = []
    for link in links:
        executor_id = link.primary_executor_id
        pair = (executor_id, link.workspace_id)

        if pair not in active_in_workspace:
            link.reason = "left_workspace"
        elif (executor_id, link.organizational_unit_id) not in in_unit:
            link.reason = "left_area"
        elif (executor_id, link.project_id) not in in_project:
            link.reason = "left_project"
        elif pair in away:
            link.reason = "away"
        else:
            continue

        unusable.append(link)

    return unusable


def return_unusable(link):
    """
    @description Put one work item back in its area's queue and tell the
    coordinators. Everything goes through the service, so the return is an
    ``AssignmentDecision`` somebody can read afterwards.
    @param link: An ``IssueOrganizationalUnit`` from ``unusable_executor_links``.
    @returns: The number of notifications written.
    """
    from plane.app.services.orca import return_to_queue
    from plane.bgtasks.organizational_queue_task import notify_overdue
    from plane.db.models.organizational_unit import QueueReason

    result = return_to_queue(
        link.issue,
        actor=None,
        reason=getattr(link, "reason", ""),
        queue_reason=QueueReason.EXECUTOR_UNAVAILABLE,
        trigger="availability",
    )
    # Same alert as an overdue item: what a coordinator needs to know is that
    # something is waiting for a decision, not which sweep noticed.
    return notify_overdue(result.link)


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def sweep_unavailable_executors(self):
    """
    Return work whose executor is gone, hourly.

    @description Writes nothing while ``ORCA_AVAILABILITY_ENABLED`` is off:
    the other three reasons (left the workspace, the area, the project) are
    real without the feature, but returning work on them alone would surprise
    an operator who has not switched availability on. The audit command still
    reports them.
    @returns: A short summary for the worker log.
    """
    if not getattr(settings, "ORCA_ORG_UNITS_ENABLED", True):
        return {"skipped": "organizational layer disabled"}
    if not getattr(settings, "ORCA_AVAILABILITY_ENABLED", False):
        return {"skipped": "availability disabled"}

    returned = 0
    for link in unusable_executor_links():
        try:
            return_unusable(link)
            returned += 1
        except Exception as error:  # noqa: BLE001 - one bad row must not stop the sweep
            log_exception(error)

    logger.info("orca.availability.sweep", extra={"returned": returned})
    return {"returned": returned}
