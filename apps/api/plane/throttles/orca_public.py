# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Rate limit for the Orca public automation API.

One budget per API token. An integration that loops — a webhook handler that
retries without backoff, a migration script pointed at the wrong queue — is the
realistic failure here, not an attacker: the endpoints need a valid token
before they do anything. The limit exists so one misbehaving integration
cannot saturate the API for the rest of the instance.
"""

# Django imports
from django.conf import settings

# Third party imports
from rest_framework.throttling import SimpleRateThrottle


class OrcaPublicThrottle(SimpleRateThrottle):
    """
    Budget for one API token's automation traffic.

    @description Keyed on the token, not on the caller's address. Every call
    from one integration arrives from the same host, so an address-keyed limit
    would have one workspace's automation spend another's budget — and a
    token-keyed one also survives an integration that moves hosts.

    Read off the view, the way ``SCIMRateThrottle`` reads its connection, and
    keyed on the token's id rather than on the token itself. ``request.auth``
    holds the raw secret for this authentication class, and a cache key is not
    a secret-safe place: keys surface in Redis monitoring, slow-log output and
    crash dumps.

    Returns ``None`` when there is no authenticated token, which makes
    ``SimpleRateThrottle`` allow the request rather than charge a shared
    counter. That is deliberate: an unauthenticated caller must not be able to
    reach a bucket that a real token's traffic also draws on, which would be a
    way to switch off a workspace's automation from outside. Unauthenticated
    requests never get past ``APIKeyAuthentication`` anyway, and the anonymous
    ceiling still applies to them.
    """

    scope = "orca_public"
    rate = settings.ORCA_PUBLIC_API_RATE_LIMIT

    def get_cache_key(self, request, view):
        token = getattr(view, "api_token", None)
        token_id = getattr(token, "id", None)
        if token_id is None:
            return None
        return f"{self.scope}:{token_id}"
