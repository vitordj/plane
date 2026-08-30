# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import requests

from plane.authentication.adapter.error import AuthenticationException
from plane.authentication.provider.oauth import oidc_free
from plane.authentication.provider.oauth.oidc_free import OidcFreeOAuthProvider

# The order get_configuration_value is called with in the provider.
CONFIG_ORDER = [
    "OIDC_FREE_CLIENT_ID",
    "OIDC_FREE_CLIENT_SECRET",
    "OIDC_FREE_DISCOVERY_URL",
    "OIDC_FREE_AUTH_URL",
    "OIDC_FREE_TOKEN_URL",
    "OIDC_FREE_USERINFO_URL",
    "OIDC_FREE_JWKS_URL",
    "OIDC_FREE_SCOPE",
    "OIDC_FREE_ALLOW_UNVERIFIED_EMAIL",
]

DEFAULTS = {
    "OIDC_FREE_CLIENT_ID": "plane",
    "OIDC_FREE_CLIENT_SECRET": "secret",
    "OIDC_FREE_DISCOVERY_URL": "",
    "OIDC_FREE_AUTH_URL": "https://sso.example.com/authorize",
    "OIDC_FREE_TOKEN_URL": "https://sso.example.com/token",
    "OIDC_FREE_USERINFO_URL": "https://sso.example.com/userinfo",
    "OIDC_FREE_JWKS_URL": "",
    "OIDC_FREE_SCOPE": "openid email profile",
    "OIDC_FREE_ALLOW_UNVERIFIED_EMAIL": "0",
}

# Entra ID serves userinfo from a different host than authorize and token, which
# is the case a single-host configuration cannot express.
ENTRA_DISCOVERY_DOCUMENT = {
    "authorization_endpoint": "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/authorize",
    "token_endpoint": "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token",
    "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
    "jwks_uri": "https://login.microsoftonline.com/tenant-id/discovery/v2.0/keys",
    "issuer": "https://login.microsoftonline.com/tenant-id/v2.0",
}


def _fake_request(host="plane.example.com", secure=True, session=None):
    request = MagicMock()
    request.get_host.return_value = host
    request.is_secure.return_value = secure
    request.session = {} if session is None else session
    return request


def _build(config=None, discovery_document=None, discovery_raises=None, state="state-token", code=None):
    """Construct the provider with its configuration and network calls stubbed."""
    values = {**DEFAULTS, **(config or {})}

    response = MagicMock()
    response.json.return_value = discovery_document
    response.raise_for_status.return_value = None

    with (
        patch.object(oidc_free, "get_configuration_value", return_value=[values[key] for key in CONFIG_ORDER]),
        patch.object(oidc_free, "cache", MagicMock(get=MagicMock(return_value=None))),
        patch.object(
            oidc_free.requests,
            "get",
            side_effect=discovery_raises or None,
            return_value=response,
        ) as fetch,
    ):
        request = _fake_request()
        provider = OidcFreeOAuthProvider(request=request, code=code, state=state)
        return provider, fetch


