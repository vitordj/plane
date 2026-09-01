# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Microsoft Entra ID (Azure AD) OAuth provider.

The tests that matter here are the ones that decide *who* gets signed in:
the tenant pin, which is what stops an account from an unrelated Entra
directory completing a sign-in, and the email resolution, which decides which
Plane account the sign-in lands on.
"""

from datetime import datetime, timedelta, timezone as dt_timezone

import jwt
import pytest

from plane.authentication.adapter.error import (
    AUTHENTICATION_ERROR_CODES,
    AuthenticationException,
)
from plane.authentication.provider.oauth.azuread import AzureADOAuthProvider

TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
OTHER_TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "5a1c2f30-9d4b-4e7a-8f26-1b3c9de0a4f7"
CLIENT_SECRET = "a-secret"


class FakeRequest:
    """Just enough request for the provider's redirect-URI construction."""

    def __init__(self, secure=True, host="plane.example.com"):
        self._secure = secure
        self._host = host
        self.session = {}
        self.META = {}

    def is_secure(self):
        return self._secure

    def get_host(self):
        return self._host


def id_token(tenant=TENANT, audience=CLIENT_ID, expired=False, **claims):
    now = datetime.now(tz=dt_timezone.utc)
    payload = {
        "aud": audience,
        "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "tid": tenant,
        "oid": "00000000-1111-2222-3333-444444444444",
        "exp": int((now - timedelta(hours=1) if expired else now + timedelta(hours=1)).timestamp()),
        "iat": int(now.timestamp()),
        **claims,
    }
    return jwt.encode(payload, "signing-key-the-client-never-checks", algorithm="HS256")


@pytest.fixture
def configured(monkeypatch):
    """Point the provider's configuration lookup at fixed values."""

    def configure(tenant=TENANT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET):
        values = {
            "AZUREAD_CLIENT_ID": client_id,
            "AZUREAD_CLIENT_SECRET": client_secret,
            "AZUREAD_TENANT_ID": tenant,
        }
        monkeypatch.setattr(
            "plane.authentication.provider.oauth.azuread.get_configuration_value",
            lambda keys: [values.get(item["key"]) for item in keys],
        )

    return configure


def build_provider(configured, tenant=TENANT, **kwargs):
    configured(tenant=tenant, **kwargs)
    return AzureADOAuthProvider(request=FakeRequest(), code="an-auth-code")


