# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Read shapes for the areas an automation can send work to."""

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import OrganizationalUnit


class PublicUnitSerializer(serializers.ModelSerializer):
    """
    An area as an automation sees it.

    @description Deliberately thin: slug, name, and the projects it covers
    with the policy each one applies. An integration needs to know where it
    may send work and what will happen to it — not the area's membership,
    which is nobody's business outside the workspace.
    """

    projects = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationalUnit
        fields = ["id", "slug", "name", "description", "projects"]
        read_only_fields = fields

    def get_projects(self, obj):
        """@returns: The covered projects, each with its effective policy."""
        # Imported here: the service imports models, and importing it at module
        # scope would drag the whole assignment layer into serializer import.
        from plane.app.services.orca import resolve_policy

        rows = []
        for link in getattr(obj, "covered_links", None) or obj.unit_projects.filter(project__archived_at__isnull=True):
            resolution = resolve_policy(obj, link.project_id)
            rows.append(
                {
                    "project_id": str(link.project_id),
                    "identifier": link.project.identifier,
                    "default_role": link.default_role,
                    "policy": {
                        "default_mode": resolution.effective_mode,
                        "allowed_modes": (resolution.policy.allowed_modes if resolution.policy else ["manual"]),
                        "policy_source": resolution.policy_source,
                    },
                }
            )
        return rows