@pytest.mark.unit
class TestOidcFreeEndpointResolution:
    """Endpoints are absolute per endpoint, so providers may spread them across hosts."""

    def test_explicit_urls_are_used_as_given(self):
        provider, fetch = _build()
        assert provider.token_url == DEFAULTS["OIDC_FREE_TOKEN_URL"]
        assert provider.userinfo_url == DEFAULTS["OIDC_FREE_USERINFO_URL"]
        assert provider.get_auth_url().startswith(DEFAULTS["OIDC_FREE_AUTH_URL"])
        # No discovery URL is set, so nothing should be fetched.
        fetch.assert_not_called()

    def test_discovery_populates_endpoints_across_hosts(self):
        provider, _ = _build(
            config={
                "OIDC_FREE_DISCOVERY_URL": "https://login.microsoftonline.com/tenant-id/v2.0",
                "OIDC_FREE_AUTH_URL": "",
                "OIDC_FREE_TOKEN_URL": "",
                "OIDC_FREE_USERINFO_URL": "",
            },
            discovery_document=ENTRA_DISCOVERY_DOCUMENT,
        )
        assert provider.token_url == ENTRA_DISCOVERY_DOCUMENT["token_endpoint"]
        # The endpoint that lives on another host is kept, not rebuilt from the issuer.
        assert provider.userinfo_url == ENTRA_DISCOVERY_DOCUMENT["userinfo_endpoint"]
        assert provider.get_auth_url().startswith(ENTRA_DISCOVERY_DOCUMENT["authorization_endpoint"])

    def test_discovery_url_gets_the_well_known_suffix(self):
        _, fetch = _build(
            config={
                "OIDC_FREE_DISCOVERY_URL": "https://sso.example.com/realms/plane",
                "OIDC_FREE_AUTH_URL": "",
                "OIDC_FREE_TOKEN_URL": "",
                "OIDC_FREE_USERINFO_URL": "",
            },
            discovery_document=ENTRA_DISCOVERY_DOCUMENT,
        )
        assert fetch.call_args.args[0] == "https://sso.example.com/realms/plane/.well-known/openid-configuration"

    @pytest.mark.parametrize(
        "configured",
        [
            "https://sso.example.com/realms/plane/",
            "https://sso.example.com/realms/plane/.well-known/openid-configuration",
            "https://sso.example.com/realms/plane/.well-known/openid-configuration/",
        ],
    )
    def test_the_suffix_is_added_once_however_the_url_is_pasted(self, configured):
        """A trailing slash used to hide the suffix and earn the URL a second copy."""
        _, fetch = _build(
            config={
                "OIDC_FREE_DISCOVERY_URL": configured,
                "OIDC_FREE_AUTH_URL": "",
                "OIDC_FREE_TOKEN_URL": "",
                "OIDC_FREE_USERINFO_URL": "",
            },
            discovery_document=ENTRA_DISCOVERY_DOCUMENT,
        )
        assert fetch.call_args.args[0] == "https://sso.example.com/realms/plane/.well-known/openid-configuration"

    def test_explicit_url_overrides_the_discovered_one(self):
        provider, _ = _build(
            config={
                "OIDC_FREE_DISCOVERY_URL": "https://sso.example.com",
                "OIDC_FREE_USERINFO_URL": "https://override.example.com/userinfo",
                "OIDC_FREE_AUTH_URL": "",
                "OIDC_FREE_TOKEN_URL": "",
            },
            discovery_document=ENTRA_DISCOVERY_DOCUMENT,
        )
        assert provider.userinfo_url == "https://override.example.com/userinfo"
        assert provider.token_url == ENTRA_DISCOVERY_DOCUMENT["token_endpoint"]

    @pytest.mark.parametrize("missing", ["OIDC_FREE_AUTH_URL", "OIDC_FREE_TOKEN_URL", "OIDC_FREE_USERINFO_URL"])
    def test_missing_endpoint_without_discovery_is_not_configured(self, missing):
        with pytest.raises(AuthenticationException) as exc:
            _build(config={missing: ""})
        assert exc.value.error_code == 5113

    @pytest.mark.parametrize("credential", ["OIDC_FREE_CLIENT_ID", "OIDC_FREE_CLIENT_SECRET"])
    def test_missing_credential_is_not_configured(self, credential):
        with pytest.raises(AuthenticationException) as exc:
            _build(config={credential: ""})
        assert exc.value.error_code == 5113

    @pytest.mark.parametrize("url", ["sso.example.com/authorize", "ftp://sso.example.com/authorize", "/authorize"])
    def test_endpoint_must_be_an_absolute_http_url(self, url):
        with pytest.raises(AuthenticationException) as exc:
            _build(config={"OIDC_FREE_AUTH_URL": url})
        assert exc.value.error_code == 5113

    def test_unreachable_discovery_document_reports_a_provider_error(self):
        with pytest.raises(AuthenticationException) as exc:
            _build(
                config={
                    "OIDC_FREE_DISCOVERY_URL": "https://sso.example.com",
                    "OIDC_FREE_AUTH_URL": "",
                    "OIDC_FREE_TOKEN_URL": "",
                    "OIDC_FREE_USERINFO_URL": "",
                },
                discovery_raises=requests.RequestException("boom"),
            )
        assert exc.value.error_code == 5114


