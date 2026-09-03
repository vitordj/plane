# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the Microsoft Entra ID sign-in provider.

The provider's security rests on decisions that are account-takeover paths
rather than bugs when they go wrong, so each is tested here: the id token is
verified against Microsoft's signing key before any claim is read, the tenant
is pinned and checked against the token's ``tid``, the nonce ties the token to
the browser that began the sign-in, and the email is taken only from
attributes the tenant actually vouches for.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from plane.authentication.adapter.error import AuthenticationException
from plane.authentication.provider.oauth.entra import (
    MULTI_TENANT_AUTHORITIES,
    EntraOAuthProvider,
)
from plane.authentication.utils import entra_id_token
from plane.authentication.utils.entra_id_token import verify_id_token

TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
CLIENT_ID = "d1c2b3a4-0000-4444-8888-abcdefabcdef"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
NONCE = "3f7c1c0a9b2e4d5f8a1b2c3d4e5f6071"


@pytest.fixture(scope="module")
def signing_key():
    """A throwaway RSA key standing in for Microsoft's."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key():
    """A second key, for the token nobody should accept."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def as_microsoft(monkeypatch, signing_key):
    """
    Answer key lookups with the test key instead of reaching Microsoft.

    @description The real client fetches the tenant's JWKS over the network.
    What is under test is the verification, not the fetch, so the lookup is
    replaced and the token really is signed and really is verified.
    """

    class StubKey:
        key = signing_key.public_key()

    class StubClient:
        def get_signing_key_from_jwt(self, token):
            return StubKey()

    monkeypatch.setattr(entra_id_token, "jwks_client", lambda tenant_id: StubClient())


def claims(**overrides):
    now = int(time.time())
    payload = {
        "aud": CLIENT_ID,
        "iss": ISSUER,
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "tid": TENANT,
        "oid": "9c6f2e11-1111-2222-3333-444455556666",
        "nonce": NONCE,
    }
    payload.update(overrides)
    return payload


def make_id_token(payload, key):
    return jwt.encode(payload, key, algorithm="RS256")


def verify(token, expected_nonce=NONCE):
    return verify_id_token(token, tenant_id=TENANT, audience=CLIENT_ID, expected_nonce=expected_nonce)


class StubProvider(EntraOAuthProvider):
    """
    The provider with its constructor skipped.

    @description ``__init__`` reads instance configuration and builds the OAuth
    URLs, none of which the tenant and email rules depend on. Constructing the
    object directly keeps these tests about the two decisions that matter.
    """

    def __init__(self, tenant_id=TENANT, claims=None):
        self.tenant_id = tenant_id
        self.id_token_claims = claims or {}


@pytest.mark.unit
@pytest.mark.usefixtures("as_microsoft")
class TestIdTokenVerification:
    """
    Each case here was accepted before this verification existed: the payload
    was read straight off the token with no signature check at all.
    """

    def test_a_well_formed_token_is_accepted(self, signing_key):
        verified = verify(make_id_token(claims(), signing_key))

        assert verified["tid"] == TENANT
        assert verified["oid"] == "9c6f2e11-1111-2222-3333-444455556666"

    def test_a_token_signed_by_someone_else_is_refused(self, other_key):
        """The forged token: right claims, wrong key."""
        with pytest.raises(AuthenticationException) as exc:
            verify(make_id_token(claims(), other_key))

        assert exc.value.error_code == 5127

    def test_a_token_for_another_audience_is_refused(self, signing_key):
        """A token minted for a different app registration is not ours to trust."""
        with pytest.raises(AuthenticationException):
            verify(make_id_token(claims(aud="00000000-0000-0000-0000-000000000000"), signing_key))

    def test_a_token_from_another_issuer_is_refused(self, signing_key):
        with pytest.raises(AuthenticationException):
            verify(make_id_token(claims(iss="https://login.microsoftonline.com/somewhere-else/v2.0"), signing_key))

    def test_an_expired_token_is_refused(self, signing_key):
        past = int(time.time()) - 3600
        with pytest.raises(AuthenticationException):
            verify(make_id_token(claims(iat=past, nbf=past, exp=past + 60), signing_key))

    def test_a_token_that_is_not_valid_yet_is_refused(self, signing_key):
        future = int(time.time()) + 3600
        with pytest.raises(AuthenticationException):
            verify(make_id_token(claims(nbf=future, exp=future + 600), signing_key))

    def test_a_token_missing_the_tenant_claim_is_refused(self, signing_key):
        payload = claims()
        payload.pop("tid")
        with pytest.raises(AuthenticationException):
            verify(make_id_token(payload, signing_key))

    @pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", None])
    def test_a_malformed_token_is_refused_rather_than_crashing(self, token):
        """A traceback here would surface as a 500 in the middle of a sign-in."""
        with pytest.raises(AuthenticationException):
            verify(token)


