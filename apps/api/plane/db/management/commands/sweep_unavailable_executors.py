# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from typing import Any

# Django imports
from django.core.management import BaseCommand, CommandError

# Module imports
from plane.app.services.orca import organizational_units_enabled
from plane.app.services.orca.availability_sweep import return_stranded_items
from plane.db.models import Workspace


class Command(BaseCommand):
    help = (
        "Find work assigned to people who can no longer do it — away, out of "
        "the area, deactivated, or without access to the project — and return "
        "it to the area's queue (RFC §6.9). Reports by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace",
            type=str,
            default=None,
            help="Workspace slug to sweep; omit to sweep every workspace.",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            help=(
                "Actually return the items to their areas' queues. Without this flag the "
                "command only reports, which is how an operator sees the list before the "
                "first run moves thirty items."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Same rule as the other two commands: while the layer is off, nothing
        # writes on its behalf, and a report about a layer nobody is running is
        # noise. Note this command deliberately does *not* require
        # ORCA_AVAILABILITY_ENABLED: the reasons it acts on include somebody
        # being deactivated or losing project access, which strand work whether
        # or not the instance has adopted absences. The beat task does require
        # it, because a scheduled write is a different promise from one an
        # operator asked for.
        if not organizational_units_enabled():
            raise CommandError("The organizational layer is disabled (ORCA_ORG_UNITS_ENABLED=0); refusing to sweep.")

        workspace_id = None
        slug = options.get("workspace")
        if slug:
            workspace = Workspace.objects.filter(slug=slug).first()
            if workspace is None:
                raise CommandError(f"No workspace with slug {slug!r}.")
            workspace_id = workspace.id

        write = bool(options.get("write"))
        results = return_stranded_items(workspace_id=workspace_id, write=write)

        if not results:
            self.stdout.write(self.style.SUCCESS("No work item is held by an unavailable executor."))
            return

        for entry in results:
            line = f"{entry['issue_id']}  unit={entry['unit_id']}  executor={entry['executor_id']}  {entry['reason']}"
            self.stdout.write(self.style.SUCCESS(line) if entry["returned"] else line)

        returned = sum(1 for entry in results if entry["returned"])
        if write:
            self.stdout.write(self.style.SUCCESS(f"Returned {returned} of {len(results)} item(s) to their queues."))
        else:
            self.stdout.write(
                self.style.WARNING(f"{len(results)} item(s) would be returned. Re-run with --write to do it.")
            )
