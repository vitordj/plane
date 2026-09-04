# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Rate limits for the SCIM provisioning endpoints.

Two limits, because the two kinds of caller are not the same caller. A request
that proved it holds the workspace's bearer token spends that workspace's
provisioning budget; a request that did not prove anything is limited by where
it came from, and must never be able to spend somebody else's budget.
"""

# Django imports
from django.conf import settings

# Third party imports
from rest_framework.throttling import SimpleRateThrottle


class SCIMRateThrottle(SimpleRateThrottle):
    """
    Provisioning budget for one workspace, spent only by authenticated calls.

    @description The SCIM views authenticate with a workspace bearer token
    rather than a Plane session, so DRF sees every provisioning call as
    anonymous and the project-wide ``AnonRateThrottle`` applies its 30/minute
    to them. Microsoft Entra ID provisions in batches — one request per user
    and per group membership change — so a first sync of a real directory
    exceeds that in seconds and the rest of the run comes back 429. Entra
    retries, hits the same wall, and reports the connection as failing.

    Keyed on the workspace rather than the caller's address. Every tenant's
    provisioning traffic arrives from Microsoft's own ranges, so an IP-keyed
    limit would have one workspace's sync throttle another's.

    Applied by ``SCIMBaseView`` *after* the bearer token is verified, never
    through DRF's automatic pass. A shared counter that anyone could reach
    before authenticating would be a way to switch off a workspace's
    provisioning from outside: fill the bucket with bogus tokens and Entra's
    real calls come back 429 until the window rolls. Anonymous callers are
    metered by ``SCIMAuthFailureRateThrottle`` instead.

    A limit still applies to authenticated callers: the bearer token is the
    real protection, and a generous ceiling only keeps a misconfigured or
    looping provisioner from saturating the API.
    """

    scope = "scim"
    rate = settings.SCIM_RATE_LIMIT

    def get_cache_key(self, request, view):
        connection = getattr(view, "connection", None)
        if connection is None:
            # No authenticated connection: this throttle has no budget to
            # charge. Returning None makes SimpleRateThrottle allow the
            # request rather than fall back to a shared counter.
            return None
        return f"{self.scope}:{connection.workspace_id}"


class SCIMAuthFailureRateThrottle(SimpleRateThrottle):
    """
    Ceiling on failed provisioning authentications, keyed by address.

    @description Charged only when a caller fails to authenticate, so a
    correctly configured Entra tenant never meets it however large its
    directory. It caps two things a valid token would otherwise be needed for:
    guessing at the token, and holding a workspace's provisioning offline by
    exhausting its budget.

    Keyed on the caller's address rather than the workspace, which is the whole
    point — the workspace in the URL is attacker-chosen, and keying failures on
    it would recreate the denial-of-service this limit exists to close.
    """

    scope = "scim_auth_failure"
    rate = settings.SCIM_AUTH_FAILURE_RATE_LIMIT

    def get_cache_key(self, request, view):
        return f"{self.scope}:{self.get_ident(request)}"
