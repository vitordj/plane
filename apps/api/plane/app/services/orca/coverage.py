# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Whether an area may be made responsible for work in a project.

An area (``OrganizationalUnit``) grants project access to its members through
the links in ``OrganizationalUnitProject``: those links are what the
reconciler turns into native ``ProjectMember`` rows. Marking an area
responsible for a work item in a project it does not link is therefore not a
harmless label — it names a group whose members have no access to that
project, so the assignment engine can find nobody, the workload view counts
work the area does not own, and whoever reads the item sees an owner that
cannot act on it.

This is defect D1 of the RFC (§2.2): the check existed nowhere. The endpoint
validated only that the area and the work item were in the same workspace, the
UI offered every active area in the workspace, and the engine papered over the
gap by adding the target project to the area's own project list when it was
missing — which quietly turned "this area does not cover this project" into
"count this project as the area's".
"""

# Module imports
from plane.db.models import OrganizationalUnitProject


def unit_covers_project(unit, project_id) -> bool:
    """
    @description Whether ``unit`` is linked to ``project_id`` in a way that
    actually grants access: the area is active, the link exists and is not
    soft-deleted (the default manager excludes those), and the project is not
    archived. An archived project grants nothing, so an area linked only to
    archived projects covers none of them.
    @param unit: The ``OrganizationalUnit`` to check, or ``None``.
    @param project_id: Id of the project the work item belongs to.
    @returns: ``True`` when the area may own work in that project.
    """
    if unit is None or project_id is None:
        return False

    if not unit.is_active:
        return False

    return OrganizationalUnitProject.objects.filter(
        organizational_unit=unit,
        project_id=project_id,
        project__archived_at__isnull=True,
    ).exists()
