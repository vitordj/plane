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
