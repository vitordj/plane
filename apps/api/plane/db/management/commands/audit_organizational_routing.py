# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Find work items whose queue state no longer matches reality.

The invariants the service maintains (I3: assigned means there is an executor
and they are a native assignee; I4: that executor is still a member of the
area and the project) can drift from outside it — somebody removes an assignee
in the app, a person leaves the project, an area's projects change. Nothing
notices until a coordinator wonders why an item nobody is working on says it
is being worked on.

Read-only by default. ``--write`` fixes only the two cases with one obviously
correct repair: put the work back in the queue.
"""

# Python imports
from typing import Any

# Django imports
from django.conf import settings
from django.core.management import BaseCommand, CommandError

# Module imports
from plane.app.services.orca import eligible_user_ids, return_to_queue
from plane.db.models import (
    IssueAssignee,
    IssueOrganizationalUnit,
    OrganizationalUnitAssignmentPolicy,
    Workspace,
)
from plane.db.models.organizational_unit import QueueReason, RoutingState

# Violations the command can repair, and the ones it can only report.
EXECUTOR_NOT_ASSIGNEE = "executor_not_assignee"
EXECUTOR_NOT_ELIGIBLE = "executor_not_eligible"
QUEUED_WITH_ASSIGNEE = "queued_with_assignee"
POLICY_DEFAULT_NOT_ALLOWED = "policy_default_not_allowed"

REPAIRABLE = (EXECUTOR_NOT_ASSIGNEE, EXECUTOR_NOT_ELIGIBLE)


def find_violations(workspace):
    """
    Report every routing invariant broken in one workspace.

    @description Four checks, in the order a person would make them: is the
    executor still on the work item, are they still able to do it, is
    something queued that somebody is quietly already doing, and does an
    area's policy contradict itself.
    @param workspace: The workspace to audit.
    @returns: List of dicts with ``kind``, ``link`` and a human ``detail``.
    """
    violations = []

    links = (
        IssueOrganizationalUnit.objects.filter(workspace=workspace)
        .select_related("organizational_unit", "issue", "primary_executor")
        .order_by("created_at")
    )

    eligibility_cache = {}

    for link in links:
        if link.routing_state == RoutingState.ASSIGNED and link.primary_executor_id:
            is_assignee = IssueAssignee.objects.filter(
                issue_id=link.issue_id, assignee_id=link.primary_executor_id, deleted_at__isnull=True
            ).exists()
            if not is_assignee:
                violations.append(
                    {
                        "kind": EXECUTOR_NOT_ASSIGNEE,
                        "link": link,
                        "detail": "executor is no longer an assignee of the work item",
                    }
                )
                continue

            cache_key = (link.organizational_unit_id, link.project_id)
            if cache_key not in eligibility_cache:
                eligibility_cache[cache_key] = set(eligible_user_ids(link.organizational_unit, link.project_id))
            if link.primary_executor_id not in eligibility_cache[cache_key]:
                violations.append(
                    {
                        "kind": EXECUTOR_NOT_ELIGIBLE,
                        "link": link,
                        "detail": "executor is no longer an active member of the area or the project",
                    }
                )

        elif link.routing_state in (RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED):
            # Reported, never repaired: an assignee here may be a collaborator
            # somebody added on purpose, and clearing them would take work away
            # from a person actually doing it.
            if IssueAssignee.objects.filter(issue_id=link.issue_id, deleted_at__isnull=True).exists():
                violations.append(
                    {
                        "kind": QUEUED_WITH_ASSIGNEE,
                        "link": link,
                        "detail": "queued, but somebody is assigned natively (may be a collaborator)",
                    }
                )

    for policy in OrganizationalUnitAssignmentPolicy.objects.filter(workspace=workspace, is_active=True):
        if policy.default_mode not in (policy.allowed_modes or []):
            violations.append(
                {
                    "kind": POLICY_DEFAULT_NOT_ALLOWED,
                    "link": None,
                    "detail": (
                        f"policy {policy.id} on area {policy.organizational_unit_id}: "
                        f"default_mode {policy.default_mode} is not in allowed_modes {policy.allowed_modes}"
                    ),
                }
            )

    return violations


class Command(BaseCommand):
    help = (
        "Audit Orca routing invariants for a workspace. Reports by default; --write returns broken work to the queue."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=str, required=True, help="Workspace slug to audit")
        parser.add_argument(
            "--write",
            action="store_true",
            help="Return work items with an unusable executor to the queue. Without this the command only reports.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        slug = options["workspace"]
        write = options["write"]

        # Same rule as the reconciler: with the layer off, every route is 404,
        # so the command must not be the one door left open.
        if not getattr(settings, "ORCA_ORG_UNITS_ENABLED", True):
            raise CommandError(
                "The organizational layer is disabled (ORCA_ORG_UNITS_ENABLED=0); refusing to audit routing."
            )

        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise CommandError(f"Workspace with slug {slug} does not exist")

        violations = find_violations(workspace)
        repaired = 0

        for violation in violations:
            link = violation["link"]
            target = f"issue={link.issue_id} area={link.organizational_unit_id}" if link else "policy"
            self.stdout.write(f"{violation['kind']:<26} {target:<60} {violation['detail']}")

            if write and violation["kind"] in REPAIRABLE:
                return_to_queue(
                    link.issue,
                    actor=None,
                    reason=violation["detail"],
                    queue_reason=QueueReason.EXECUTOR_UNAVAILABLE,
                    trigger="command",
                )
                repaired += 1

        counts = {}
        for violation in violations:
            counts[violation["kind"]] = counts.get(violation["kind"], 0) + 1
        for kind, count in sorted(counts.items()):
            self.stdout.write(f"  {kind}: {count}")

        if write:
            self.stdout.write(self.style.SUCCESS(f"Returned {repaired} work item(s) to the queue."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Found {len(violations)} violation(s). Nothing was written (dry-run).")
            )
            if any(violation["kind"] in REPAIRABLE for violation in violations):
                self.stdout.write("Re-run with --write to return the affected work items to the queue.")
