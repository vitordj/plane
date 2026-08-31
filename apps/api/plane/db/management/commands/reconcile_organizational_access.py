# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from typing import Any

# Django imports
from django.core.management import BaseCommand, CommandError

# Module imports
from plane.app.services.orca import plan_access, reconcile_access
from plane.db.models import Workspace


class Command(BaseCommand):
    help = "Reconcile inherited project access from Orca organizational units. Previews by default."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=str, required=True, help="Workspace slug to reconcile")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this flag the command only previews them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        slug = options["workspace"]
        apply_changes = options["apply"]

        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise CommandError(f"Workspace with slug {slug} does not exist")

        changes = reconcile_access(workspace.id) if apply_changes else plan_access(workspace.id)
        actionable = [change for change in changes if change.action != "none"]

        for change in actionable:
            self.stdout.write(
                f"{change.action:<20} member={change.workspace_member_id} "
                f"project={change.project_id} {change.current_role} -> {change.desired_role}"
            )

        mode = "Applied" if apply_changes else "Would apply (dry-run)"
        self.stdout.write(
            self.style.SUCCESS(f"{mode} {len(actionable)} change(s) across {len(changes)} evaluated pair(s).")
        )
        if not apply_changes and actionable:
            self.stdout.write("Re-run with --apply to write these changes.")
