# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
from typing import Any

# Django imports
from django.core.management import BaseCommand

# Module imports
from plane.utils.orca_build_info import build_info


class Command(BaseCommand):
    help = "Print the commit, image tag and version this container was built from."

    def handle(self, *args: Any, **options: Any) -> None:
        # The worker and the beat container have no HTTP surface, so the
        # command is the only way to ask them the same question the endpoint
        # answers for the API — which is exactly the check that catches one
        # service left behind on an older image.
        self.stdout.write(json.dumps(build_info(), indent=2, sort_keys=True))
