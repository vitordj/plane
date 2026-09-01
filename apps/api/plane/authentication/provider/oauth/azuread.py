# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

import jwt
import pytz

# Module imports
from plane.authentication.adapter.oauth import OauthAdapter
from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)
from plane.license.utils.instance_value import get_configuration_value

# Tenant values that deliberately accept sign-ins from more than one directory.
# With any of these the `tid` claim cannot be pinned to a single tenant, so the
# instance admin is opting into whoever Microsoft lets through.
MULTI_TENANT_VALUES = frozenset({"common", "organizations", "consumers"})

# A tenant is addressed either by its directory GUID or by a verified domain.
TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

AUTHORITY = "https://login.microsoftonline.com"
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"

# A guest (B2B) account carries the home-tenant address encoded in its UPN
# rather than a mailbox in this directory.
GUEST_UPN_MARKER = "#EXT#"


class AzureADOAuthProvider(OauthAdapter):
    provider = "azuread"
    # openid/email/profile yield the id_token claims; User.Read is what Graph
    # requires for /me. offline_access is what makes Entra return a refresh
    # token, which the Account row stores like every other provider's.
    scope = "openid email profile offline_access User.Read"

    def __init__(self, request, code=None, state=None, callback=None):
        (
            AZUREAD_CLIENT_ID,
            AZUREAD_CLIENT_SECRET,
            AZUREAD_TENANT_ID,
        ) = get_configuration_value(
            [
                {
                    "key": "AZUREAD_CLIENT_ID",
                    "default": os.environ.get("AZUREAD_CLIENT_ID"),
                },
                {
                    "key": "AZUREAD_CLIENT_SECRET",
                    "default": os.environ.get("AZUREAD_CLIENT_SECRET"),
                },
                {
                    "key": "AZUREAD_TENANT_ID",
                    "default": os.environ.get("AZUREAD_TENANT_ID"),
                },
            ]
        )

        if not (AZUREAD_CLIENT_ID and AZUREAD_CLIENT_SECRET and AZUREAD_TENANT_ID):
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["AZUREAD_NOT_CONFIGURED"],
                error_message="AZUREAD_NOT_CONFIGURED",
            )

        tenant = str(AZUREAD_TENANT_ID).strip().strip("/")
        # The tenant lands in the authority URL, so anything that could carry a
        # path or a scheme is rejected rather than escaped.
        if not TENANT_PATTERN.match(tenant):
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["AZUREAD_NOT_CONFIGURED"],
                # Avoid echoing the configured value into query params.
                error_message="AZUREAD_NOT_CONFIGURED",
            )

        self.tenant = tenant
        self.id_token_claims = {}
        self.token_url = f"{AUTHORITY}/{tenant}/oauth2/v2.0/token"
        self.userinfo_url = GRAPH_ME_URL

        client_id = AZUREAD_CLIENT_ID
        client_secret = AZUREAD_CLIENT_SECRET

        redirect_uri = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}/auth/azuread/callback/"
        url_params = {
            "client_id": client_id,
            "scope": self.scope,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "state": state,
        }
        auth_url = f"{AUTHORITY}/{tenant}/oauth2/v2.0/authorize?{urlencode(url_params)}"

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
        self.id_token_claims = self.__validate_id_token(id_token)

        expires_in = token_response.get("expires_in")
        super().set_token_data(
            {
                "access_token": token_response.get("access_token"),
                "refresh_token": token_response.get("refresh_token", None),
                "access_token_expired_at": (
                    datetime.now(tz=pytz.utc) + timedelta(seconds=int(expires_in)) if expires_in else None
                ),
                # Entra does not publish a refresh token lifetime; it is governed
                # by tenant policy and revocation, not by a value we can store.
                "refresh_token_expired_at": None,
                "id_token": id_token,
            }
        )

    def __validate_id_token(self, id_token):
        """Check that the id_token was issued for this app by the configured tenant.

        The token arrives on the back channel: a direct TLS POST to the tenant's
        own token endpoint, authenticated with the client secret. OIDC Core
        3.1.3.7 lets a client skip signature verification in exactly that case,
        so the claims are read without fetching JWKS — but the claims that decide
        *who* is signing in are still enforced here.

        The `tid` check is the one that matters: with a single-tenant
        configuration it is what stops an account from an unrelated Entra
        directory from completing a sign-in against this instance.
        """
        if not id_token:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["AZUREAD_OAUTH_PROVIDER_ERROR"],
                error_message="AZUREAD_OAUTH_PROVIDER_ERROR",
            )

        try:
            claims = jwt.decode(
                id_token,
                options={"verify_signature": False, "verify_aud": False, "verify_exp": True},
                algorithms=["RS256"],
            )
        except jwt.PyJWTError:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["AZUREAD_OAUTH_PROVIDER_ERROR"],
                error_message="AZUREAD_OAUTH_PROVIDER_ERROR",
            )

        # The token must have been minted for this application.
        if claims.get("aud") != self.client_id:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["AZUREAD_OAUTH_PROVIDER_ERROR"],
                error_message="AZUREAD_OAUTH_PROVIDER_ERROR",
            )

        if self.tenant.lower() not in MULTI_TENANT_VALUES:
            tid = str(claims.get("tid", ""))
            issuer = str(claims.get("iss", ""))
            if GUID_PATTERN.match(self.tenant):
                # Configured by directory GUID: `tid` must be that directory.
                tenant_ok = tid.lower() == self.tenant.lower()
            else:
                # Configured by domain, which never appears in `tid`. The pin is
                # the authority URL — Entra resolved the domain to one directory
                # and minted this token there — so all that is left to confirm
                # is that the token names a directory and agrees with itself.
                tenant_ok = bool(tid) and issuer.startswith(f"{AUTHORITY}/{tid}/")
            if not tenant_ok:
                raise AuthenticationException(
                    error_code=AUTHENTICATION_ERROR_CODES["AZUREAD_TENANT_MISMATCH"],
                    error_message="AZUREAD_TENANT_MISMATCH",
                )

        return claims

    def __get_email(self, user_info_response):
        """Resolve the directory address to sign the person in as.

        `mail` is the mailbox the tenant assigned. `userPrincipalName` is the
        fallback for accounts without one, but only when it is a real address in
        this directory: a guest's UPN encodes a foreign home tenant and is not a
        deliverable address, so it is refused rather than used to match an
        existing Plane account.
        """
        email = user_info_response.get("mail")
        if email:
            return email

        upn = user_info_response.get("userPrincipalName") or ""
        if upn and GUEST_UPN_MARKER not in upn.upper() and "@" in upn:
            return upn

        # Fall back to the id_token claims for accounts Graph reports without
        # either attribute.
        claims = getattr(self, "id_token_claims", {}) or {}
        for claim in ("email", "preferred_username"):
            value = claims.get(claim)
            if value and "@" in str(value) and GUEST_UPN_MARKER not in str(value).upper():
                return value

        raise AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["AZUREAD_NO_EMAIL"],
            error_message="AZUREAD_NO_EMAIL",
        )

    def sync_user_data(self, user):
        """Sync the directory's name onto the account, and nothing else.

        The shared implementation also re-derives the avatar, which for this
        provider means deleting whatever the person uploaded and putting
        nothing in its place: Entra's photo sits behind an authenticated Graph
        endpoint that `set_user_data` deliberately does not hand over. It also
        rewrites the display name from the email on every login. Neither is
        something the directory is authoritative about, so with
        `ENABLE_AZUREAD_SYNC` on, only the names Entra actually supplies move.
        """
        first_name = self.user_data.get("user", {}).get("first_name", "")
        last_name = self.user_data.get("user", {}).get("last_name", "")
        user.first_name = first_name if first_name else ""
        user.last_name = last_name if last_name else ""
        user.save()
        return user

    def set_user_data(self):
        user_info_response = self.get_user_response()
        claims = getattr(self, "id_token_claims", {}) or {}

        email = self.__get_email(user_info_response)

        # `id` from Graph is the directory object id, the same value the
        # id_token carries as `oid`: stable across renames and email changes,
        # which is what the Account row needs to key on.
        provider_id = user_info_response.get("id") or claims.get("oid") or claims.get("sub")

        first_name = user_info_response.get("givenName") or claims.get("given_name") or ""
        last_name = user_info_response.get("surname") or claims.get("family_name") or ""
        if not first_name and not last_name:
            first_name = user_info_response.get("displayName") or claims.get("name") or ""

        # No avatar: the Graph photo endpoint needs the access token on the
        # request, and the shared avatar fetcher replays its headers across
        # every redirect hop, which would hand the token to whichever host
        # Graph redirects the blob to.
        super().set_user_data(
            {
                "email": email,
                "user": {
                    "provider_id": str(provider_id) if provider_id else None,
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_password_autoset": True,
                },
            }
        )
