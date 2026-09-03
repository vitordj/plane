# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import hashlib

# Django imports
from django.conf import settings

# Third party imports
from rest_framework.throttling import SimpleRateThrottle


class OrcaPublicRateThrottle(SimpleRateThrottle):
    """
    Rate limit for the public automation API.

    @description Keyed on the API token, not on the caller's IP: an
    orchestrator runs on one host, and every one of its tenants' traffic
    arrives from the same address, so an IP-keyed limit would have one
    integration throttle another's. The token is also what the limit is
    really about — it identifies the automation, and a looping automation is
    the failure mode this exists for.

    Generous on purpose. The permission check is the real protection; this
    only keeps a misconfigured retry loop from saturating the API.
    """

    scope = "orca_public"
    rate = settings.ORCA_PUBLIC_API_RATE_LIMIT

    def get_cache_key(self, request, view):
        # request.auth is the API key itself. It is hashed rather than used, so
        # the cache never holds a credential — a cache dump should not be a
        # list of working tokens.
        token = getattr(request, "auth", None)
        if token:
            digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:32]
            return f"{self.scope}:token:{digest}"
        # Unauthenticated calls never get past authentication anyway; keying
        # them by IP keeps an unauthenticated flood from being free.
        return f"{self.scope}:ip:{self.get_ident(request)}"
