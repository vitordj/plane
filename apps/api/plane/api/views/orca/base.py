# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The base every public automation view sits on.

Two switches have to be on for these routes to exist: the organizational layer
itself, and the public API on top of it. Either one off and the routes answer
**404**, not 403 — a workspace that has not enabled this should not be able to
learn the endpoints are there, and an operator turning the API off during an
incident wants it gone, not merely refusing.
"""

# Django imports
from django.conf import settings
from django.http import Http404

# Module imports
from plane.throttles.orca_public import OrcaPublicRateThrottle

from ..base import BaseAPIView


def orca_public_api_enabled() -> bool:
    """
    @description Whether the public automation API is switched on. Requires
    the organizational layer too: the API is a door onto that layer, and a
    door onto something switched off is worse than no door.
    @returns: ``True`` when both flags are on.
    """
    return bool(getattr(settings, "ORCA_ORG_UNITS_ENABLED", True)) and bool(
        getattr(settings, "ORCA_PUBLIC_API_ENABLED", False)
    )


class OrcaPublicApiFeatureMixin:
    """Makes the view's routes disappear when the API is off."""

    def initial(self, request, *args, **kwargs):
        if not orca_public_api_enabled():
            raise Http404()
        return super().initial(request, *args, **kwargs)


class OrcaPublicBaseAPIView(OrcaPublicApiFeatureMixin, BaseAPIView):
    """
    Public automation view: API-key authentication, own rate limit.

    @description Inherits the v1 API's authentication, so a token's permission
    is its user's permission — an automation can never do more than the person
    whose token it carries.
    """

    def get_throttles(self):
        return [OrcaPublicRateThrottle()]
