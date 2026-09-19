# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the Microsoft Entra ID sign-in provider.

Everything here is an account-takeover path rather than a bug, which is why
each rule gets its own test: the tenant is pinned and checked against the
token's ``tid``, the id token is verified against the tenant's signing keys
before any claim of it is believed, the nonce ties the token to the browser
that started the sign-in, and the email is taken only from attributes the
tenant actually vouches for.

The provider is built through its real constructor with the instance
configuration mocked. An earlier version of these tests skipped ``__init__``
with a stub subclass, which left the tenant-shape checks in the constructor
untested — and those checks are part of the same trust argument.
"""

import json
import pathlib
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import RequestFactory
from jwt import PyJWKClientError

from plane.authentication.adapter.error import AUTHENTICATION_ERROR_CODES, AuthenticationException
from plane.authentication.provider.oauth import entra as entra_module
from plane.authentication.provider.oauth.entra import (
    MULTI_TENANT_AUTHORITIES,
    EntraOAuthProvider,
)

TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
OTHER_TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "99999999-8888-7777-6666-555555555555"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
NONCE = "3f1c9d2b4a5e6f708192a3b4c5d6e7f8"


@pytest.fixture(scope="module")
def tenant_key():
    """The tenant's signing key. Generated once — 2048-bit keygen is slow."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def rogue_key():
    """A key the tenant does not publish, for the forged-signature case."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_claims(**overrides):
    """
    @description A well-formed set of Entra id token claims.
    @param overrides: Claims to replace or, with a value of ``None``, remove.
    @returns The claim dict to sign.
    """
    now = datetime.now(tz=timezone.utc)
    claims = {
        "aud": CLIENT_ID,
        "iss": ISSUER,
        "iat": now - timedelta(minutes=1),
        "nbf": now - timedelta(minutes=1),
        "exp": now + timedelta(minutes=10),
        "tid": TENANT,
        "oid": "0a0a0a0a-1b1b-2c2c-3d3d-4e4e4e4e4e4e",
        "nonce": NONCE,
    }
    for key, value in overrides.items():
        if value is None:
            claims.pop(key, None)
        else:
            claims[key] = value
    return claims


def sign(claims, key):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def make_provider(monkeypatch, tenant=TENANT, session=None, code="auth-code", nonce=None):
    """
    @description Build the provider through its real constructor, with the
    instance configuration mocked and a request whose session is a plain dict.
    @returns The configured ``EntraOAuthProvider``.
    """
    monkeypatch.setattr(
        entra_module,
        "get_configuration_value",
        lambda keys: (CLIENT_ID, "client-secret", tenant),
    )
    request = RequestFactory().get("/auth/entra/callback/")
    request.session = {} if session is None else session
    return EntraOAuthProvider(request=request, code=code, nonce=nonce)


def use_key(monkeypatch, private_key):
    """Point the provider at a JWKS client that serves this key's public half."""
    public_key = private_key.public_key()
    monkeypatch.setattr(
        entra_module,
        "get_jwks_client",
        lambda tenant_id: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=public_key)),
    )


