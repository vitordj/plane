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
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

# Module imports
from plane.api.views.base import BaseAPIView
from plane.app.services.orca.errors import IdempotencyKeyRequired, OrcaDomainError
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

    def handle_exception(self, exc):
        """
        @description Turn any refusal from the assignment layer into this
        namespace's error envelope, wherever in the request it was raised.

        Here rather than in each ``post``: the first version caught them only
        inside the idempotent block, so a refusal raised *before* the receipt
        was opened — a work item that is not in this project, a reassignment
        with no ``If-Match`` — fell through to the generic handler and answered
        **500** for two conditions the contract documents as 404 and 428. A
        route added later would have inherited the same hole.
        """
        if isinstance(exc, OrcaDomainError):
            body, http_status = domain_error_response(exc)
            return Response(body, status=http_status)
        return super().handle_exception(exc)

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


# The public API answers some refusals with a different status than the
# interface does. ``DecisionStale`` is the one case today: RFC §7.3 specifies
# 412 for a failed ``If-Match``, which is what a precondition header means over
# HTTP, while the internal route has always answered 409 to the web app.
# Changing the exception would change the interface's contract, so the mapping
# lives here — at the edge that speaks HTTP to machines.
PUBLIC_STATUS_OVERRIDES = {"ORG_DECISION_STALE": status.HTTP_412_PRECONDITION_FAILED}

# What an operation's receipt records when the body itself was malformed.
# Deliberately not an Orca error code: the useful information is DRF's
# field-by-field detail, which no single code could carry, and inventing one
# would put a number in three files and nineteen locales to say "look at the
# detail".
VALIDATION_ERROR = "VALIDATION_ERROR"

# The longest ``Idempotency-Key`` the receipt can hold.
MAX_IDEMPOTENCY_KEY = 255


def error_body(error_code, **payload):
    """
    @description The error envelope of the Orca layer, as a dict rather than a
    ``Response``, because every failure here is recorded on the receipt as well
    as sent — and a replay has to reproduce it byte for byte.
    @param error_code: A key of ``ORCA_ERROR_CODES``.
    @param payload: Extra fields the caller can act on — the winner of a
        contested claim, the work item already holding a binding. Ids only.
    @returns A JSON-serializable dict.
    """
    body = {
        "error": ORCA_ERROR_MESSAGES[error_code],
        "error_code": ORCA_ERROR_CODES[error_code],
        "error_message": error_code,
    }
    body.update(payload)
    return body


def public_status(exc):
    """@description The status this namespace answers a domain error with. @returns int."""
    return PUBLIC_STATUS_OVERRIDES.get(exc.error_code, exc.http_status)


def domain_error_response(exc):
    """@description Turn a service refusal into what the caller sees. @returns ``(body, status)``."""
    return error_body(exc.error_code, **exc.payload), public_status(exc)


def validation_error_response(exc):
    """
    @description Turn a serializer refusal into what the caller sees.
    @param exc: A DRF ``ValidationError``.
    @returns ``(body, status)``.
    """
    body = {
        "error": "The request body is not valid",
        "error_message": VALIDATION_ERROR,
        "detail": exc.detail,
    }
    return body, status.HTTP_400_BAD_REQUEST


def read_idempotency_key(request):
    """
    @description Read and check the header every mutation in this namespace
    requires (RFC §7.1).
    @param request: The DRF request.
    @returns The key.
    @raises IdempotencyKeyRequired: Missing, blank, or longer than the column
        that has to hold it. All three are the same answer on purpose — a key
        the server cannot store is a key that would not make the next retry
        idempotent, and a second error code would only tell the caller which
        way it broke a rule it has already broken.
    """
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key or len(key) > MAX_IDEMPOTENCY_KEY:
        raise IdempotencyKeyRequired()
    return key


def replay_response(handle):
    """
    @description Answer a retry with the response its first attempt got.
    @param handle: A replayed ``OperationHandle``.
    @returns A DRF ``Response`` carrying ``Idempotent-Replay: true``, so a
        client can tell "your work was done" from "your work was done, again"
        without diffing bodies.
    """
    body, http_status = handle.replay_response()
    if isinstance(body.get("operation"), dict):
        body["operation"]["replay"] = True
    response = Response(body, status=http_status)
    response["Idempotent-Replay"] = "true"
    return response
