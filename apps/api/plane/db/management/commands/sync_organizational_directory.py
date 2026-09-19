# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from typing import Any

# Django imports
from django.core.management import BaseCommand, CommandError

# Module imports
from plane.app.services.orca import organizational_units_enabled, project_workspace, unresolved_identities
from plane.db.models import OrganizationalDirectoryConnection, OrganizationalUnit, Workspace


class Command(BaseCommand):
    help = (
        "Replay the directory mirror onto the organizational layer for one workspace, "
        "and report the identities that granted nothing. Calls nothing external."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=str, required=True, help="Workspace slug to project")
        parser.add_argument(
            "--report-only",
            action="store_true",
            help="Only print the unresolved identities, without projecting.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Same refusal the API and the reconcile command give: projecting the
        # mirror writes unit memberships and, through them, project access.
        if not organizational_units_enabled():
            raise CommandError(
                "The organizational layer is disabled (ORCA_ORG_UNITS_ENABLED=0); refusing to project the directory."
            )

        slug = options["workspace"]
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise CommandError(f"Workspace with slug {slug} does not exist")

        connection = OrganizationalDirectoryConnection.objects.filter(workspace_id=workspace.id).first()
        if connection is None:
            self.stdout.write(self.style.WARNING("No directory connection is configured for this workspace."))
        elif not connection.is_enabled:
            self.stdout.write(self.style.WARNING("The directory connection is configured but switched off."))

        bound_units = OrganizationalUnit.objects.filter(workspace_id=workspace.id).exclude(external_id="")
        self.stdout.write(f"{bound_units.count()} unit(s) bound to a directory group.")

        if not options["report_only"]:
            result = project_workspace(workspace.id)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Projected: {result.memberships_created} created, "
                    f"{result.memberships_reactivated} reactivated, "
                    f"{result.memberships_deactivated} withdrawn."
                )
            )

        unresolved = list(unresolved_identities(workspace.id))
        if not unresolved:
            self.stdout.write(self.style.SUCCESS("Every directory identity resolves to an active workspace member."))
            return

        self.stdout.write("")
        self.stdout.write(f"{len(unresolved)} identity/identities granted no access:")
        for identity in unresolved:
            reason = "inactive in the directory" if not identity.is_active else "not an active workspace member"
            self.stdout.write(f"  {identity.user_name:<45} {reason}")
        self.stdout.write("")
        self.stdout.write("Invite these people to the workspace, or remove them from the group in the directory.")