@pytest.mark.unit
@pytest.mark.usefixtures("as_microsoft")
class TestNonce:
    def test_the_matching_nonce_is_accepted(self, signing_key):
        assert verify(make_id_token(claims(), signing_key))["nonce"] == NONCE

    def test_a_different_nonce_is_refused(self, signing_key):
        """A token minted for another sign-in of the same tenant."""
        with pytest.raises(AuthenticationException) as exc:
            verify(make_id_token(claims(nonce="0000"), signing_key))

        assert exc.value.error_code == 5128

    def test_a_token_without_a_nonce_is_refused(self, signing_key):
        payload = claims()
        payload.pop("nonce")
        with pytest.raises(AuthenticationException) as exc:
            verify(make_id_token(payload, signing_key))

        assert exc.value.error_code == 5128

    def test_a_browser_with_no_nonce_in_session_is_refused(self, signing_key):
        """
        Fail closed: a sign-in that cannot be tied back to the browser that
        began it is the case the nonce exists to catch.
        """
        with pytest.raises(AuthenticationException) as exc:
            verify(make_id_token(claims(), signing_key), expected_nonce=None)

        assert exc.value.error_code == 5128


@pytest.mark.unit
class TestTenantVerification:
    def test_a_token_from_the_configured_tenant_is_accepted(self):
        StubProvider(claims={"tid": TENANT}).verify_tenant()

    def test_tenant_comparison_ignores_casing(self):
        StubProvider(claims={"tid": TENANT.upper()}).verify_tenant()

    def test_a_token_from_another_tenant_is_refused(self):
        """
        This is the attack the pinning exists for: anyone can create their own
        Azure tenant and put a user in it whose email is somebody else's.
        """
        with pytest.raises(AuthenticationException):
            StubProvider(claims={"tid": "11111111-2222-3333-4444-555555555555"}).verify_tenant()

    def test_a_token_with_no_tenant_claim_is_refused(self):
        with pytest.raises(AuthenticationException):
            StubProvider(claims={}).verify_tenant()

    def test_the_multi_tenant_authorities_are_known(self):
        """Guards the constructor's rejection list against a silent edit."""
        assert MULTI_TENANT_AUTHORITIES == {"common", "organizations", "consumers"}


@pytest.mark.unit
class TestEmailResolution:
    def test_the_mail_attribute_is_preferred(self):
        provider = StubProvider()

        email = provider.resolve_email({"mail": "person@example.com", "userProxy": None})

        assert email == "person@example.com"

    def test_the_user_principal_name_is_the_fallback(self):
        """Plenty of tenants leave `mail` unset for cloud-only accounts."""
        provider = StubProvider()

        assert provider.resolve_email({"userPrincipalName": "person@example.com"}) == "person@example.com"

    def test_a_guest_user_principal_name_is_refused(self):
        """
        A guest's UPN (`someone_other.com#EXT#@tenant.onmicrosoft.com`) is an
        internal identifier, not a mailbox, so matching an account on it would
        associate the sign-in with the wrong address.
        """
        provider = StubProvider()

        with pytest.raises(AuthenticationException):
            provider.resolve_email({"userPrincipalName": "person_other.com#EXT#@tenant.onmicrosoft.com"})

    def test_a_response_with_no_usable_address_is_refused(self):
        provider = StubProvider()

        with pytest.raises(AuthenticationException):
            provider.resolve_email({"id": "abc", "displayName": "No Mailbox"})
