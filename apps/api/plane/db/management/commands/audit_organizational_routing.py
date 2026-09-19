# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from typing import Any

# Django imports
from django.core.management import BaseCommand, CommandError

# Module imports
from plane.app.services.orca import audit_routing, organizational_units_enabled
from plane.app.services.orca.routing_audit import REPAIRABLE
from plane.db.models import Workspace


class Command(BaseCommand):
    help = (
        "Check the routing invariants that cannot be database constraints "
        "(RFC §6.1 I3, I4) for one workspace. Reports by default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=str, required=True, help="Workspace slug to audit")
        parser.add_argument(
            "--write",
            action="store_true",
            help=(
                "Return the items whose executor is gone or unassigned to the queue. "
                "Without this flag the command only reports."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Same rule as the reconciler: while the layer is off, no entry point
        # writes on its behalf — and a report of a layer nobody is running is
        # noise, so the whole command refuses rather than only its repairs.
        if not organizational_units_enabled():
            raise CommandError(
                "The organizational layer is disabled (ORCA_ORG_UNITS_ENABLED=0); refusing to audit routing."
            )

        workspace = Workspace.objects.filter(slug=options["workspace"]).first()
        if workspace is None:
            raise CommandError(f"Workspace with slug {options['workspace']} does not exist")

        write = options["write"]
        findings = audit_routing(workspace.id, write=write)

        for finding in findings:
            self.stdout.write(str(finding))

        repairable = [finding for finding in findings if finding.kind in REPAIRABLE]
        repaired = [finding for finding in findings if finding.repaired]

        if not findings:
            self.stdout.write(self.style.SUCCESS("No routing violations found."))
            return

        summary = f"{len(findings)} violation(s); {len(repairable)} repairable"
        if write:
            summary = f"{summary}, {len(repaired)} returned to the queue"
        self.stdout.write(self.style.WARNING(summary + "."))
        if not write and repairable:
            self.stdout.write("Re-run with --write to return those items to the queue.")