@pytest.mark.unit
class TestOidcFreeAuthorizationUrl:
    def test_redirect_uri_is_derived_from_the_request_host(self):
        provider, _ = _build()
        params = parse_qs(urlparse(provider.get_auth_url()).query)
        assert params["redirect_uri"] == ["https://plane.example.com/auth/oidc-free/callback/"]
        assert params["response_type"] == ["code"]
        assert params["state"] == ["state-token"]

    def test_query_on_the_authorization_endpoint_is_preserved(self):
        provider, _ = _build(config={"OIDC_FREE_AUTH_URL": "https://sso.example.com/authorize?audience=plane"})
        parsed = urlparse(provider.get_auth_url())
        params = parse_qs(parsed.query)
        assert params["audience"] == ["plane"]
        assert params["client_id"] == ["plane"]


@pytest.mark.unit
class TestOidcFreeUserData:
    """Email verification fails closed unless an admin opts out."""

    CLAIMS = {
        "sub": "subject-id",
        "email": "member@example.com",
        "given_name": "Ada",
        "family_name": "Lovelace",
    }

    def _set_user_data(self, claims, allow_unverified="0"):
        provider, _ = _build(config={"OIDC_FREE_ALLOW_UNVERIFIED_EMAIL": allow_unverified})
        with patch.object(OidcFreeOAuthProvider, "get_user_response", return_value=claims):
            provider.set_user_data()
        return provider.user_data

    def test_verified_email_is_accepted(self):
        user_data = self._set_user_data({**self.CLAIMS, "email_verified": True})
        assert user_data["email"] == "member@example.com"
        assert user_data["user"]["provider_id"] == "subject-id"
        assert user_data["user"]["first_name"] == "Ada"

    @pytest.mark.parametrize("claims_extra", [{}, {"email_verified": False}, {"email_verified": "true"}])
    def test_unverified_email_is_rejected_by_default(self, claims_extra):
        with pytest.raises(AuthenticationException) as exc:
            self._set_user_data({**self.CLAIMS, **claims_extra})
        assert exc.value.error_code == 5124

    def test_unverified_email_is_accepted_when_opted_in(self):
        user_data = self._set_user_data(self.CLAIMS, allow_unverified="1")
        assert user_data["email"] == "member@example.com"

    def test_name_is_the_fallback_when_given_name_is_absent(self):
        claims = {"sub": "s", "email": "m@example.com", "email_verified": True, "name": "Ada Lovelace"}
        user_data = self._set_user_data(claims)
        assert user_data["user"]["first_name"] == "Ada Lovelace"

    @pytest.mark.parametrize("missing", ["sub", "email"])
    def test_missing_required_claim_reports_a_provider_error(self, missing):
        claims = {**self.CLAIMS, "email_verified": True}
        del claims[missing]
        with pytest.raises(AuthenticationException) as exc:
            self._set_user_data(claims)
        assert exc.value.error_code == 5114


