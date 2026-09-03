# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Whether an area may own work in a project.

An area (``OrganizationalUnit``) inherits project access for its members from
the projects it is linked to. Marking an area responsible for a work item in a
project it does not cover therefore asks for something the access model cannot
give: nobody in the area is a member of that project, so the assignment engine
finds nobody, or — worse, and this is what used to happen — the engine quietly
treated the project as covered and handed the work to someone who then had to
be given access to see it.

One helper, used by every path that can attach an area to work: the endpoints,
the engine, and later the public API.
"""

# Module imports
from plane.db.models import OrganizationalUnitProject


def unit_covers_project(unit, project_id) -> bool:
    """
    Say whether this area may own work in this project.

    @description True only when all three hold: the area is active, it is
    linked to the project, and the project is not archived. Anything else and
    the area's members have no inherited access to that project, so work
    routed there has nowhere to land.
    @param unit: The ``OrganizationalUnit`` being marked responsible.
    @param project_id: The project the work item belongs to.
    @returns: ``True`` when the area covers the project.
    """
    if unit is None or not unit.is_active:
        return False

    return OrganizationalUnitProject.objects.filter(
        organizational_unit=unit,
        project_id=project_id,
        project__archived_at__isnull=True,
    ).exists()


def covered_project_ids(unit) -> list:
    """
    List the projects an area covers.

    @description Same rule as ``unit_covers_project``, in the form the API and
    the interface need to filter a list of areas down to the ones that may own
    a given work item.
    @param unit: The ``OrganizationalUnit``.
    @returns: Project ids, empty when the area is inactive.
    """
    if unit is None or not unit.is_active:
        return []

    return list(
        OrganizationalUnitProject.objects.filter(
            organizational_unit=unit,
            project__archived_at__isnull=True,
        ).values_list("project_id", flat=True)
    )
