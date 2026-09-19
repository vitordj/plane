# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Microsoft Entra ID (formerly Azure AD) OAuth 2.0 / OIDC provider.

Signing in with Entra is the other half of the fork's directory story: SCIM
decides who belongs to which area, and this decides who may sign in at all.
The two are independent — either can be enabled without the other — but they
match people by the same email, so a tenant that runs both ends up with users
who sign in with their corporate account and land in the areas their Entra
groups put them in.

**Why a tenant id is required.** Plane matches an OAuth identity to an account
by email, so a provider that can assert an arbitrary email can take over an
existing account (the class of issue behind GHSA-7j95-vh8g-f365). Microsoft
only guarantees that a user's identity belongs to *some* tenant, so pointing
this provider at the multi-tenant ``common`` endpoint would let anyone with
their own Azure tenant assert any address. Pinning a tenant id — and verifying
the ``tid`` claim of the returned token against it — is what makes the asserted
email trustworthy, so the configuration deliberately has no ``common`` default.

**Why the id token is verified in full.** The token arrives over TLS straight
from Microsoft's token endpoint, which OpenID Connect Core §3.1.3.7 accepts as
a reason to skip signature validation — and this provider used to take that
shortcut, reading the payload with base64 and checking only ``tid``. The
shortcut buys nothing here and costs the defence in depth: it makes every
claim depend on the transport being what the code assumes (no proxy in the
middle, no misconfigured endpoint, no cached response), and it leaves ``aud``,
``iss``, ``exp`` and ``nbf`` unchecked, so a token minted for a different
application, or one that expired hours ago, would be accepted. The token is
now verified against the tenant's published signing keys with every one of
those claims required, and the tenant check stays on top of it.

