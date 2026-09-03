# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What an automation may read about areas.

Two reads, and both are about deciding where to send work: which areas exist
and what they will do with it, and what is currently waiting in one. Nothing
here exposes membership — who is in an area is the workspace's business, not
an integration's.
"""

# Django imports
from django.db.models import Prefetch
from django.utils import timezone

# Third party imports
from rest_framework import status

# Module imports
from plane.api.serializers.orca import PublicUnitSerializer
from plane.db.models import (
    IssueOrganizationalUnit,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
)
from plane.db.models.organizational_unit import RoutingState
from plane.utils.orca_error_codes import orca_error

from .base import OrcaPublicBaseAPIView


class OrcaUnitListEndpoint(OrcaPublicBaseAPIView):
    """The areas of a workspace, with the projects each one covers."""

    use_read_replica = True

    def get(self, request, slug):
        units = (
            OrganizationalUnit.objects.filter(workspace__slug=slug, is_active=True)
            .prefetch_related(
                Prefetch(
                    "unit_projects",
                    queryset=OrganizationalUnitProject.objects.filter(project__archived_at__isnull=True).select_related(
                        "project"
                    ),
                    to_attr="covered_links",
                )
            )
            .order_by("name")
        )
        return self.paginate(
            request=request,
            queryset=units,
            on_results=lambda results: PublicUnitSerializer(results, many=True).data,
        )


class OrcaUnitQueueEndpoint(OrcaPublicBaseAPIView):
    """
    What is waiting in one area.

    @description The read an orchestrator uses to know whether the work it
    created is moving. Restricted to people who belong to the area or
    administer the workspace: a queue is a list of what a team has not done
    yet, which is not public information inside a company either.
    """

    use_read_replica = True

    def get(self, request, slug, unit_slug):
        unit = OrganizationalUnit.objects.filter(workspace__slug=slug, slug=unit_slug, is_active=True).first()
        if unit is None:
            return orca_error("ORG_UNIT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        if not self._may_read(request.user, unit):
            return orca_error("ORG_UNIT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        links = (
            IssueOrganizationalUnit.objects.filter(organizational_unit=unit)
            .select_related("issue", "issue__project", "primary_executor")
            .order_by("queued_at", "created_at")
        )

        routing_state = request.query_params.get("routing_state")
        if routing_state:
            links = links.filter(routing_state=routing_state)
        else:
            links = links.filter(routing_state__in=[RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED])

        project_id = request.query_params.get("project")
        if project_id:
            links = links.filter(project_id=project_id)

        if request.query_params.get("overdue") == "true":
            links = links.filter(assignment_due_at__lt=timezone.now())

        return self.paginate(
            request=request,
            queryset=links,
            on_results=lambda results: [self._row(link) for link in results],
        )

    def _may_read(self, user, unit):
        """
        @description Members of the area, and workspace admins. Answers 404
        rather than 403 to everyone else, so the existence of an area is not
        something an unrelated token can probe for.
        """
        from plane.db.models import WorkspaceMember

        if OrganizationalUnitMembership.objects.filter(
            organizational_unit=unit, is_active=True, workspace_member__member=user
        ).exists():
            return True
        return WorkspaceMember.objects.filter(
            workspace_id=unit.workspace_id, member=user, role=20, is_active=True
        ).exists()

    def _row(self, link):
        """@returns: One queue row, in the shape the envelope uses elsewhere."""
        issue = link.issue
        executor = link.primary_executor
        now = timezone.now()
        return {
            "issue_id": str(issue.id),
            "sequence_id": issue.sequence_id,
            "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
            "name": issue.name,
            "project_id": str(link.project_id),
            "routing_state": link.routing_state,
            "queue_reason": link.queue_reason,
            "queued_at": link.queued_at.isoformat() if link.queued_at else None,
            "assignment_due_at": link.assignment_due_at.isoformat() if link.assignment_due_at else None,
            "assignment_overdue": bool(link.assignment_due_at and link.assignment_due_at < now),
            "age_seconds": int((now - link.queued_at).total_seconds()) if link.queued_at else None,
            "primary_executor": (
                {"id": str(executor.id), "email": executor.email, "display_name": executor.display_name}
                if executor
                else None
            ),
        }
