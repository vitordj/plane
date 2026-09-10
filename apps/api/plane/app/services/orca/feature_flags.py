# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The organizational layer's kill switch, in one place.

``ORCA_ORG_UNITS_ENABLED=0`` has to stop the layer *acting*, not merely hide
it. The layer writes native ``ProjectMember`` rows, so every entry point that
can reach that write has to ask the same question: the API, the SCIM
provisioning endpoints, the management commands, and the Celery tasks that run
on the beat with nobody watching.

The check lives here rather than in the view module so a background task can
ask it without importing the API layer.
"""

# Django imports
from django.conf import settings


def organizational_units_enabled() -> bool:
    """
    Whether the organizational layer is switched on for this instance.

    @description Read at call time rather than captured at import, so flipping
    the setting takes effect on the next request or task run instead of on the
    next process restart.

    @returns: ``True`` when the layer may read and write.
    """
    return bool(getattr(settings, "ORCA_ORG_UNITS_ENABLED", True))


def orca_public_api_enabled() -> bool:
    """
    Whether ``/api/v1/orca/`` answers on this instance.

    @description Two switches, and both have to be on. The layer being enabled
    is not consent for machines to drive it: the automation API creates work
    items and allocates people through a long-lived API key, which is a wider
    blast radius than a person doing the same thing in the app. So an operator
    can run the layer for the UI while the API stays shut, which is how this
    instance ships and how production stays until Gate 2-minimum (RFC §9).

    Read at call time, for the same reason as above.

    @returns: ``True`` when the public automation API may answer.
    """
    return bool(organizational_units_enabled() and getattr(settings, "ORCA_PUBLIC_API_ENABLED", False))


def availability_enabled() -> bool:
    """
    Whether leave, opt-out and personal caps affect who gets new work.

    @description Default off. The tables can exist (and the UI of item 3.3
    can write them) while ranking still ignores them: ``is_available`` and
    ``accepts_new_work`` return the permissive answer until an operator
    turns this on. Independent of the public-API switch — people going on
    leave is a UI concern, not an automation one.

    Read at call time, for the same reason as the other two.

    @returns: ``True`` when availability windows and allocation settings
        may change ranking and the sweep.
    """
    return bool(getattr(settings, "ORCA_AVAILABILITY_ENABLED", False))


def process_projection_enabled() -> bool:
    """
    Whether this instance records process runs against work items.

    @description Default off. The tables can exist (and the assignment
    service can fill ``IssueServiceLevel``) while ``POST work-items/``
    still refuses a ``process`` block: an orchestrator that thought it was
    building a run and got four unrelated work items is worse than an
    error. Independent of the public-API switch — the flag is what the
    complete/ and process-instance routes consult, and they already sit
    behind ``ORCA_PUBLIC_API_ENABLED``.

    Read at call time, for the same reason as the other three.

    @returns: ``True`` when the process block, ``complete/`` and the
        instance read are accepted.
    """
    return bool(getattr(settings, "ORCA_PROCESS_PROJECTION_ENABLED", False))