**Why the tenant must be the GUID.** Microsoft accepts either the GUID or a
verified domain in the authority URL, but the ``tid`` claim and the ``iss``
claim always carry the GUID. Both checks compare against the configured value,
so a tenant configured by domain would refuse every sign-in. The constructor
rejects it up front instead, where the message can say why.
"""

# Python imports
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

import jwt
import pytz
from jwt import PyJWKClient

# Module imports
from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)
from plane.authentication.adapter.oauth import OauthAdapter
from plane.license.utils.instance_value import get_configuration_value

# Tenant placeholders Microsoft accepts that defeat the guarantee above.
MULTI_TENANT_AUTHORITIES = {"common", "organizations", "consumers"}

# The tenant id as it appears in the ``tid`` and ``iss`` claims.
TENANT_GUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

JWKS_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenant}/v2.0"

# Claims the token has to carry. Without ``require``, PyJWT validates only the
# claims that happen to be present, so a token with no ``exp`` would never be
# considered expired and one with no ``aud`` would never fail the audience
# check — the two failures this validation exists to catch.
REQUIRED_ID_TOKEN_CLAIMS = ["exp", "iat", "nbf", "aud", "iss", "tid"]

# How long to wait on the JWKS endpoint. It is on the sign-in path, so a
# Microsoft outage has to fail rather than hold the worker open.
JWKS_TIMEOUT_SECONDS = 10

# One client per tenant, kept for the life of the process: PyJWKClient caches
# the key set (five minutes by default) and re-fetches only when a token
# arrives signed by a key it has not seen, which is what makes Entra's key
# rotation transparent without a request to Microsoft on every sign-in.
_JWKS_CLIENTS: dict = {}


def get_jwks_client(tenant_id: str) -> PyJWKClient:
    """
    @description The cached JWKS client for one tenant.
    @param tenant_id: Tenant GUID the keys are published under.
    @returns: A ``PyJWKClient`` shared by every sign-in for that tenant.
    """
    client = _JWKS_CLIENTS.get(tenant_id)
    if client is None:
        client = PyJWKClient(
            JWKS_URL_TEMPLATE.format(tenant=tenant_id),
            cache_keys=True,
            timeout=JWKS_TIMEOUT_SECONDS,
        )
        _JWKS_CLIENTS[tenant_id] = client
    return client


class EntraOAuthProvider(OauthAdapter):
    provider = "entra"
    # `User.Read` is what Microsoft Graph needs for /me; `offline_access` is
    # what makes Entra return a refresh token, which the Account row stores.
    scope = "openid email profile User.Read offline_access"
    userinfo_url = "https://graph.microsoft.com/v1.0/me"

    # Where the one-time value that ties an id token to this browser's sign-in
    # attempt is kept. Same session as `state`, which the views compare.
    NONCE_SESSION_KEY = "entra_nonce"

    def __init__(self, request, code=None, state=None, nonce=None, callback=None):
        (ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET, ENTRA_TENANT_ID) = get_configuration_value(
            [
                {
                    "key": "ENTRA_CLIENT_ID",
                    "default": os.environ.get("ENTRA_CLIENT_ID"),
                },
                {
                    "key": "ENTRA_CLIENT_SECRET",
                    "default": os.environ.get("ENTRA_CLIENT_SECRET"),
                },
                {
                    "key": "ENTRA_TENANT_ID",
                    "default": os.environ.get("ENTRA_TENANT_ID"),
                },
            ]
        )

        if not (ENTRA_CLIENT_ID and ENTRA_CLIENT_SECRET and ENTRA_TENANT_ID):
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_NOT_CONFIGURED"],
                error_message="ENTRA_NOT_CONFIGURED",
            )

        # A multi-tenant authority would let any Azure tenant assert any email.
        # Refusing it here means the misconfiguration surfaces at sign-in rather
        # than as a silent account-takeover path.
        if str(ENTRA_TENANT_ID).strip().lower() in MULTI_TENANT_AUTHORITIES:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_NOT_CONFIGURED"],
                error_message="ENTRA_NOT_CONFIGURED",
            )

        # A tenant configured by domain name would fail every `tid` and `iss`
        # comparison after the round trip to Microsoft. Refusing it here turns
        # that into a configuration error instead of a sign-in that never works.
        if not TENANT_GUID_PATTERN.match(str(ENTRA_TENANT_ID).strip()):
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_NOT_CONFIGURED"],
                error_message="ENTRA_NOT_CONFIGURED",
            )

        self.tenant_id = str(ENTRA_TENANT_ID).strip()
        self.token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        self.id_token_claims = {}

        client_id = ENTRA_CLIENT_ID
        client_secret = ENTRA_CLIENT_SECRET

        redirect_uri = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}/auth/entra/callback/"
        url_params = {
            "client_id": client_id,
            "scope": self.scope,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "state": state,
        }
        # Only the authorization request carries a nonce; the callback builds
        # the same provider without one and compares against the session.
        if nonce:
            url_params["nonce"] = nonce
        auth_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?{urlencode(url_params)}"

        super().__init__(
            request,
            self.provider,
            client_id,
            self.scope,
            redirect_uri,
            auth_url,
            self.token_url,
            self.userinfo_url,
            client_secret,
            code,
            callback=callback,
        )

    def set_token_data(self):
        data = {
            "code": self.code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
            "scope": self.scope,
        }
        headers = {"Accept": "application/json"}
        token_response = self.get_user_token(data=data, headers=headers)

        id_token = token_response.get("id_token", "")
        self.id_token_claims = self.decode_id_token(id_token)
        self.verify_tenant()
        self.verify_nonce()

        super().set_token_data(
            {
                "access_token": token_response.get("access_token"),
                "refresh_token": token_response.get("refresh_token", None),
                "access_token_expired_at": (
                    datetime.now(tz=pytz.utc) + timedelta(seconds=token_response.get("expires_in"))
                    if token_response.get("expires_in")
                    else None
                ),
                # Entra expresses refresh token lifetime as a duration, and only
                # on some token types; absent means "no stated expiry".
                "refresh_token_expired_at": (
                    datetime.now(tz=pytz.utc) + timedelta(seconds=token_response.get("refresh_token_expires_in"))
                    if token_response.get("refresh_token_expires_in")
                    else None
                ),
                "id_token": id_token,
            }
        )

    def decode_id_token(self, id_token: str) -> dict:
        """
        Verify the id token against the tenant's signing keys and return its
        claims.

        @description Signature (RS256 against the tenant's published JWKS),
        audience (this application's client id), issuer (this tenant), and the
        time window are all checked, and every claim the later checks depend on
        is required to be present — a token missing ``exp`` would otherwise
        never be considered expired.

        @param id_token: The compact JWS from the token response.
        @returns: The verified claims.
        @raises AuthenticationException: When the token is absent, malformed,
            signed by a key this tenant does not publish, minted for another
            application or tenant, outside its validity window, or missing a
            required claim. Also when the key set cannot be fetched: an
            unverifiable token is refused rather than trusted.
        """
        if not id_token:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_ID_TOKEN_INVALID"],
                error_message="ENTRA_ID_TOKEN_INVALID",
            )

        try:
            signing_key = get_jwks_client(self.tenant_id).get_signing_key_from_jwt(id_token)
            return jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=ISSUER_TEMPLATE.format(tenant=self.tenant_id),
                options={"require": REQUIRED_ID_TOKEN_CLAIMS},
            )
        except Exception:
            # Deliberately broad: PyJWT raises its own hierarchy for a bad
            # token and urllib errors for an unreachable key set, and both mean
            # the same thing here — this token cannot be trusted. The reason is
            # logged, never returned: it would tell an attacker which check
            # they still have to get past.
            self.logger.warning("Entra id token failed verification", exc_info=True)
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_ID_TOKEN_INVALID"],
                error_message="ENTRA_ID_TOKEN_INVALID",
            )

    def verify_tenant(self):
        """
        Confirm the token really came from the configured tenant.

        @description Redundant with the ``iss`` check in ``decode_id_token``
        and kept on purpose: the whole trust argument for accepting Entra's
        email rests on the sign-in having happened inside one known tenant, and
        that argument should not depend on a single library option being passed
        correctly. A ``tid`` that does not match means the token was minted
        somewhere else, so the email it carries proves nothing.

        @raises AuthenticationException: When the tenant claim is absent or
            does not match the configured tenant.
        """
        tid = str(self.id_token_claims.get("tid", "")).strip().lower()
        if not tid or tid != self.tenant_id.lower():
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_OAUTH_PROVIDER_ERROR"],
                error_message="ENTRA_OAUTH_PROVIDER_ERROR",
            )

    def verify_nonce(self):
        """
        Confirm this token was minted for this browser's sign-in attempt.

        @description ``state`` protects the callback URL from being replayed at
        the browser; the nonce protects the token itself. Without it, an id
        token captured from another sign-in — same tenant, same application,
        different person — is indistinguishable from one this flow just
        requested. The value is single use: it is removed from the session
        whether or not it matches, so a captured callback cannot be replayed
        against the same session.

        @raises AuthenticationException: When the session has no nonce (the
            flow did not start here, or started before this was deployed) or
            the token carries a different one.
        """
        session = getattr(self.request, "session", None)
        expected = ""
        if session is not None:
            expected = str(session.pop(self.NONCE_SESSION_KEY, "") or "")

        presented = str(self.id_token_claims.get("nonce", "") or "")

        if not expected or presented != expected:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_NONCE_MISMATCH"],
                error_message="ENTRA_NONCE_MISMATCH",
            )

    def resolve_email(self, user_info_response) -> str:
        """
        Pick the address to identify the account by.

        @description Plane matches an OAuth identity to an account by email, so
        the address chosen here is the entire trust boundary. Two rules keep it
        inside the configured tenant.

        **Guests are refused, whatever they present.** A B2B guest belongs to
        another tenant and is merely present in this one, so the address Graph
        reports for them is one their home tenant controls — and in a default
        Entra configuration any member can invite a guest, which would make the
        set of people who can sign in much wider than the set of people the
        tenant employs. The ``#EXT#`` marker in ``userPrincipalName`` is what
        identifies them, and it is there whether or not ``mail`` is populated:
        modern tenants do populate ``mail`` for guests with their external
        address, so checking the UPN only as a fallback would let exactly the
        case this rule exists for walk straight through.

        For a member, ``mail`` is the tenant's real mailbox attribute and is
        preferred; ``userPrincipalName`` is the fallback when it is a genuine
        address.

        @param user_info_response: The Microsoft Graph ``/me`` payload.
        @returns: The email to sign in with.
        @raises AuthenticationException: When the caller is a guest, or no
            usable address is present.
        """
        upn = (user_info_response.get("userPrincipalName") or "").strip()
        if "#EXT#" in upn.upper():
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OAUTH_PROVIDER_UNVERIFIED_EMAIL"],
                error_message="OAUTH_PROVIDER_UNVERIFIED_EMAIL",
            )

        mail = (user_info_response.get("mail") or "").strip()
        if mail:
            return mail

        if upn and "@" in upn:
            return upn

        # A tenant that maps neither is a configuration problem, not a user
        # error — but it must never fall through to an unverified address.
        raise AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["OAUTH_PROVIDER_UNVERIFIED_EMAIL"],
            error_message="OAUTH_PROVIDER_UNVERIFIED_EMAIL",
        )

    def set_user_data(self):
        user_info_response = self.get_user_response()
        email = self.resolve_email(user_info_response)

        super().set_user_data(
            {
                "email": email,
                "user": {
                    "provider_id": str(user_info_response.get("id") or self.id_token_claims.get("oid") or ""),
                    "email": email,
                    # Graph /me carries no photo URL — the picture lives at a
                    # separate binary endpoint — so avatars stay unset rather
                    # than pointing at something that will not render.
                    "avatar": "",
                    "first_name": (user_info_response.get("givenName") or user_info_response.get("displayName") or ""),
                    "last_name": user_info_response.get("surname") or "",
                    "is_password_autoset": True,
                },
            }
        )
