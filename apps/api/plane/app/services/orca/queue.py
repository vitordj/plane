# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What an area has waiting, as one query two surfaces share.

The public API reads it (item 1.4) and the coordinator's inbox will read it
(item 2.2). They differ in who may look and how the rows are rendered, not in
what "the queue" means — so the meaning lives here: which states count, and
what order a person or a robot should work through them in.

The order is the opinion this module holds. Overdue first, then oldest first:
an item whose assignment deadline has passed is the one costing somebody
something right now, and among items that are merely waiting, the one that has
waited longest has the best claim. Sorting by ``queued_at`` alone would bury a
breached SLA behind twenty fresh items.
"""

# Django imports
from django.db.models import BooleanField, Case, Q, Value, When
from django.utils import timezone

# Module imports
from plane.db.models import IssueOrganizationalUnit, RoutingState

# What "the queue" means when the caller does not say: work this area owns that
# nobody is on. ``allocation_failed`` belongs here rather than in an error
# list — it is an item waiting for a human precisely because the machine could
# not place it.
WAITING_STATES = (RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED)

# Accepted values of the ``routing_state`` filter beyond the real states.
ALL_STATES = "all"


def queue_queryset(unit, *, routing_state=None, overdue=None, project_id=None, now=None, visible_project_ids=None):
    """
    @description The area's queue, filtered and ordered (RFC §7.2, §8.1).
    @param unit: The ``OrganizationalUnit`` whose work to list.
    @param routing_state: One state to show, or ``"all"`` for every item the
        area owns including assigned ones. Default: the waiting states.
    @param overdue: ``True`` keeps only items past ``assignment_due_at``;
        ``False`` keeps only the rest; ``None`` keeps both.
    @param project_id: Restrict to one project.
    @param visible_project_ids: Projects the reader may be shown work from, or
        ``None`` for no restriction. Being in the area is not the same as being
        able to open its projects -- archiving one withdraws the access the
        layer granted, and the queue used to keep reporting its rows anyway
        (R1.A7). Passed in rather than derived here so this stays one query per
        page and so the caller, which knows who is asking, decides.
    @param now: The instant "overdue" is judged against. Passed in so every row
        of one page is judged against the same moment — a page evaluated
        row-by-row against ``now()`` can order two items by microseconds.
    @returns A queryset of ``IssueOrganizationalUnit``, annotated with
        ``assignment_overdue`` and ordered overdue-first, oldest-first.
    """
    now = now or timezone.now()

    queryset = IssueOrganizationalUnit.objects.filter(organizational_unit=unit)
    if routing_state == ALL_STATES:
        pass
    elif routing_state:
        queryset = queryset.filter(routing_state=routing_state)
    else:
        queryset = queryset.filter(routing_state__in=WAITING_STATES)

    if project_id:
        queryset = queryset.filter(project_id=project_id)

    if visible_project_ids is not None:
        queryset = queryset.filter(project_id__in=visible_project_ids)

    is_overdue = Case(
        When(Q(assignment_due_at__isnull=False) & Q(assignment_due_at__lt=now), then=Value(True)),
        default=Value(False),
        output_field=BooleanField(),
    )
    queryset = queryset.annotate(assignment_overdue=is_overdue)

    if overdue is True:
        queryset = queryset.filter(assignment_overdue=True)
    elif overdue is False:
        queryset = queryset.filter(assignment_overdue=False)

    return (
        queryset.select_related("issue", "primary_executor", "organizational_unit")
        # ``-assignment_overdue`` puts True first. ``queued_at`` last so an
        # assigned item, which has none, sorts predictably rather than by
        # whatever the database returns.
        .order_by("-assignment_overdue", "queued_at", "created_at")
    )
