# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Sign-in endpoints for Microsoft Entra ID (see
``plane.authentication.provider.oauth.entra``). Mirrors the shape of the
upstream providers exactly, so the flow, the session handling and the error
redirects stay identical to Google's and GitHub's — the only fork-specific
part is the provider itself.
"""

import uuid
from urllib.parse import urlencode, urljoin

# Django import
from django.http import HttpResponseRedirect
from django.views import View

# Module imports
from plane.authentication.provider.oauth.entra import EntraOAuthProvider
from plane.authentication.utils.login import user_login
from plane.authentication.utils.redirection_path import get_redirection_path
from plane.authentication.utils.user_auth_workflow import post_user_auth_workflow
from plane.license.models import Instance
from plane.authentication.utils.host import base_host
from plane.authentication.adapter.error import (
    AuthenticationException,
    AUTHENTICATION_ERROR_CODES,
)
from plane.utils.path_validator import validate_next_path


class EntraOauthInitiateEndpoint(View):
    def get(self, request):
        # Get host and next path
        request.session["host"] = base_host(request=request, is_app=True)
        next_path = request.GET.get("next_path")
        if next_path:
            request.session["next_path"] = str(validate_next_path(next_path))

        # Check instance configuration
        instance = Instance.objects.first()
        if instance is None or not instance.is_setup_done:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["INSTANCE_NOT_CONFIGURED"],
                error_message="INSTANCE_NOT_CONFIGURED",
            )
            params = exc.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host(request=request, is_app=True), "?" + urlencode(params))
            return HttpResponseRedirect(url)
        try:
            state = uuid.uuid4().hex
            # `state` protects the callback URL, the nonce protects the token
            # itself: without it an id token captured from another sign-in in
            # the same tenant, for the same application, is indistinguishable
            # from one this flow just asked for. The provider compares it and
            # consumes it (see EntraOAuthProvider.verify_nonce).
            nonce = uuid.uuid4().hex
            provider = EntraOAuthProvider(request=request, state=state, nonce=nonce)
            request.session["state"] = state
            request.session[EntraOAuthProvider.NONCE_SESSION_KEY] = nonce
            auth_url = provider.get_auth_url()
            return HttpResponseRedirect(auth_url)
        except AuthenticationException as e:
            params = e.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host(request=request, is_app=True), "?" + urlencode(params))
            return HttpResponseRedirect(url)


class EntraCallbackEndpoint(View):
    def get(self, request):
        code = request.GET.get("code")
        state = request.GET.get("state")
        base_host = request.session.get("host")
        next_path = request.session.get("next_path")

        if state != request.session.get("state", ""):
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_OAUTH_PROVIDER_ERROR"],
                error_message="ENTRA_OAUTH_PROVIDER_ERROR",
            )
            params = exc.get_error_dict()
            if next_path:
                params["next_path"] = str(next_path)
            url = urljoin(base_host, "?" + urlencode(params))
            return HttpResponseRedirect(url)

        if not code:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ENTRA_OAUTH_PROVIDER_ERROR"],
                error_message="ENTRA_OAUTH_PROVIDER_ERROR",
            )
            params = exc.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host, "?" + urlencode(params))
            return HttpResponseRedirect(url)

        try:
            provider = EntraOAuthProvider(request=request, code=code, callback=post_user_auth_workflow)
            user = provider.authenticate()
            # Login the user and record his device info
            user_login(request=request, user=user, is_app=True)
            # Get the redirection path
            if next_path:
                path = str(validate_next_path(next_path))
            else:
                path = get_redirection_path(user=user)
            # redirect to referer path
            url = urljoin(base_host, path)
            return HttpResponseRedirect(url)
        except AuthenticationException as e:
            params = e.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host, "?" + urlencode(params))
            return HttpResponseRedirect(url)
