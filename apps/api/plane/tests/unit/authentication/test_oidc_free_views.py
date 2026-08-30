# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import override_settings

from plane.authentication.views.app.oidc_free import OidcFreeCallbackEndpoint

APP_URL = "https://plane.example.com"


def _callback(session, query=None):
    """Run the app callback against a request carrying the given session."""
    request = MagicMock()
    request.GET = query or {}
    request.session = session
    with override_settings(APP_BASE_URL=APP_URL, WEB_URL=APP_URL):
        return OidcFreeCallbackEndpoint().get(request)


@pytest.mark.unit
class TestOidcFreeCallbackRedirects:
    """A callback that cannot proceed still has to send the browser somewhere absolute."""

    def test_a_callback_without_a_session_leaves_the_callback(self):
        """A relative redirect here resolves back to this endpoint and loops forever.

        Reached whenever the session is gone by the time the provider redirects back —
        a link out of the browser's history, an expired session, cleared cookies.
        """
        response = _callback(session={})

        assert response.status_code == 302
        assert urlparse(response.url).netloc, "the redirect has to be absolute, or it loops"
        assert not response.url.startswith("?")
        assert response.url.startswith(APP_URL)

    def test_a_state_mismatch_reports_the_provider_error(self):
        response = _callback(session={"state": "the-state"}, query={"state": "another-state"})

        assert parse_qs(urlparse(response.url).query)["error_code"] == ["5114"]

    def test_a_callback_without_a_code_reports_the_provider_error(self):
        response = _callback(session={"state": "the-state"}, query={"state": "the-state"})

        assert response.url.startswith(APP_URL)
        assert parse_qs(urlparse(response.url).query)["error_code"] == ["5114"]

    def test_the_next_path_survives_the_error_redirect(self):
        response = _callback(session={"state": "the-state", "next_path": "/projects"}, query={"state": "the-state"})

        assert parse_qs(urlparse(response.url).query)["next_path"] == ["/projects"]