def _rsa_keypair():
    """A throwaway RSA key plus the JWK the provider would fetch for it."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)


def _sign(private_key, **claims):
    """An id_token carrying the standard claims, with the given ones layered on top."""
    payload = {
        "aud": "plane",
        "sub": "subject",
        "nonce": "the-nonce",
        "iat": datetime.now(tz=timezone.utc),
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


@pytest.mark.unit
class TestOidcFreePkce:
    """The code is bound to a verifier only this session holds."""

    def test_authorization_request_carries_an_s256_challenge(self):
        session = {}
        with (
            patch.object(oidc_free, "get_configuration_value", return_value=[DEFAULTS[key] for key in CONFIG_ORDER]),
            patch.object(oidc_free, "cache", MagicMock(get=MagicMock(return_value=None))),
        ):
            provider = OidcFreeOAuthProvider(request=_fake_request(session=session), state="state-token")

        params = parse_qs(urlparse(provider.get_auth_url()).query)
        verifier = session[oidc_free.SESSION_CODE_VERIFIER]
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        )
        assert params["code_challenge"] == [expected]
        assert params["code_challenge_method"] == ["S256"]
        # The challenge is a hash, never the verifier itself.
        assert verifier not in provider.get_auth_url()
        assert params["nonce"] == [session[oidc_free.SESSION_NONCE]]

    def test_callback_leg_does_not_mint_new_secrets(self):
        session = {"oidc_free_code_verifier": "kept", "oidc_free_nonce": "kept-nonce"}
        with (
            patch.object(oidc_free, "get_configuration_value", return_value=[DEFAULTS[key] for key in CONFIG_ORDER]),
            patch.object(oidc_free, "cache", MagicMock(get=MagicMock(return_value=None))),
        ):
            OidcFreeOAuthProvider(request=_fake_request(session=session), code="auth-code")
        assert session["oidc_free_code_verifier"] == "kept"

    def test_verifier_is_sent_once_and_then_dropped(self):
        session = {oidc_free.SESSION_CODE_VERIFIER: "the-verifier", oidc_free.SESSION_NONCE: "the-nonce"}
        with (
            patch.object(oidc_free, "get_configuration_value", return_value=[DEFAULTS[key] for key in CONFIG_ORDER]),
            patch.object(oidc_free, "cache", MagicMock(get=MagicMock(return_value=None))),
        ):
            provider = OidcFreeOAuthProvider(request=_fake_request(session=session), code="auth-code")

        with patch.object(OidcFreeOAuthProvider, "get_user_token", return_value={"access_token": "at"}) as exchange:
            provider.set_token_data()

        assert exchange.call_args.kwargs["data"]["code_verifier"] == "the-verifier"
        # Single use: a replay of the same code finds nothing to send.
        assert oidc_free.SESSION_CODE_VERIFIER not in session
        assert oidc_free.SESSION_NONCE not in session


@pytest.mark.unit
class TestOidcFreeIdTokenValidation:
    """With a JWKS URL known, the id_token has to hold up."""

    ISSUER = "https://sso.example.com"
    CONFIG = {"OIDC_FREE_JWKS_URL": "https://sso.example.com/jwks.json"}

    def _provider(self, session, expected_issuer=None):
        with (
            patch.object(
                oidc_free,
                "get_configuration_value",
                return_value=[{**DEFAULTS, **self.CONFIG}[key] for key in CONFIG_ORDER],
            ),
            patch.object(oidc_free, "cache", MagicMock(get=MagicMock(return_value=None))),
        ):
            provider = OidcFreeOAuthProvider(request=_fake_request(session=session), code="auth-code")
        provider.expected_issuer = expected_issuer
        return provider

    def _exchange(self, provider, id_token, jwk, key=None):
        client = MagicMock()
        signing_key = key if key is not None else jwt.PyJWK(jwk).key
        client.get_signing_key_from_jwt.return_value = MagicMock(key=signing_key)
        with (
            patch.object(oidc_free, "_jwks_client", return_value=client),
            patch.object(
                OidcFreeOAuthProvider, "get_user_token", return_value={"access_token": "at", "id_token": id_token}
            ),
        ):
            provider.set_token_data()

    def test_a_well_formed_id_token_is_accepted(self):
        private_key, jwk = _rsa_keypair()
        session = {oidc_free.SESSION_CODE_VERIFIER: "v", oidc_free.SESSION_NONCE: "the-nonce"}
        provider = self._provider(session, expected_issuer=self.ISSUER)
        id_token = jwt.encode(
            {
                "iss": self.ISSUER,
                "aud": "plane",
                "sub": "subject",
                "nonce": "the-nonce",
                "iat": datetime.now(tz=timezone.utc),
                "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )
        self._exchange(provider, id_token, jwk)
        assert provider.token_data["id_token"] == id_token

    def test_a_token_signed_by_another_key_is_rejected(self):
        private_key, _ = _rsa_keypair()
        _, other_jwk = _rsa_keypair()
        session = {oidc_free.SESSION_NONCE: "the-nonce"}
        provider = self._provider(session)
        id_token = jwt.encode(
            {
                "aud": "plane",
                "nonce": "the-nonce",
                "iat": datetime.now(tz=timezone.utc),
                "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )
        with pytest.raises(AuthenticationException) as exc:
            self._exchange(provider, id_token, other_jwk)
        assert exc.value.error_code == 5114

    def test_a_token_for_another_client_is_rejected(self):
        private_key, jwk = _rsa_keypair()
        provider = self._provider({oidc_free.SESSION_NONCE: "the-nonce"})
        id_token = jwt.encode(
            {
                "aud": "someone-else",
                "nonce": "the-nonce",
                "iat": datetime.now(tz=timezone.utc),
                "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )
        with pytest.raises(AuthenticationException) as exc:
            self._exchange(provider, id_token, jwk)
        assert exc.value.error_code == 5114

    def test_an_expired_token_is_rejected(self):
        private_key, jwk = _rsa_keypair()
        provider = self._provider({oidc_free.SESSION_NONCE: "the-nonce"})
        id_token = jwt.encode(
            {
                "aud": "plane",
                "nonce": "the-nonce",
                "iat": datetime.now(tz=timezone.utc) - timedelta(hours=2),
                "exp": datetime.now(tz=timezone.utc) - timedelta(hours=1),
            },
            private_key,
            algorithm="RS256",
        )
        with pytest.raises(AuthenticationException) as exc:
            self._exchange(provider, id_token, jwk)
        assert exc.value.error_code == 5114

    def test_a_token_from_another_session_is_rejected(self):
        """A valid token whose nonce belongs to a different authorization request."""
        private_key, jwk = _rsa_keypair()
        provider = self._provider({oidc_free.SESSION_NONCE: "our-nonce"})
        id_token = jwt.encode(
            {
                "aud": "plane",
                "nonce": "someone-elses-nonce",
                "iat": datetime.now(tz=timezone.utc),
                "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )
        with pytest.raises(AuthenticationException) as exc:
            self._exchange(provider, id_token, jwk)
        assert exc.value.error_code == 5114

    def test_an_unsigned_token_is_rejected(self):
        """alg=none must never pass, however well-formed the claims are.

        PyJWT refuses this algorithm on its own, so this guards the behaviour
        rather than our allowlist: it is what fails if signature verification is
        ever turned off or the library is swapped. The allowlist itself is
        covered by the test below.
        """
        _, jwk = _rsa_keypair()
        provider = self._provider({oidc_free.SESSION_NONCE: "the-nonce"})
        id_token = jwt.encode(
            {
                "aud": "plane",
                "nonce": "the-nonce",
                "iat": datetime.now(tz=timezone.utc),
                "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(AuthenticationException) as exc:
            self._exchange(provider, id_token, jwk, key="")
        assert exc.value.error_code == 5114

    def test_symmetric_algorithms_are_not_offered_to_the_decoder(self):
        """An HMAC alg would let a token be signed with a key we also hold."""
        private_key, jwk = _rsa_keypair()
        provider = self._provider({oidc_free.SESSION_NONCE: "the-nonce"})
        id_token = jwt.encode(
            {
                "aud": "plane",
                "nonce": "the-nonce",
                "iat": datetime.now(tz=timezone.utc),
                "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )
        with patch.object(oidc_free.jwt, "decode", wraps=jwt.decode) as decode:
            self._exchange(provider, id_token, jwk)
        offered = decode.call_args.kwargs["algorithms"]
        assert offered, "no algorithm allowlist was passed"
        assert not [alg for alg in offered if alg.lower() == "none" or alg.startswith("HS")]

    def test_a_missing_id_token_is_rejected(self):
        provider = self._provider({oidc_free.SESSION_NONCE: "the-nonce"})
        with patch.object(OidcFreeOAuthProvider, "get_user_token", return_value={"access_token": "at"}):
            with pytest.raises(AuthenticationException) as exc:
                provider.set_token_data()
        assert exc.value.error_code == 5114

    def test_without_a_jwks_url_the_token_is_not_inspected(self):
        """Manual configuration with no JWKS URL is the documented way out."""
        provider = self._provider({}, expected_issuer=None)
        provider.jwks_url = None
        with patch.object(
            OidcFreeOAuthProvider, "get_user_token", return_value={"access_token": "at", "id_token": "not-a-jwt"}
        ):
            provider.set_token_data()
        assert provider.token_data["access_token"] == "at"

    def test_discovery_supplies_the_jwks_url_and_issuer(self):
        provider, _ = _build(
            config={
                "OIDC_FREE_DISCOVERY_URL": "https://login.microsoftonline.com/tenant-id/v2.0",
                "OIDC_FREE_AUTH_URL": "",
                "OIDC_FREE_TOKEN_URL": "",
                "OIDC_FREE_USERINFO_URL": "",
            },
            discovery_document=ENTRA_DISCOVERY_DOCUMENT,
        )
        assert provider.jwks_url == ENTRA_DISCOVERY_DOCUMENT["jwks_uri"]
        assert provider.expected_issuer == ENTRA_DISCOVERY_DOCUMENT["issuer"]

    def test_a_templated_issuer_is_matched_against_the_tokens_own_tenant(self):
        """Entra's multi-tenant endpoints declare "{tenantid}", filled in per token."""
        private_key, jwk = _rsa_keypair()
        tenant = "9188040d-6c67-4c5b-b112-36a304b66dad"
        provider = self._provider(
            {oidc_free.SESSION_NONCE: "the-nonce"},
            expected_issuer="https://login.microsoftonline.com/{tenantid}/v2.0",
        )
        id_token = _sign(
            private_key,
            iss=f"https://login.microsoftonline.com/{tenant}/v2.0",
            tid=tenant,
        )
        self._exchange(provider, id_token, jwk)
        assert provider.token_data["id_token"] == id_token

    def test_a_templated_issuer_rejects_a_tenant_the_token_does_not_claim(self):
        private_key, jwk = _rsa_keypair()
        provider = self._provider(
            {oidc_free.SESSION_NONCE: "the-nonce"},
            expected_issuer="https://login.microsoftonline.com/{tenantid}/v2.0",
        )
        id_token = _sign(
            private_key,
            iss="https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0",
            tid="a-different-tenant",
        )
        with pytest.raises(AuthenticationException) as exc:
            self._exchange(provider, id_token, jwk)
        assert exc.value.error_code == 5114

    def test_a_templated_issuer_still_pins_everything_around_the_placeholder(self):
        """The placeholder stands in for one path segment, not for the whole issuer."""
        private_key, jwk = _rsa_keypair()
        tenant = "9188040d-6c67-4c5b-b112-36a304b66dad"
        provider = self._provider(
            {oidc_free.SESSION_NONCE: "the-nonce"},
            expected_issuer="https://login.microsoftonline.com/{tenantid}/v2.0",
        )
        id_token = _sign(private_key, iss=f"https://login.microsoftonline.evil.com/{tenant}/v2.0", tid=tenant)
        with pytest.raises(AuthenticationException) as exc:
            self._exchange(provider, id_token, jwk)
        assert exc.value.error_code == 5114


