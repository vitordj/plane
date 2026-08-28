# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

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
    "OIDC_FREE_SCOPE": "openid email profile",
    "OIDC_FREE_ALLOW_UNVERIFIED_EMAIL": "0",
}

# Entra ID serves userinfo from a different host than authorize and token, which
# is the case a single-host configuration cannot express.
ENTRA_DISCOVERY_DOCUMENT = {
    "authorization_endpoint": "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/authorize",
    "token_endpoint": "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token",
    "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
}


def _fake_request(host="plane.example.com", secure=True):
    request = MagicMock()
    request.get_host.return_value = host
    request.is_secure.return_value = secure
    request.session = {}
    return request


def _build(config=None, discovery_document=None, discovery_raises=None):
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
        provider = OidcFreeOAuthProvider(request=_fake_request(), state="state-token")
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
