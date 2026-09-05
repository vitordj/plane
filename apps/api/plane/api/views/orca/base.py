# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Shared base for the Orca public automation API under ``/api/v1/orca/``.

Everything here is about the three things every endpoint in the namespace has
to get right before it does any work: the instance has the feature switched on,
the caller's token is resolved into something the audit trail can point at, and
the caller's traffic is metered against that token.
"""

# Third party imports
from rest_framework.exceptions import NotFound

# Module imports
from plane.api.views.base import BaseAPIView
from plane.app.services.orca.feature_flags import orca_public_api_enabled
from plane.db.models import APIToken
from plane.throttles.orca_public import OrcaPublicThrottle
from plane.utils.orca_error_codes import ORCA_ERROR_CODES, ORCA_ERROR_MESSAGES


class OrcaPublicApiFeatureMixin:
    """
    Two kill switches in front of the automation API.

    @description ``ORCA_ORG_UNITS_ENABLED`` off means the organizational layer
    is gone, so an API that allocates work through it has nothing to talk to.
    ``ORCA_PUBLIC_API_ENABLED`` off means the layer is running for people in
    the app but not for machines holding an API key — which is the state this
    instance ships in, and the state production stays in until Gate 2-minimum.

    Answers 404, like ``OrganizationalUnitFeatureMixin``: a disabled feature
    should read as absent, not as something the caller merely lacks rights for.
    A 403 would tell an unauthorized reader that the endpoint exists.

    Unlike the internal mixin it carries a coded body, because the callers here
    are programs: ``ORG_PUBLIC_API_DISABLED`` lets an integration tell "this
    instance has the API switched off" from "you got the URL wrong", which a
    bare 404 cannot express.
    """

    def initial(self, request, *args, **kwargs):
        if not orca_public_api_enabled():
            raise NotFound(
                {
                    "error": ORCA_ERROR_MESSAGES["ORG_PUBLIC_API_DISABLED"],
                    "error_code": ORCA_ERROR_CODES["ORG_PUBLIC_API_DISABLED"],
                    "error_message": "ORG_PUBLIC_API_DISABLED",
                }
            )
        return super().initial(request, *args, **kwargs)


class OrcaPublicBaseAPIView(OrcaPublicApiFeatureMixin, BaseAPIView):
    """
    Base for every ``/api/v1/orca/`` endpoint.

    @description Inherits the public API's ``APIKeyAuthentication``, so the
    caller is the token's user and their workspace and project roles are the
    permissions that apply — the automation API grants nothing of its own.

    Adds ``api_token``: the ``APIToken`` row behind ``request.auth``.
    ``APIKeyAuthentication`` returns the raw token string rather than the
    object, and two things here need the object — the throttle wants a stable
    id to key on, and ``AutomationOperation`` records which credential made a
    change. Resolved once per request and cached, so neither costs a second
    query.
    """

    def get_throttles(self):
        return [OrcaPublicThrottle()]

    @property
    def api_token(self):
        """
        @description The credential behind this request, or ``None`` when the
        request was not authenticated by an API key (a session-authenticated
        call, or an anonymous one that has not been rejected yet).
        @returns An ``APIToken`` instance or ``None``.
        """
        if hasattr(self, "_api_token"):
            return self._api_token

        raw = getattr(self.request, "auth", None)
        self._api_token = APIToken.objects.filter(token=raw).first() if raw else None
        return self._api_token