@pytest.mark.unit
class TestOidcFreeTokenLifetimes:
    """The lifetimes a token response carries are seconds, but not always numbers."""

    def _token_data(self, token_response):
        provider, _ = _build(code="auth-code")
        # No JWKS URL is configured, so the response is taken as it comes.
        assert provider.jwks_url is None
        with patch.object(OidcFreeOAuthProvider, "get_user_token", return_value=token_response):
            provider.set_token_data()
        return provider.token_data

    def test_expires_in_sent_as_a_string_is_honoured(self):
        """ADFS and older Azure endpoints send it as a string, not a number."""
        before = datetime.now(tz=timezone.utc)
        token_data = self._token_data({"access_token": "at", "expires_in": "3600"})
        expires_at = token_data["access_token_expired_at"]
        assert timedelta(minutes=59) <= expires_at - before <= timedelta(minutes=61)

    def test_expires_in_sent_as_a_number_is_unchanged(self):
        before = datetime.now(tz=timezone.utc)
        token_data = self._token_data({"access_token": "at", "expires_in": 3600})
        assert timedelta(minutes=59) <= token_data["access_token_expired_at"] - before <= timedelta(minutes=61)

    @pytest.mark.parametrize("expires_in", ["", "not-a-number", None, {}])
    def test_an_unusable_expires_in_leaves_the_lifetime_unset(self, expires_in):
        """A lifetime we cannot read is worth less than a 500 after redeeming the code."""
        token_data = self._token_data({"access_token": "at", "expires_in": expires_in})
        assert token_data["access_token_expired_at"] is None
        assert token_data["access_token"] == "at"

    @pytest.mark.parametrize("expired_at", ["1780000000", 1780000000])
    def test_refresh_token_expiry_is_read_as_an_epoch_either_way(self, expired_at):
        token_data = self._token_data({"access_token": "at", "refresh_token_expired_at": expired_at})
        assert token_data["refresh_token_expired_at"] == datetime.fromtimestamp(1780000000, tz=timezone.utc)

    @pytest.mark.parametrize("expired_at", ["not-a-number", 10**20])
    def test_a_refresh_token_expiry_out_of_range_leaves_the_lifetime_unset(self, expired_at):
        token_data = self._token_data({"access_token": "at", "refresh_token_expired_at": expired_at})
        assert token_data["refresh_token_expired_at"] is None
