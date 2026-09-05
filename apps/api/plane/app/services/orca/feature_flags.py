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
