# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import override_settings

from plane.authentication.views.app import oidc_free as app_views
from plane.authentication.views.space import oidc_free as space_views

APP_URL = "https://plane.example.com"
HOSTS = {"APP_BASE_URL": APP_URL, "WEB_URL": APP_URL, "SPACE_BASE_URL": APP_URL}


def _request(session, query=None):
    request = MagicMock()
    request.GET = query or {}
    request.session = session
    return request


def _app_callback(session, query=None):
    with override_settings(**HOSTS):
        return app_views.OidcFreeCallbackEndpoint().get(_request(session, query))


def _space_callback(session, query=None):
    with override_settings(**HOSTS):
        return space_views.OidcFreeCallbackSpaceEndpoint().get(_request(session, query))


@pytest.mark.unit
class TestOidcFreeCallbackRedirects:
    """A callback that cannot proceed still has to send the browser somewhere absolute."""

    def test_a_callback_without_a_session_leaves_the_callback(self):
        """A relative redirect here resolves back to this endpoint and loops forever.

        Reached whenever the session is gone by the time the provider redirects back —
        a link out of the browser's history, an expired session, cleared cookies.
        """
        response = _app_callback(session={})

        assert response.status_code == 302
        assert urlparse(response.url).netloc, "the redirect has to be absolute, or it loops"
        assert not response.url.startswith("?")
        assert response.url.startswith(APP_URL)

    def test_a_state_mismatch_reports_the_provider_error(self):
        response = _app_callback(session={"state": "the-state"}, query={"state": "another-state"})

        assert parse_qs(urlparse(response.url).query)["error_code"] == ["5114"]

    def test_a_callback_without_a_code_reports_the_provider_error(self):
        response = _app_callback(session={"state": "the-state"}, query={"state": "the-state"})

        assert response.url.startswith(APP_URL)
        assert parse_qs(urlparse(response.url).query)["error_code"] == ["5114"]

    def test_the_next_path_survives_the_error_redirect(self):
        response = _app_callback(session={"state": "the-state", "next_path": "/projects"}, query={"state": "the-state"})

        assert parse_qs(urlparse(response.url).query)["next_path"] == ["/projects"]

    def test_an_error_redirect_off_the_allowed_hosts_is_refused(self):
        """next_path is the one part of the URL a caller can influence."""
        response = _app_callback(
            session={"state": "the-state", "next_path": "https://evil.example.com/steal"},
            query={"state": "the-state"},
        )

        assert urlparse(response.url).netloc == urlparse(APP_URL).netloc

    def test_the_space_callback_redirects_to_the_space_host(self):
        response = _space_callback(session={"state": "the-state"}, query={"state": "another-state"})

        assert response.url.startswith(APP_URL)
        assert parse_qs(urlparse(response.url).query)["error_code"] == ["5114"]


@pytest.mark.unit
class TestOidcFreeSuccessRedirects:
    """Where a signed-in user lands, with the provider and the login stubbed out."""

    def _authenticated(self, views, endpoint, session, redirection_path=None):
        with (
            patch.object(views, "OidcFreeOAuthProvider", MagicMock()),
            patch.object(views, "user_login", MagicMock()),
            override_settings(**HOSTS),
        ):
            if redirection_path is not None:
                with patch.object(views, "get_redirection_path", return_value=redirection_path):
                    return endpoint().get(_request(session, {"code": "auth-code", "state": "the-state"}))
            return endpoint().get(_request(session, {"code": "auth-code", "state": "the-state"}))

    def test_the_space_success_path_does_not_double_the_separator(self):
        """base_host ends in "/spaces/" and next_path opens with "/"."""
        response = self._authenticated(
            space_views,
            space_views.OidcFreeCallbackSpaceEndpoint,
            session={"state": "the-state", "next_path": "/issues/the-issue"},
        )

        assert response.url == f"{APP_URL}/spaces/issues/the-issue"
        assert "//issues" not in response.url

    def test_the_space_success_path_without_a_next_path_lands_on_the_host(self):
        response = self._authenticated(
            space_views, space_views.OidcFreeCallbackSpaceEndpoint, session={"state": "the-state"}
        )

        assert response.url == f"{APP_URL}/spaces"

    def test_the_app_success_path_carries_the_next_path(self):
        response = self._authenticated(
            app_views,
            app_views.OidcFreeCallbackEndpoint,
            session={"state": "the-state", "next_path": "/projects"},
            redirection_path="workspace-slug",
        )

        assert response.url.startswith(APP_URL)
        assert parse_qs(urlparse(response.url).query)["next_path"] == ["/projects"]

    def test_the_app_success_path_refuses_a_next_path_off_the_allowed_hosts(self):
        response = self._authenticated(
            app_views,
            app_views.OidcFreeCallbackEndpoint,
            session={"state": "the-state", "next_path": "https://evil.example.com/steal"},
            redirection_path="workspace-slug",
        )

        assert urlparse(response.url).netloc == urlparse(APP_URL).netloc