@pytest.mark.unit
class TestConstructor:
    def test_the_authorization_url_carries_the_state_and_the_nonce(self, monkeypatch):
        monkeypatch.setattr(
            entra_module,
            "get_configuration_value",
            lambda keys: (CLIENT_ID, "client-secret", TENANT),
        )
        request = RequestFactory().get("/auth/entra/")
        request.session = {}

        provider = EntraOAuthProvider(request=request, state="state-value", nonce=NONCE)

        auth_url = provider.get_auth_url()
        assert f"login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize" in auth_url
        assert "state=state-value" in auth_url
        assert f"nonce={NONCE}" in auth_url

    def test_the_callback_leg_sends_no_nonce(self, monkeypatch):
        """The callback rebuilds the provider only to exchange the code."""
        provider = make_provider(monkeypatch)

        assert "nonce=" not in provider.get_auth_url()

    @pytest.mark.parametrize("authority", sorted(MULTI_TENANT_AUTHORITIES))
    def test_a_multi_tenant_authority_is_refused(self, monkeypatch, authority):
        """
        The attack the pinning exists for: on `common`, anyone can create their
        own Azure tenant with a user whose email is somebody else's.
        """
        with pytest.raises(AuthenticationException):
            make_provider(monkeypatch, tenant=authority)

    def test_a_tenant_configured_by_domain_is_refused(self, monkeypatch):
        """
        `tid` and `iss` always carry the GUID, so a domain-configured tenant
        would fail every sign-in after the round trip. Refusing it here says
        why, instead of leaving an instance where nobody can log in.
        """
        with pytest.raises(AuthenticationException):
            make_provider(monkeypatch, tenant="contoso.onmicrosoft.com")

    def test_an_unconfigured_instance_is_refused(self, monkeypatch):
        monkeypatch.setattr(entra_module, "get_configuration_value", lambda keys: (None, None, None))
        request = RequestFactory().get("/auth/entra/")
        request.session = {}

        with pytest.raises(AuthenticationException):
            EntraOAuthProvider(request=request)

    def test_the_multi_tenant_authorities_are_known(self):
        """Guards the constructor's rejection list against a silent edit."""
        assert MULTI_TENANT_AUTHORITIES == {"common", "organizations", "consumers"}


@pytest.mark.unit
class TestIdTokenVerification:
    def test_a_well_formed_token_verifies(self, monkeypatch, tenant_key):
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        claims = provider.decode_id_token(sign(make_claims(), tenant_key))

        assert claims["tid"] == TENANT
        assert claims["nonce"] == NONCE

    def test_a_token_signed_by_another_key_is_refused(self, monkeypatch, tenant_key, rogue_key):
        """
        The case the old code could not see at all: it read the payload with
        base64 and never looked at the signature, so a token minted by anyone
        would have been believed.
        """
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        with pytest.raises(AuthenticationException):
            provider.decode_id_token(sign(make_claims(), rogue_key))

    def test_a_token_for_another_application_is_refused(self, monkeypatch, tenant_key):
        """Same tenant, different app registration — not ours to accept."""
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        with pytest.raises(AuthenticationException):
            provider.decode_id_token(sign(make_claims(aud="another-client-id"), tenant_key))

    def test_a_token_from_another_issuer_is_refused(self, monkeypatch, tenant_key):
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        other_issuer = f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"
        with pytest.raises(AuthenticationException):
            provider.decode_id_token(sign(make_claims(iss=other_issuer), tenant_key))

    def test_an_expired_token_is_refused(self, monkeypatch, tenant_key):
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        expired = make_claims(exp=datetime.now(tz=timezone.utc) - timedelta(minutes=1))
        with pytest.raises(AuthenticationException):
            provider.decode_id_token(sign(expired, tenant_key))

    def test_a_token_that_is_not_valid_yet_is_refused(self, monkeypatch, tenant_key):
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        future = make_claims(nbf=datetime.now(tz=timezone.utc) + timedelta(minutes=10))
        with pytest.raises(AuthenticationException):
            provider.decode_id_token(sign(future, tenant_key))

    @pytest.mark.parametrize("claim", ["exp", "iat", "nbf", "aud", "iss", "tid"])
    def test_every_required_claim_is_actually_required(self, monkeypatch, tenant_key, claim):
        """
        Without `require`, PyJWT only validates the claims that happen to be
        there: a token with no `exp` would never be considered expired, and one
        with no `aud` would never fail the audience check.
        """
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        with pytest.raises(AuthenticationException):
            provider.decode_id_token(sign(make_claims(**{claim: None}), tenant_key))

    @pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c"])
    def test_a_malformed_token_is_refused(self, monkeypatch, tenant_key, token):
        provider = make_provider(monkeypatch)
        use_key(monkeypatch, tenant_key)

        with pytest.raises(AuthenticationException):
            provider.decode_id_token(token)

    def test_an_unreachable_key_set_refuses_rather_than_trusts(self, monkeypatch, tenant_key):
        """
        A Microsoft outage must not degrade into accepting tokens nobody can
        check. Fail closed.
        """
        provider = make_provider(monkeypatch)

        def unreachable(tenant_id):
            def boom(token):
                raise PyJWKClientError("cannot fetch keys")

            return SimpleNamespace(get_signing_key_from_jwt=boom)

        monkeypatch.setattr(entra_module, "get_jwks_client", unreachable)

        with pytest.raises(AuthenticationException):
            provider.decode_id_token(sign(make_claims(), tenant_key))


