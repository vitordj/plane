# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.license.api.permissions import InstanceAdminPermission
from plane.utils.orca_build_info import build_info

from .base import BaseAPIView


class OrcaBuildInfoEndpoint(BaseAPIView):
    """
    Which commit this API container was built from.

    @description Restricted to instance admins: the answer names the exact
    commit of a deployment, which is a detail an anonymous caller has no
    business enumerating. It is deliberately outside the organizational kill
    switch — the whole point is to be answerable when something looks wrong,
    including a misconfigured switch.
    """

    permission_classes = [InstanceAdminPermission]

    def get(self, request):
        return Response(build_info(), status=status.HTTP_200_OK)
