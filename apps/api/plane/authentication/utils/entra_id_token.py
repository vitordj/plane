# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Verification of Microsoft Entra ID id tokens.

The fork used to read the id token's payload without checking its signature,
on the argument from OpenID Connect Core §3.1.3.7 that a token fetched by the
server itself, over TLS, straight from the token endpoint needs no further
proof. That argument holds only as long as nothing else can reach the code
path — and it puts the entire sign-in on one assumption, with the tenant claim
(``tid``) as the only thing standing between an attacker's own Azure tenant
and somebody else's account.

So the token is verified properly: Microsoft's published signing key for the
tenant, the audience this client registered as, the issuer the tenant mints
under, and the lifetime claims. Then the nonce, which is what ties the token
to the browser that started this sign-in.

Kept free of Django imports on purpose, so the rules can be tested without
standing up a database.
"""

# Python imports
import jwt
from jwt import PyJWKClient

# Module imports
from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)

# Microsoft publishes one key set per tenant; keys rotate, so the client caches
# them and refetches on an unknown `kid` rather than on every sign-in.
JWKS_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/v2.0"

# A hung metadata endpoint must not hold a worker open through a sign-in.
JWKS_TIMEOUT_SECONDS = 10

# Entra signs id tokens with RS256. Listing it explicitly is what stops the
# "alg: none" and HMAC-with-the-public-key substitutions.
ALLOWED_ALGORITHMS = ["RS256"]

# Claims without which the token cannot be reasoned about at all.
REQUIRED_CLAIMS = ["exp", "iat", "nbf", "aud", "iss", "tid"]

_JWKS_CLIENTS: dict[str, PyJWKClient] = {}


def jwks_client(tenant_id: str) -> PyJWKClient:
    """
    Return the cached key client for a tenant.

    @description One client per tenant, kept for the life of the process: it
    holds the fetched key set, so signing in does not refetch Microsoft's
    metadata every time.
    @param tenant_id: The configured Entra tenant.
    @returns: A ``PyJWKClient`` for that tenant's key set.
    """
    client = _JWKS_CLIENTS.get(tenant_id)
    if client is None:
        client = PyJWKClient(
            JWKS_URL_TEMPLATE.format(tenant_id=tenant_id),
            cache_keys=True,
            timeout=JWKS_TIMEOUT_SECONDS,
        )
        _JWKS_CLIENTS[tenant_id] = client
    return client


def verify_id_token(id_token, *, tenant_id, audience, expected_nonce=None):
    """
    Verify an Entra id token and return its claims.

    @description Checks the signature against Microsoft's published key for the
    tenant, that the token was minted for this client (``aud``) by this tenant
    (``iss``), that it is inside its lifetime, and that it carries the nonce
    this sign-in started with. Anything short of all of that is refused: the
    email in an unverified token proves nothing, and the account it names is
    somebody's.
    @param id_token: The compact JWS from the token response.
    @param tenant_id: The configured Entra tenant id.
    @param audience: This instance's Entra client id.
    @param expected_nonce: The nonce stored when the sign-in was initiated.
        ``None`` means the browser has no nonce for this flow, which is itself
        a refusal — a sign-in that cannot be tied to the browser that began it
        is exactly what the nonce exists to catch.
    @returns: The verified claims.
    @raises AuthenticationException: ``ENTRA_ID_TOKEN_INVALID`` when the token
        does not verify, ``ENTRA_NONCE_MISMATCH`` when it verifies but belongs
        to a different sign-in.
    """
    if not id_token or not isinstance(id_token, str):
        raise AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["ENTRA_ID_TOKEN_INVALID"],
            error_message="ENTRA_ID_TOKEN_INVALID",
        )

    try:
        signing_key = jwks_client(tenant_id).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=audience,
            issuer=ISSUER_TEMPLATE.format(tenant_id=tenant_id),
            options={"require": REQUIRED_CLAIMS},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError, ValueError, TypeError, KeyError):
        # The reason is deliberately not echoed to the browser: which of these
        # failed is useful to an attacker probing the endpoint, and useless to
        # the person trying to sign in. The provider logs it.
        raise AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["ENTRA_ID_TOKEN_INVALID"],
            error_message="ENTRA_ID_TOKEN_INVALID",
        )

    presented_nonce = claims.get("nonce")
    if not expected_nonce or presented_nonce != expected_nonce:
        raise AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["ENTRA_NONCE_MISMATCH"],
            error_message="ENTRA_NONCE_MISMATCH",
        )

    return claims