@pytest.mark.unit
class TestTenantVerification:
    def test_a_token_from_the_configured_tenant_is_accepted(self, monkeypatch):
        provider = make_provider(monkeypatch)
        provider.id_token_claims = {"tid": TENANT}

        provider.verify_tenant()

    def test_tenant_comparison_ignores_casing(self, monkeypatch):
        provider = make_provider(monkeypatch)
        provider.id_token_claims = {"tid": TENANT.upper()}

        provider.verify_tenant()

    def test_a_token_from_another_tenant_is_refused(self, monkeypatch):
        provider = make_provider(monkeypatch)
        provider.id_token_claims = {"tid": OTHER_TENANT}

        with pytest.raises(AuthenticationException):
            provider.verify_tenant()

    def test_a_token_with_no_tenant_claim_is_refused(self, monkeypatch):
        provider = make_provider(monkeypatch)
        provider.id_token_claims = {}

        with pytest.raises(AuthenticationException):
            provider.verify_tenant()


@pytest.mark.unit
class TestNonceVerification:
    def test_the_nonce_from_this_browser_is_accepted(self, monkeypatch):
        session = {EntraOAuthProvider.NONCE_SESSION_KEY: NONCE}
        provider = make_provider(monkeypatch, session=session)
        provider.id_token_claims = {"nonce": NONCE}

        provider.verify_nonce()

    def test_a_token_carrying_another_nonce_is_refused(self, monkeypatch):
        """
        An id token captured from a different sign-in — same tenant, same
        application, another person — is otherwise indistinguishable from the
        one this flow asked for.
        """
        session = {EntraOAuthProvider.NONCE_SESSION_KEY: NONCE}
        provider = make_provider(monkeypatch, session=session)
        provider.id_token_claims = {"nonce": "someone-elses-nonce"}

        with pytest.raises(AuthenticationException):
            provider.verify_nonce()

    def test_a_token_with_no_nonce_is_refused(self, monkeypatch):
        session = {EntraOAuthProvider.NONCE_SESSION_KEY: NONCE}
        provider = make_provider(monkeypatch, session=session)
        provider.id_token_claims = {}

        with pytest.raises(AuthenticationException):
            provider.verify_nonce()

    def test_a_flow_that_did_not_start_here_is_refused(self, monkeypatch):
        """No nonce in the session: this callback belongs to another flow."""
        provider = make_provider(monkeypatch, session={})
        provider.id_token_claims = {"nonce": NONCE}

        with pytest.raises(AuthenticationException):
            provider.verify_nonce()

    def test_the_nonce_is_single_use(self, monkeypatch):
        session = {EntraOAuthProvider.NONCE_SESSION_KEY: NONCE}
        provider = make_provider(monkeypatch, session=session)
        provider.id_token_claims = {"nonce": NONCE}

        provider.verify_nonce()

        assert EntraOAuthProvider.NONCE_SESSION_KEY not in session
        with pytest.raises(AuthenticationException):
            provider.verify_nonce()

    def test_the_nonce_is_consumed_even_when_it_does_not_match(self, monkeypatch):
        """A failed attempt must not leave a value a replay could aim at."""
        session = {EntraOAuthProvider.NONCE_SESSION_KEY: NONCE}
        provider = make_provider(monkeypatch, session=session)
        provider.id_token_claims = {"nonce": "wrong"}

        with pytest.raises(AuthenticationException):
            provider.verify_nonce()

        assert EntraOAuthProvider.NONCE_SESSION_KEY not in session


