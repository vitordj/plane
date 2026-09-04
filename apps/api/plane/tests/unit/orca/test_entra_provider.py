# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the Microsoft Entra ID sign-in provider.

The provider's security rests on two decisions, and both are tested here
because getting either wrong is an account-takeover path rather than a bug:
the tenant is pinned and verified against the token's ``tid`` claim, and the
email is taken only from attributes the tenant actually vouches for.
"""

import base64
import json

import pytest

from plane.authentication.adapter.error import AuthenticationException
from plane.authentication.provider.oauth.entra import (
    MULTI_TENANT_AUTHORITIES,
    EntraOAuthProvider,
    decode_id_token_claims,
)

TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"


def make_id_token(claims: dict) -> str:
    """Build a token whose payload decodes to the given claims."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"header.{payload}.signature"


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
class TestIdTokenClaims:
    def test_claims_are_decoded_from_an_unpadded_payload(self):
        """JWT strips base64 padding; decoding has to put it back."""
        token = make_id_token({"tid": TENANT, "oid": "abc"})

        assert decode_id_token_claims(token)["tid"] == TENANT

    @pytest.mark.parametrize("token", ["", "not-a-token", "a.!!!.c", None])
    def test_a_malformed_token_decodes_to_nothing_rather_than_raising(self, token):
        """A crash here would surface as a 500 in the middle of a sign-in."""
        assert decode_id_token_claims(token) == {}


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

    def test_a_guest_is_refused_even_when_the_mail_attribute_is_populated(self):
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
        provider = StubProvider()

        with pytest.raises(AuthenticationException):
            provider.resolve_email(
                {
                    "mail": "person@other-company.com",
                    "userPrincipalName": "person_other-company.com#EXT#@tenant.onmicrosoft.com",
                }
            )

    def test_the_guest_marker_is_matched_whatever_its_casing(self):
        """Graph is not consistent about the casing of the marker."""
        provider = StubProvider()

        with pytest.raises(AuthenticationException):
            provider.resolve_email(
                {
                    "mail": "person@other-company.com",
                    "userPrincipalName": "person_other-company.com#ext#@tenant.onmicrosoft.com",
                }
            )

    def test_a_member_whose_mailbox_is_on_another_domain_still_signs_in(self):
        """
        Not every non-tenant-looking address is a guest: plenty of tenants have
        verified domains that differ from the UPN suffix. Only the `#EXT#`
        marker refuses, so these keep working.
        """
        provider = StubProvider()

        email = provider.resolve_email(
            {"mail": "person@brand.example", "userPrincipalName": "person@tenant.onmicrosoft.com"}
        )

        assert email == "person@brand.example"

    def test_a_response_with_no_usable_address_is_refused(self):
        provider = StubProvider()

        with pytest.raises(AuthenticationException):
            provider.resolve_email({"id": "abc", "displayName": "No Mailbox"})
