# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

import jwt
import pytz
import requests
from django.core.cache import cache
from jwt import PyJWKClient

# Module imports
from plane.authentication.adapter.oauth import OauthAdapter
from plane.license.utils.instance_value import get_configuration_value
from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)

# The route this provider's callback is served on. Kept in step with the
# "oidc-free-callback" URL pattern; the space flow reuses it, as the other
# providers do.
CALLBACK_PATH = "auth/oidc-free/callback/"

# Discovery documents change rarely, so cache them: an uncached login pays two
# round trips to the provider instead of one.
DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
DISCOVERY_CACHE_TIMEOUT = 60 * 60
DISCOVERY_TIMEOUT = 10

# PKCE and the nonce are minted on the authorization request and read back when
# the code is exchanged, so they live in the session in between.
SESSION_CODE_VERIFIER = "oidc_free_code_verifier"
SESSION_NONCE = "oidc_free_nonce"

# Asymmetric algorithms only: a symmetric one would let a provider sign an
# id_token with a key we also hold, and "none" would let anyone sign one.
ID_TOKEN_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
ID_TOKEN_LEEWAY = 60

# Microsoft's multi-tenant endpoints (/common, /organizations, /consumers) serve one
# discovery document for every tenant, so the issuer it declares is a template —
# "https://login.microsoftonline.com/{tenantid}/v2.0" — that each id_token fills in with
# its own tenant. Comparing an issuer like that literally rejects every sign-in, so the
# placeholders are matched instead: the rest of the issuer still has to be exactly what
# discovery declared, and a placeholder naming a claim is pinned to that claim's value.
ISSUER_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")
ISSUER_PLACEHOLDER_CLAIMS = {"tenantid": "tid", "tenant_id": "tid"}

# PyJWKClient caches the key set it fetches, so one client per URL keeps the
# JWKS out of the critical path of every sign-in.
_JWKS_CLIENTS = {}