@pytest.mark.unit
class TestSetTokenData:
    """The three checks have to run, in order, on the real exchange path."""

    @staticmethod
    def exchange(provider, monkeypatch, id_token):
        monkeypatch.setattr(
            provider,
            "get_user_token",
            lambda data, headers=None: {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "id_token": id_token,
            },
        )
        provider.set_token_data()

    def test_a_valid_exchange_stores_the_token(self, monkeypatch, tenant_key):
        session = {EntraOAuthProvider.NONCE_SESSION_KEY: NONCE}
        provider = make_provider(monkeypatch, session=session)
        use_key(monkeypatch, tenant_key)
        id_token = sign(make_claims(), tenant_key)

        self.exchange(provider, monkeypatch, id_token)

        assert provider.token_data["id_token"] == id_token
        assert provider.token_data["access_token"] == "access"
        assert provider.id_token_claims["tid"] == TENANT

    def test_a_token_from_another_tenant_never_reaches_the_account(self, monkeypatch, tenant_key):
        """
        Defence in depth: `iss` already pins the tenant, but `verify_tenant`
        stays so the trust argument does not rest on one library option.
        """
        session = {EntraOAuthProvider.NONCE_SESSION_KEY: NONCE}
        provider = make_provider(monkeypatch, session=session)
        use_key(monkeypatch, tenant_key)

        with pytest.raises(AuthenticationException):
            self.exchange(provider, monkeypatch, sign(make_claims(tid=OTHER_TENANT), tenant_key))
        assert provider.token_data is None

    def test_a_replayed_token_never_reaches_the_account(self, monkeypatch, tenant_key):
        provider = make_provider(monkeypatch, session={})
        use_key(monkeypatch, tenant_key)

        with pytest.raises(AuthenticationException):
            self.exchange(provider, monkeypatch, sign(make_claims(), tenant_key))
        assert provider.token_data is None

    def test_a_response_with_no_id_token_is_refused(self, monkeypatch):
        provider = make_provider(monkeypatch, session={EntraOAuthProvider.NONCE_SESSION_KEY: NONCE})

        with pytest.raises(AuthenticationException):
            self.exchange(provider, monkeypatch, "")
        assert provider.token_data is None


@pytest.mark.unit
class TestEmailResolution:
    @pytest.fixture
    def provider(self, monkeypatch):
        return make_provider(monkeypatch)

    def test_the_mail_attribute_is_preferred(self, provider):
        assert provider.resolve_email({"mail": "person@example.com"}) == "person@example.com"

    def test_the_user_principal_name_is_the_fallback(self, provider):
        """Plenty of tenants leave `mail` unset for cloud-only accounts."""
        assert provider.resolve_email({"userPrincipalName": "person@example.com"}) == "person@example.com"

    def test_a_guest_user_principal_name_is_refused(self, provider):
        """
        A guest's UPN (`someone_other.com#EXT#@tenant.onmicrosoft.com`) is an
        internal identifier, not a mailbox, so matching an account on it would
        associate the sign-in with the wrong address.
        """
        with pytest.raises(AuthenticationException):
            provider.resolve_email({"userPrincipalName": "person_other.com#EXT#@tenant.onmicrosoft.com"})

    def test_a_guest_is_refused_even_when_the_mail_attribute_is_populated(self, provider):
        """
        The case the guest rule exists for, and the one it used to miss.

        Modern tenants do populate `mail` for B2B guests, with the address in
        their *home* tenant. Preferring `mail` and only inspecting the UPN as a
        fallback meant every such guest signed in normally, while the
        documentation promised guests were refused. Since a default Entra
        tenant lets any member invite a guest, that quietly widened who may
        sign in from "people this organization employs" to "people any employee
        has invited" — and the address they arrive with is one somebody else's
        tenant controls.
        """
        with pytest.raises(AuthenticationException):
            provider.resolve_email(
                {
                    "mail": "person@other-company.com",
                    "userPrincipalName": "person_other-company.com#EXT#@tenant.onmicrosoft.com",
                }
            )

    def test_the_guest_marker_is_matched_whatever_its_casing(self, provider):
        """Graph is not consistent about the casing of the marker."""
        with pytest.raises(AuthenticationException):
            provider.resolve_email(
                {
                    "mail": "person@other-company.com",
                    "userPrincipalName": "person_other-company.com#ext#@tenant.onmicrosoft.com",
                }
            )

    def test_a_member_whose_mailbox_is_on_another_domain_still_signs_in(self, provider):
        """
        Not every non-tenant-looking address is a guest: plenty of tenants have
        verified domains that differ from the UPN suffix. Only the `#EXT#`
        marker refuses, so these keep working.
        """
        email = provider.resolve_email(
            {"mail": "person@brand.example", "userPrincipalName": "person@tenant.onmicrosoft.com"}
        )

        assert email == "person@brand.example"

    def test_a_response_with_no_usable_address_is_refused(self, provider):
        with pytest.raises(AuthenticationException):
            provider.resolve_email({"id": "abc", "displayName": "No Mailbox"})


