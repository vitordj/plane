# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings

# Third party imports
from rest_framework.throttling import SimpleRateThrottle


class SCIMRateThrottle(SimpleRateThrottle):
    """
    Rate limit for the SCIM provisioning endpoints.

    @description The SCIM views authenticate with a workspace bearer token
    rather than a Plane session, so DRF sees every provisioning call as
    anonymous and the project-wide ``AnonRateThrottle`` applies its 30/minute
    to them. Microsoft Entra ID provisions in batches — one request per user
    and per group membership change — so a first sync of a real directory
    exceeds that in seconds and the rest of the run comes back 429. Entra
    retries, hits the same wall, and reports the connection as failing.

    Keyed on the workspace slug from the URL rather than the caller's IP.
    Every tenant's provisioning traffic arrives from Microsoft's own address
    ranges, so an IP-keyed limit would have one workspace's sync throttle
    another's, and the slug is available before authentication runs.

    A limit still applies: the bearer token is the real protection, and a
    generous ceiling only keeps a misconfigured or looping provisioner from
    saturating the API.
    """

    scope = "scim"
    rate = settings.SCIM_RATE_LIMIT

    def get_cache_key(self, request, view):
        slug = view.kwargs.get("slug")
        if not slug:
            return None
        return f"{self.scope}:{slug}"
