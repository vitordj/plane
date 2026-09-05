# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What has to be true before ``/api/v1/orca/`` answers at all.

Two switches and a per-token budget. The switches are tested here against the
mixin rather than against a route, because the routes land with the endpoints
(item 1.4) and the gate has to be right before they do — a mixin that fails
open would expose every endpoint added later.
"""

import pytest
from django.http import Http404
from rest_framework.exceptions import NotFound

from plane.api.views.orca.base import OrcaPublicApiFeatureMixin, OrcaPublicBaseAPIView
from plane.app.services.orca.feature_flags import orca_public_api_enabled
from plane.throttles.orca_public import OrcaPublicThrottle
from plane.utils.orca_error_codes import ORCA_ERROR_CODES


class Gated(OrcaPublicApiFeatureMixin):
    """A minimal view-like object: the mixin only needs ``initial`` to chain."""

    def initial(self, request, *args, **kwargs):
        return "reached the view"


@pytest.mark.unit
class TestTheTwoSwitches:
    def test_both_on_lets_the_request_through(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = True
        settings.ORCA_PUBLIC_API_ENABLED = True
        assert orca_public_api_enabled() is True
        assert Gated().initial(None) == "reached the view"

    def test_the_public_switch_alone_is_not_enough(self, settings):
        # The layer is off, so there is no organizational data to drive.
        settings.ORCA_ORG_UNITS_ENABLED = False
        settings.ORCA_PUBLIC_API_ENABLED = True
        assert orca_public_api_enabled() is False

    def test_the_layer_switch_alone_is_not_enough(self, settings):
        # The layer runs for people in the app; machines with an API key are a
        # separate decision, and this is the state the instance ships in.
        settings.ORCA_ORG_UNITS_ENABLED = True
        settings.ORCA_PUBLIC_API_ENABLED = False
        assert orca_public_api_enabled() is False

    def test_it_is_off_by_default(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = True
        del settings.ORCA_PUBLIC_API_ENABLED
        # Absent means off: a switch that opens an API when unset is not a
        # switch anybody can deploy behind.
        assert orca_public_api_enabled() is False

    def test_it_is_read_at_call_time(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = True
        settings.ORCA_PUBLIC_API_ENABLED = True
        assert orca_public_api_enabled() is True
        settings.ORCA_PUBLIC_API_ENABLED = False
        # Not captured at import: flipping the setting takes effect on the next
        # request, not on the next process restart.
        assert orca_public_api_enabled() is False


@pytest.mark.unit
class TestTheGateWhenClosed:
    def test_it_answers_not_found(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = True
        settings.ORCA_PUBLIC_API_ENABLED = False
        with pytest.raises((NotFound, Http404)):
            Gated().initial(None)

    def test_it_carries_the_code_a_client_can_act_on(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = True
        settings.ORCA_PUBLIC_API_ENABLED = False
        with pytest.raises(NotFound) as caught:
            Gated().initial(None)

        detail = caught.value.detail
        # A bare 404 cannot tell "switched off here" from "wrong URL", and the
        # callers are programs that have to tell them apart.
        assert str(detail["error_code"]) == str(ORCA_ERROR_CODES["ORG_PUBLIC_API_DISABLED"])
        assert str(detail["error_message"]) == "ORG_PUBLIC_API_DISABLED"

    def test_the_layer_switch_closes_it_too(self, settings):
        settings.ORCA_ORG_UNITS_ENABLED = False
        settings.ORCA_PUBLIC_API_ENABLED = True
        with pytest.raises((NotFound, Http404)):
            Gated().initial(None)


@pytest.mark.unit
class TestTheThrottle:
    def test_the_base_view_uses_it(self):
        # Not the public API's shared ApiKeyRateThrottle: automation traffic
        # gets a budget of its own so it cannot starve ordinary API calls.
        throttles = OrcaPublicBaseAPIView().get_throttles()
        assert len(throttles) == 1
        assert isinstance(throttles[0], OrcaPublicThrottle)

    def test_it_is_keyed_on_the_token_id(self):
        class Token:
            id = "11111111-1111-1111-1111-111111111111"

        class View:
            api_token = Token()

        key = OrcaPublicThrottle().get_cache_key(None, View())
        assert key == f"orca_public:{Token.id}"

    def test_the_raw_token_never_reaches_the_cache_key(self):
        class Token:
            id = "11111111-1111-1111-1111-111111111111"

        class View:
            api_token = Token()

        # request.auth holds the secret for this authentication class; cache
        # keys surface in Redis monitoring and crash dumps.
        key = OrcaPublicThrottle().get_cache_key(None, View())
        assert "secret-token-value" not in key

    def test_no_token_means_no_bucket(self):
        class View:
            api_token = None

        # None makes SimpleRateThrottle allow rather than charge a shared
        # counter — otherwise an unauthenticated caller could drain the bucket
        # a real token's traffic draws on.
        assert OrcaPublicThrottle().get_cache_key(None, View()) is None

    def test_a_view_without_the_attribute_means_no_bucket(self):
        class View:
            pass

        assert OrcaPublicThrottle().get_cache_key(None, View()) is None

    def test_its_rate_comes_from_the_setting(self, settings):
        assert OrcaPublicThrottle.rate == settings.ORCA_PUBLIC_API_RATE_LIMIT

    def test_the_scope_is_registered_in_the_throttle_rates(self, settings):
        # Without the entry SimpleRateThrottle raises at request time rather
        # than at boot, so the endpoint would 500 instead of metering.
        assert "orca_public" in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