# An authentication error code is mirrored in five places: the Python table,
# the web helper, the space helper, and the message catalogue of every locale.
# Nothing in the build compares them, and a number that disagrees between the
# API and a helper shows the person a sentence about a different failure.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[6]
WEB_HELPER = REPO_ROOT / "apps/web/helpers/authentication.helper.tsx"
SPACE_HELPER = REPO_ROOT / "apps/space/helpers/authentication.helper.tsx"
LOCALES = REPO_ROOT / "packages/i18n/src/locales"

# code name -> the i18n key its message map entry points at
ENTRA_CODES = {
    "ENTRA_NOT_CONFIGURED": "not_configured",
    "ENTRA_OAUTH_PROVIDER_ERROR": "sign_in_failed",
    "ENTRA_ID_TOKEN_INVALID": "token_invalid",
    "ENTRA_NONCE_MISMATCH": "nonce_mismatch",
}

needs_repo = pytest.mark.skipif(
    not WEB_HELPER.is_file(),
    reason="runs from a full checkout; the Docker test stack mounts only apps/api",
)


@pytest.mark.unit
@needs_repo
class TestEntraErrorCodeParity:
    @staticmethod
    def numbers_in(helper: pathlib.Path) -> dict:
        source = helper.read_text(encoding="utf-8")
        return {
            name: int(match.group(1))
            for name in ENTRA_CODES
            for match in [re.search(rf"^\s*{name} = \"(\d+)\",", source, re.MULTILINE)]
            if match
        }

    @pytest.mark.parametrize("helper", [WEB_HELPER, SPACE_HELPER], ids=["web", "space"])
    def test_the_helpers_carry_the_same_numbers_as_the_api(self, helper):
        expected = {name: AUTHENTICATION_ERROR_CODES[name] for name in ENTRA_CODES}

        assert self.numbers_in(helper) == expected

    @pytest.mark.parametrize("helper", [WEB_HELPER, SPACE_HELPER], ids=["web", "space"])
    def test_every_code_has_a_message_and_is_grouped(self, helper):
        source = helper.read_text(encoding="utf-8")

        for name in ENTRA_CODES:
            # once in the enum, once in the message map, once in the group list
            assert source.count(f"EAuthenticationErrorCodes.{name}") >= 2, name

    def test_every_message_key_exists_in_every_locale(self):
        for locale in sorted(p.name for p in LOCALES.iterdir() if p.is_dir()):
            catalogue = json.loads((LOCALES / locale / "auth.json").read_text(encoding="utf-8"))
            entra = catalogue["auth"]["errors"]["entra"]
            for key in ENTRA_CODES.values():
                assert entra[key]["title"].strip(), (locale, key)
                assert entra[key]["message"].strip(), (locale, key)

    def test_the_codes_do_not_collide(self):
        numbers = [AUTHENTICATION_ERROR_CODES[name] for name in ENTRA_CODES]
        assert len(numbers) == len(set(numbers))
        # Every other name in the table has to stay on a different number too.
        others = [v for k, v in AUTHENTICATION_ERROR_CODES.items() if k not in ENTRA_CODES]
        assert not (set(numbers) & set(others))
