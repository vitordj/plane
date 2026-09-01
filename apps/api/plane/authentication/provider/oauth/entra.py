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
"""

# Python imports
import base64
import json
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytz

# Module imports
from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)
from plane.authentication.adapter.oauth import OauthAdapter
from plane.license.utils.instance_value import get_configuration_value

# Tenant placeholders Microsoft accepts that defeat the guarantee above.
MULTI_TENANT_AUTHORITIES = {"common", "organizations", "consumers"}


def decode_id_token_claims(id_token: str) -> dict:
    """
    Read the claims of an OIDC id token without verifying its signature.

    @description Safe in this flow, and only in this flow: the token was
    fetched by Plane itself from Microsoft's token endpoint over TLS in direct
    response to the authorization code, which OpenID Connect Core §3.1.3.7
    accepts as sufficient. The claims are used for one purpose — confirming the
    token came from the configured tenant — never to authenticate the user.

    @param id_token: The compact JWS from the token response.
    @returns: The decoded payload, or an empty dict when it cannot be read.
    """
    try:
        payload = id_token.split(".")[1]
        # JWT uses unpadded base64url; restore the padding before decoding.
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (AttributeError, IndexError, ValueError, TypeError, UnicodeDecodeError):
        return {}


class EntraOAuthProvider(OauthAdapter):
    provider = "entra"
    # `User.Read` is what Microsoft Graph needs for /me; `offline_access` is
    # what makes Entra return a refresh token, which the Account row stores.
    scope = "openid email profile User.Read offline_access"
    userinfo_url = "https://graph.microsoft.com/v1.0/me"

    def __init__(self, request, code=None, state=None, callback=None):
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

        self.tenant_id = str(ENTRA_TENANT_ID).strip()
        self.token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

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
        self.id_token_claims = decode_id_token_claims(id_token)
        self.verify_tenant()

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

    def verify_tenant(self):
        """
        Confirm the token really came from the configured tenant.

        @description The whole trust argument for accepting Entra's email rests
        on the sign-in having happened inside one known tenant. A ``tid`` that
        does not match means the token was minted somewhere else, so the email
        it carries proves nothing and the sign-in is refused.

        @raises AuthenticationException: When the tenant claim is absent or
            does not match the configured tenant.
        """
        tid = str(self.id_token_claims.get("tid", "")).strip().lower()
        if not tid or tid != self.tenant_id.lower():
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_OAUTH_PROVIDER_ERROR"],
                error_message="ENTRA_OAUTH_PROVIDER_ERROR",
            )

    def resolve_email(self, user_info_response) -> str:
        """
        Pick the address to identify the account by.

        @description ``mail`` is the tenant's real mailbox attribute and is
        preferred. ``userPrincipalName`` is the fallback, but only when it is a
        genuine address: guest accounts carry a mangled UPN
        (``someone_example.com#EXT#@tenant.onmicrosoft.com``) which is an
        internal identifier, not a mailbox, and matching an account on it would
        be wrong.

        @param user_info_response: The Microsoft Graph ``/me`` payload.
        @returns: The email to sign in with.
        @raises AuthenticationException: When no usable address is present.
        """
        mail = (user_info_response.get("mail") or "").strip()
        if mail:
            return mail

        upn = (user_info_response.get("userPrincipalName") or "").strip()
        if upn and "@" in upn and "#EXT#" not in upn.upper():
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
