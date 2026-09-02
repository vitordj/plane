# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Whether the signed-in person follows the organization's interface language.

``Profile.language`` says which language somebody reads in; it cannot say
whether that was their decision. This endpoint exposes the sidecar row that
can, and lets a person hand the choice back — the only way to return to
following the organization once you have picked something.

Reading and writing your own preference needs no workspace role: it is the
same authorization as editing your own profile.
"""

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.services.orca.language import get_default_language
from plane.db.models import Profile, UserLanguagePreference

from .base import BaseAPIView


class UserLanguagePreferenceEndpoint(BaseAPIView):
    """``/api/orca/users/me/language-preference/``"""

    def _payload(self, request):
        preference = UserLanguagePreference.objects.filter(user=request.user).first()
        return {
            # No row means nobody has chosen, which is the common case: the
            # table only gains a row when somebody picks a language.
            "follows_organization_default": preference.follows_organization_default if preference else True,
            "organization_default_language": get_default_language(),
        }

    def get(self, request):
        return Response(self._payload(request), status=status.HTTP_200_OK)

    def patch(self, request):
        """Follow the organization's language again, or stop following it.

        @description Sending ``true`` also moves the person onto the current
            default — saying "use whatever the organization uses" and staying
            on yesterday's language would be a switch that does nothing.

            The profile write goes through ``queryset.update`` so it does not
            trip the receiver that records a personal choice; this is the one
            path where changing the language means the opposite of choosing.
        @returns: The same payload ``get`` returns.
        """
        follows = request.data.get("follows_organization_default")
        if not isinstance(follows, bool):
            return Response(
                {"error": "follows_organization_default must be true or false"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        UserLanguagePreference.objects.update_or_create(
            user=request.user,
            defaults={"follows_organization_default": follows},
        )

        if follows:
            Profile.objects.filter(user=request.user).update(language=get_default_language())

        return Response(self._payload(request), status=status.HTTP_200_OK)
