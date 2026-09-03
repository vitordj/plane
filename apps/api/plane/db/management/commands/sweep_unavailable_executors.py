# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The availability sweep, by hand.

Same code the hourly task runs, with a dry run in front of it. That order
matters: the first time this is pointed at a real workspace, the useful
question is "how much work is this about to move, and does the list look
right?", and a task that answers it only by doing it is a task nobody dares
turn on.

Reports by default. ``--write`` returns the work to its area's queue, exactly
as the task would.
"""

# Python imports
from typing import Any

# Django imports
from django.conf import settings
from django.core.management import BaseCommand, CommandError

# Module imports
from plane.bgtasks.organizational_availability_task import (
    return_unusable,
    unusable_executor_links,
)
from plane.db.models import Workspace


class Command(BaseCommand):
    help = "Report (or, with --write, return to the queue) work whose executor is unavailable."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=str, help="Limit to one workspace slug. Default: all of them.")
        parser.add_argument(
            "--write",
            action="store_true",
            help="Return the work items to their area's queue. Without this the command only reports.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not getattr(settings, "ORCA_ORG_UNITS_ENABLED", True):
            raise CommandError("The organizational layer is disabled (ORCA_ORG_UNITS_ENABLED=0); refusing to sweep.")

        workspace = None
        if options.get("workspace"):
            workspace = Workspace.objects.filter(slug=options["workspace"]).first()
            if workspace is None:
                raise CommandError(f"Workspace with slug {options['workspace']} does not exist")

        # Unlike the task, the command reports with the feature off: knowing
        # what it *would* move is exactly what somebody needs before deciding
        # to switch it on. Writing with it off is still refused.
        links = unusable_executor_links()
        if workspace is not None:
            links = [link for link in links if link.workspace_id == workspace.id]

        if options["write"] and not getattr(settings, "ORCA_AVAILABILITY_ENABLED", False):
            raise CommandError(
                "Availability is disabled (ORCA_AVAILABILITY_ENABLED=0); refusing to write. "
                "Run without --write to see what it would do."
            )

        counts = {}
        for link in links:
            reason = getattr(link, "reason", "unknown")
            counts[reason] = counts.get(reason, 0) + 1
            self.stdout.write(
                f"{reason:<16} issue={link.issue_id} area={link.organizational_unit_id} "
                f"executor={link.primary_executor_id}"
            )
            if options["write"]:
                return_unusable(link)

        for reason, count in sorted(counts.items()):
            self.stdout.write(f"  {reason}: {count}")

        if options["write"]:
            self.stdout.write(self.style.SUCCESS(f"Returned {len(links)} work item(s) to the queue."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Found {len(links)} work item(s) with an unusable executor. Nothing written.")
            )