def _seconds_or_none(value):
    """The value as a whole number of seconds, or None when it is not one.

    Providers are free to send the lifetimes in their token response as JSON strings,
    and several do, so the value is coerced rather than trusted to be a number.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _epoch_or_none(seconds):
    """A UTC datetime for an epoch timestamp, or None when it is out of range."""
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=pytz.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _jwks_client(jwks_url):
    client = _JWKS_CLIENTS.get(jwks_url)
    if client is None:
        client = PyJWKClient(jwks_url, cache_keys=True, timeout=DISCOVERY_TIMEOUT)
        _JWKS_CLIENTS[jwks_url] = client
    return client


class OidcFreeOAuthProvider(OauthAdapter):
    provider = "oidc-free"

    def __read_option_from_env(self, option: str, default: str | None = None):
        env_default = os.environ.get(option)
        return {
            "key": option,
            "default": env_default if not default else env_default or default,
        }

    @staticmethod
    def __not_configured():
        # The error code travels back in the query string, so it carries no
        # detail about which value is missing or malformed.
        return AuthenticationException(
            error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_NOT_CONFIGURED"],
            error_message="OIDC_FREE_NOT_CONFIGURED",
        )

    @classmethod
    def __validate_endpoint(cls, url):
        """Return the URL if it is an absolute http(s) URL, else raise."""
        if not url:
            raise cls.__not_configured()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise cls.__not_configured()
        return url

    @classmethod
    def __discover_endpoints(cls, discovery_url):
        """Read the authorization, token and userinfo endpoints from the
        provider's OpenID Connect discovery document.

        Providers do not have to serve all three endpoints from one host —
        Microsoft Entra ID, for one, serves userinfo from a different domain
        than authorize and token — so each endpoint is taken as the absolute
        URL the document declares.
        """
        if not discovery_url.endswith(DISCOVERY_SUFFIX):
            discovery_url = discovery_url.rstrip("/") + DISCOVERY_SUFFIX
        cls.__validate_endpoint(discovery_url)

        cache_key = f"oidc_free:discovery:{discovery_url}"
        try:
            document = cache.get(cache_key)
        except Exception:
            # A cache outage must not take sign-in with it.
            document = None

        if not document:
            try:
                # The discovery URL is set by an instance admin, exactly like
                # the token and userinfo URLs this provider already fetches,
                # so it is fetched with the same trust: a self-hosted provider
                # on an internal address is a legitimate target here.
                response = requests.get(discovery_url, timeout=DISCOVERY_TIMEOUT)
                response.raise_for_status()
                document = response.json()
            except (requests.RequestException, ValueError):
                raise AuthenticationException(
                    error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                    error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: could not read the discovery document",
                )
            if not isinstance(document, dict):
                raise AuthenticationException(
                    error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                    error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: malformed discovery document",
                )
            try:
                cache.set(cache_key, document, DISCOVERY_CACHE_TIMEOUT)
            except Exception:
                pass

        return (
            document.get("authorization_endpoint"),
            document.get("token_endpoint"),
            document.get("userinfo_endpoint"),
            document.get("jwks_uri"),
            document.get("issuer"),
        )

    @staticmethod
    def __code_challenge(code_verifier):
        """S256 challenge for the verifier: base64url(sha256(verifier)), unpadded."""
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def __init__(self, request, code=None, state=None, callback=None):
        (
            OIDC_FREE_CLIENT_ID,
            OIDC_FREE_CLIENT_SECRET,
            OIDC_FREE_DISCOVERY_URL,
            OIDC_FREE_AUTH_URL,
            OIDC_FREE_TOKEN_URL,
            OIDC_FREE_USERINFO_URL,
            OIDC_FREE_JWKS_URL,
            OIDC_FREE_SCOPE,
            OIDC_FREE_ALLOW_UNVERIFIED_EMAIL,
        ) = get_configuration_value(
            [
                self.__read_option_from_env("OIDC_FREE_CLIENT_ID"),
                self.__read_option_from_env("OIDC_FREE_CLIENT_SECRET"),
                self.__read_option_from_env("OIDC_FREE_DISCOVERY_URL"),
                self.__read_option_from_env("OIDC_FREE_AUTH_URL"),
                self.__read_option_from_env("OIDC_FREE_TOKEN_URL"),
                self.__read_option_from_env("OIDC_FREE_USERINFO_URL"),
                self.__read_option_from_env("OIDC_FREE_JWKS_URL"),
                self.__read_option_from_env("OIDC_FREE_SCOPE", "openid email profile"),
                self.__read_option_from_env("OIDC_FREE_ALLOW_UNVERIFIED_EMAIL", "0"),
            ]
        )

        if not OIDC_FREE_CLIENT_ID or not OIDC_FREE_CLIENT_SECRET:
            raise self.__not_configured()

        # Discovery fills in whatever the admin has not set explicitly, so a
        # provider that serves a discovery document needs only its URL, and one
        # that does not can still be wired up endpoint by endpoint.
        discovered_issuer = None
        if OIDC_FREE_DISCOVERY_URL:
            (
                discovered_auth_url,
                discovered_token_url,
                discovered_userinfo_url,
                discovered_jwks_url,
                discovered_issuer,
            ) = self.__discover_endpoints(OIDC_FREE_DISCOVERY_URL)
            OIDC_FREE_AUTH_URL = OIDC_FREE_AUTH_URL or discovered_auth_url
            OIDC_FREE_TOKEN_URL = OIDC_FREE_TOKEN_URL or discovered_token_url
            OIDC_FREE_USERINFO_URL = OIDC_FREE_USERINFO_URL or discovered_userinfo_url
            OIDC_FREE_JWKS_URL = OIDC_FREE_JWKS_URL or discovered_jwks_url

        auth_endpoint = self.__validate_endpoint(OIDC_FREE_AUTH_URL)
        self.token_url = self.__validate_endpoint(OIDC_FREE_TOKEN_URL)
        self.userinfo_url = self.__validate_endpoint(OIDC_FREE_USERINFO_URL)
        self.allow_unverified_email = str(OIDC_FREE_ALLOW_UNVERIFIED_EMAIL) == "1"

        # The id_token is only worth checking if we know the keys it should be
        # signed with. Discovery supplies them, so the signed-in path validates
        # by default; configuring the endpoints by hand and leaving the JWKS URL
        # blank is the way to run against a provider that cannot support it.
        self.jwks_url = self.__validate_endpoint(OIDC_FREE_JWKS_URL) if OIDC_FREE_JWKS_URL else None
        self.expected_issuer = discovered_issuer or None

        scope = OIDC_FREE_SCOPE
        client_id = OIDC_FREE_CLIENT_ID
        client_secret = OIDC_FREE_CLIENT_SECRET

        # get_host() already carries the non-default port, and honours the
        # X-Forwarded-Host header when USE_X_FORWARDED_HOST is enabled.
        redirect_uri = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}/{CALLBACK_PATH}"
        url_params = {
            "client_id": client_id,
            "scope": scope,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }

        # Only the authorization leg mints these; the callback reads them back
        # out of the session.
        if state:
            code_verifier = secrets.token_urlsafe(64)
            nonce = secrets.token_urlsafe(32)
            request.session[SESSION_CODE_VERIFIER] = code_verifier
            request.session[SESSION_NONCE] = nonce
            url_params["code_challenge"] = self.__code_challenge(code_verifier)
            url_params["code_challenge_method"] = "S256"
            url_params["nonce"] = nonce

        # The authorization endpoint may already carry query parameters of its
        # own, which a plain "?" would discard.
        separator = "&" if urlparse(auth_endpoint).query else "?"
        auth_url = f"{auth_endpoint}{separator}{urlencode(url_params)}"

        super().__init__(
            request,
            self.provider,
            client_id,
            scope,
            redirect_uri,
            auth_url,
            self.token_url,
            self.userinfo_url,
            client_secret,
            code,
            callback=callback,
        )

    def __validate_id_token(self, id_token, expected_nonce):
        """Verify the id_token's signature and claims against the provider's keys.

        Skipped when no JWKS URL is known, since there is then nothing to verify
        the signature against.
        """
        if not self.jwks_url:
            return

        if not id_token:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: token response carried no id_token",
            )

        # A templated issuer cannot be compared literally, so the decoder is told to skip
        # it and __validate_issuer_template checks it against the template below.
        templated_issuer = bool(self.expected_issuer and ISSUER_PLACEHOLDER.search(self.expected_issuer))
        verify_issuer = bool(self.expected_issuer) and not templated_issuer
        required_claims = ["exp", "iat", "aud"] + (["iss"] if self.expected_issuer else [])

        try:
            signing_key = _jwks_client(self.jwks_url).get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=ID_TOKEN_ALGORITHMS,
                audience=self.client_id,
                # The issuer is only checked when discovery told us what it is.
                issuer=self.expected_issuer if verify_issuer else None,
                leeway=ID_TOKEN_LEEWAY,
                options={"require": required_claims, "verify_iss": verify_issuer},
            )
        except jwt.PyJWTError:
            self.logger.warning("Rejected an oidc-free id_token", exc_info=True)
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: the id_token did not validate",
            )

        if templated_issuer:
            self.__validate_issuer_template(claims)

        # Binds this token to the authorization request we started, so one
        # obtained for another session cannot be replayed into this one.
        if expected_nonce and claims.get("nonce") != expected_nonce:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: the id_token nonce did not match",
            )

    def __validate_issuer_template(self, claims):
        """Check the token's issuer against the templated one discovery declared.

        The template becomes a pattern: its literal parts have to match exactly, and each
        placeholder matches a single path segment — pinned to the token's own claim where
        the placeholder names one, which is how Microsoft documents validating a
        multi-tenant issuer against the tenant the token was signed for.
        """
        issuer = claims.get("iss")
        # split() on one capturing group alternates literal, placeholder, literal, ...
        pieces = ISSUER_PLACEHOLDER.split(self.expected_issuer)
        pattern = ""
        for index, piece in enumerate(pieces):
            if index % 2 == 0:
                pattern += re.escape(piece)
                continue
            claim = claims.get(ISSUER_PLACEHOLDER_CLAIMS.get(piece.lower(), ""))
            pattern += re.escape(str(claim)) if claim else "[^/]+"

        if not issuer or not re.fullmatch(pattern, issuer):
            self.logger.warning(
                "Rejected an oidc-free id_token: issuer %s does not match %s",
                issuer,
                self.expected_issuer,
            )
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: the id_token issuer did not match the provider's",
            )

    def set_token_data(self):
        data = {
            "code": self.code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        # Both are single use: taken out of the session as the code is redeemed.
        code_verifier = self.request.session.pop(SESSION_CODE_VERIFIER, None)
        expected_nonce = self.request.session.pop(SESSION_NONCE, None)
        if code_verifier:
            data["code_verifier"] = code_verifier

        headers = {"Accept": "application/json"}
        token_response = self.get_user_token(data=data, headers=headers)
        self.__validate_id_token(token_response.get("id_token"), expected_nonce)

        # Both are seconds, and neither is necessarily a number: a provider that sends
        # them as strings must not take a sign-in down with a TypeError after the code
        # has already been redeemed.
        expires_in = _seconds_or_none(token_response.get("expires_in"))
        refresh_expires_at = _seconds_or_none(token_response.get("refresh_token_expired_at"))
        super().set_token_data(
            {
                "access_token": token_response.get("access_token"),
                "refresh_token": token_response.get("refresh_token", None),
                "access_token_expired_at": (
                    datetime.now(tz=pytz.utc) + timedelta(seconds=expires_in) if expires_in else None
                ),
                "refresh_token_expired_at": _epoch_or_none(refresh_expires_at),
                "id_token": token_response.get("id_token", ""),
            }
        )

    def set_user_data(self):
        user_info_response = self.get_user_response()

        # Reject unverified emails — an attacker-controlled provider could otherwise assert
        # any email to match an existing account (GHSA-7j95-vh8g-f365). Fail closed: treat
        # an absent email_verified claim the same as email_verified=false.
        #
        # Some providers never emit the claim (Microsoft Entra ID among them), which would
        # leave them unusable, so an admin can accept their word for it. That is only sound
        # when the provider vouches for the addresses it asserts — a directory whose users
        # the admin controls. Pointed at a provider anyone can register with, it hands over
        # every account whose email an attacker can name.
        if user_info_response.get("email_verified") is not True and not self.allow_unverified_email:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OAUTH_PROVIDER_UNVERIFIED_EMAIL"],
                error_message="OAUTH_PROVIDER_UNVERIFIED_EMAIL",
            )

        email = user_info_response.get("email")
        if not email:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: userinfo response carried no email claim",
            )

        # "sub" is the only claim OIDC guarantees to be stable and unique per issuer.
        subject = user_info_response.get("sub")
        if not subject:
            raise AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["OIDC_FREE_OAUTH_PROVIDER_ERROR"],
                error_message="OIDC_FREE_OAUTH_PROVIDER_ERROR: userinfo response carried no sub claim",
            )

        super().set_user_data(
            {
                "email": email,
                "user": {
                    "provider_id": str(subject),
                    "email": email,
                    "avatar": user_info_response.get("picture") or "",
                    "first_name": user_info_response.get("given_name") or user_info_response.get("name"),
                    "last_name": user_info_response.get("family_name"),
                    "is_password_autoset": True,
                },
            }
        )