@pytest.mark.unit
class TestAzureADConfiguration:
    def test_missing_configuration_is_reported_as_not_configured(self, configured):
        configured(client_secret=None)
        with pytest.raises(AuthenticationException) as exc:
            AzureADOAuthProvider(request=FakeRequest(), state="state")
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_NOT_CONFIGURED"]

    @pytest.mark.parametrize(
        "tenant",
        [
            "../../evil",
            "https://attacker.example.com",
            "tenant/oauth2/v2.0/authorize?x=y",
            "tenant with spaces",
        ],
    )
    def test_a_tenant_that_could_escape_the_authority_url_is_refused(self, configured, tenant):
        """The tenant is interpolated into the authority URL, so it stays a bare label."""
        configured(tenant=tenant)
        with pytest.raises(AuthenticationException) as exc:
            AzureADOAuthProvider(request=FakeRequest(), state="state")
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_NOT_CONFIGURED"]
        # The rejected value must not travel back to the browser in a query param.
        assert tenant not in exc.value.error_message

    def test_auth_url_targets_the_configured_tenant(self, configured):
        configured()
        provider = AzureADOAuthProvider(request=FakeRequest(), state="opaque-state")
        auth_url = provider.get_auth_url()
        assert auth_url.startswith(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?")
        assert "state=opaque-state" in auth_url
        assert f"client_id={CLIENT_ID}" in auth_url
        assert "redirect_uri=https%3A%2F%2Fplane.example.com%2Fauth%2Fazuread%2Fcallback%2F" in auth_url

    def test_redirect_uri_follows_the_request_scheme(self, configured):
        configured()
        provider = AzureADOAuthProvider(request=FakeRequest(secure=False, host="localhost:3000"), state="s")
        assert provider.redirect_uri == "http://localhost:3000/auth/azuread/callback/"


@pytest.mark.unit
class TestIdTokenValidation:
    def _validate(self, provider, token):
        # The check runs inside set_token_data; call it through the token response.
        provider.get_user_token = lambda data, headers=None: {
            "access_token": "an-access-token",
            "refresh_token": "a-refresh-token",
            "expires_in": 3600,
            "id_token": token,
        }
        provider.set_token_data()
        return provider

    def test_a_token_from_another_directory_is_refused(self, configured):
        provider = build_provider(configured)
        with pytest.raises(AuthenticationException) as exc:
            self._validate(provider, id_token(tenant=OTHER_TENANT))
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_TENANT_MISMATCH"]

    def test_a_token_minted_for_another_application_is_refused(self, configured):
        provider = build_provider(configured)
        with pytest.raises(AuthenticationException) as exc:
            self._validate(provider, id_token(audience="some-other-app"))
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_OAUTH_PROVIDER_ERROR"]

    def test_an_expired_token_is_refused(self, configured):
        provider = build_provider(configured)
        with pytest.raises(AuthenticationException) as exc:
            self._validate(provider, id_token(expired=True))
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_OAUTH_PROVIDER_ERROR"]

    def test_a_missing_token_is_refused(self, configured):
        provider = build_provider(configured)
        with pytest.raises(AuthenticationException) as exc:
            self._validate(provider, "")
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_OAUTH_PROVIDER_ERROR"]

    def test_a_token_from_the_configured_directory_is_accepted(self, configured):
        provider = build_provider(configured)
        self._validate(provider, id_token())
        assert provider.token_data["access_token"] == "an-access-token"
        assert provider.token_data["refresh_token"] == "a-refresh-token"
        assert provider.token_data["access_token_expired_at"] is not None
        # Entra publishes no refresh-token lifetime, so none is invented.
        assert provider.token_data["refresh_token_expired_at"] is None

    def test_a_domain_configured_tenant_accepts_the_directory_it_resolves_to(self, configured):
        """Configured by domain, `tid` carries the GUID the domain belongs to."""
        provider = build_provider(configured, tenant="contoso.com")
        self._validate(provider, id_token(tenant=OTHER_TENANT))
        assert provider.id_token_claims["tid"] == OTHER_TENANT

    def test_a_domain_configured_tenant_still_needs_a_directory_claim(self, configured):
        provider = build_provider(configured, tenant="contoso.com")
        token = jwt.encode(
            {
                "aud": CLIENT_ID,
                "iss": "https://login.microsoftonline.com/9188040d/v2.0",
                "exp": int((datetime.now(tz=dt_timezone.utc) + timedelta(hours=1)).timestamp()),
            },
            "signing-key-the-client-never-checks",
            algorithm="HS256",
        )
        with pytest.raises(AuthenticationException) as exc:
            self._validate(provider, token)
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_TENANT_MISMATCH"]

    @pytest.mark.parametrize("tenant", ["common", "organizations", "consumers"])
    def test_a_multi_tenant_configuration_accepts_any_directory(self, configured, tenant):
        """`common` is the admin explicitly opting out of the tenant pin."""
        provider = build_provider(configured, tenant=tenant)
        self._validate(provider, id_token(tenant=OTHER_TENANT))
        assert provider.id_token_claims["tid"] == OTHER_TENANT


@pytest.mark.unit
class TestUserData:
    def _sign_in(self, configured, graph_response, claims=None, tenant=TENANT):
        provider = build_provider(configured, tenant=tenant)
        provider.id_token_claims = claims if claims is not None else {"oid": "graph-less-oid"}
        provider.token_data = {"access_token": "an-access-token"}
        provider.get_user_response = lambda: graph_response
        provider.set_user_data()
        return provider.user_data

    def test_the_mailbox_address_is_preferred(self, configured):
        data = self._sign_in(
            configured,
            {
                "id": "00000000-1111-2222-3333-444444444444",
                "mail": "ana@contoso.com",
                "userPrincipalName": "ana.silva@contoso.onmicrosoft.com",
                "givenName": "Ana",
                "surname": "Silva",
            },
        )
        assert data["email"] == "ana@contoso.com"
        assert data["user"]["provider_id"] == "00000000-1111-2222-3333-444444444444"
        assert data["user"]["first_name"] == "Ana"
        assert data["user"]["last_name"] == "Silva"
        assert data["user"]["is_password_autoset"] is True

    def test_the_principal_name_covers_accounts_without_a_mailbox(self, configured):
        data = self._sign_in(
            configured,
            {"id": "an-oid", "mail": None, "userPrincipalName": "bruno@contoso.com", "displayName": "Bruno Costa"},
        )
        assert data["email"] == "bruno@contoso.com"
        # No given/surname: the display name stands in rather than leaving it blank.
        assert data["user"]["first_name"] == "Bruno Costa"

    def test_a_guest_principal_name_is_not_treated_as_an_address(self, configured):
        """A guest's UPN names a foreign home tenant and is not deliverable."""
        with pytest.raises(AuthenticationException) as exc:
            self._sign_in(
                configured,
                {
                    "id": "an-oid",
                    "mail": None,
                    "userPrincipalName": "carla_outside.com#EXT#@contoso.onmicrosoft.com",
                },
            )
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_NO_EMAIL"]

    def test_the_id_token_claims_are_the_last_resort_for_an_address(self, configured):
        data = self._sign_in(
            configured,
            {"id": "an-oid", "mail": None, "userPrincipalName": None},
            claims={"oid": "an-oid", "preferred_username": "diego@contoso.com"},
        )
        assert data["email"] == "diego@contoso.com"

    def test_an_account_with_no_address_anywhere_is_refused(self, configured):
        with pytest.raises(AuthenticationException) as exc:
            self._sign_in(configured, {"id": "an-oid"}, claims={})
        assert exc.value.error_code == AUTHENTICATION_ERROR_CODES["AZUREAD_NO_EMAIL"]

    def test_the_directory_object_id_keys_the_account(self, configured):
        """Graph's `id` survives renames and email changes; the address does not."""
        data = self._sign_in(
            configured,
            {"id": None, "mail": "erica@contoso.com"},
            claims={"oid": "the-object-id"},
        )
        assert data["user"]["provider_id"] == "the-object-id"

    def test_no_avatar_url_is_stored(self, configured):
        """Graph's photo endpoint needs the access token, which the shared avatar
        fetcher would replay across redirects — so no URL is handed to it."""
        data = self._sign_in(configured, {"id": "an-oid", "mail": "ana@contoso.com"})
        assert "avatar" not in data["user"]


@pytest.mark.unit
class TestDirectorySync:
    """With ENABLE_AZUREAD_SYNC on, every login re-syncs the account."""

    class FakeUser:
        def __init__(self):
            self.first_name = "Old"
            self.last_name = "Name"
            self.display_name = "chosen-handle"
            self.avatar = "https://cdn.example.com/uploaded.png"
            self.avatar_asset = object()
            self.saved = False

        def save(self):
            self.saved = True

    def test_the_directory_names_are_synced(self, configured):
        provider = build_provider(configured)
        provider.user_data = {"email": "ana@contoso.com", "user": {"first_name": "Ana", "last_name": "Silva"}}
        user = self.FakeUser()
        provider.sync_user_data(user)
        assert (user.first_name, user.last_name) == ("Ana", "Silva")
        assert user.saved is True

    def test_the_avatar_and_display_name_the_person_chose_survive_a_sync(self, configured):
        """The base sync would delete the avatar and rewrite the display name;
        Entra is authoritative about neither."""
        provider = build_provider(configured)
        provider.user_data = {"email": "ana@contoso.com", "user": {"first_name": "Ana", "last_name": "Silva"}}
        user = self.FakeUser()
        provider.sync_user_data(user)
        assert user.avatar == "https://cdn.example.com/uploaded.png"
        assert user.avatar_asset is not None
        assert user.display_name == "chosen-handle"


@pytest.mark.unit
class TestProviderWiring:
    def test_the_provider_maps_to_its_own_error_code(self, configured):
        provider = build_provider(configured)
        assert provider.authentication_error_code() == "AZUREAD_OAUTH_PROVIDER_ERROR"

    def test_the_scope_requests_a_refresh_token_and_graph_access(self, configured):
        provider = build_provider(configured)
        assert "offline_access" in provider.scope
        assert "User.Read" in provider.scope
        assert "openid" in provider.scope
