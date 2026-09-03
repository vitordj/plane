# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.apps import AppConfig


class AppApiConfig(AppConfig):
    name = "plane.app"

    def ready(self):
        # Orca (fork): attaches the layer's signal receivers. Imported for the
        # @receiver side effects only — see plane/app/services/orca/signals.py.
        from plane.app.services.orca import signals  # noqa: F401
