# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What this container was built from.

The release chain (P0.0 to P0.3) proves which digest a commit produced and
which digest was promoted, but every one of those checks happens outside the
running system. Nothing inside a container said which commit it came from, so
"is staging running the code we reviewed?" could only be answered by trusting
the deploy pipeline. The image records the commit as an environment variable
at build time and this module reads it back.

The image digest is not knowable from inside the container, so the commit SHA
is the primary evidence here; the digest belongs to the registry side of the
chain.
"""

import json
import os
from typing import Any, Dict

from django.conf import settings

# Written into the image by the Dockerfiles from the GIT_SHA / IMAGE_TAG build
# args the stage workflow passes. Empty in a local build, which is itself the
# answer: this container did not come from CI.
BUILD_SHA_ENV = "ORCA_BUILD_SHA"
IMAGE_TAG_ENV = "ORCA_IMAGE_TAG"

# Set per service in docker-compose-orca.yml. api, worker, beat-worker and
# migrator all run the same image, so the variable is the only thing that says
# which of them answered.
SERVICE_ENV = "ORCA_SERVICE_NAME"


def _version() -> str:
    """
    @description Application version, read the same way
    ``register_instance`` reads it: the ``APP_VERSION`` environment variable
    when set, otherwise ``package.json``.
    @returns Version string, or an empty string when neither source has one.
    """
    env_version = os.environ.get("APP_VERSION")
    if env_version:
        return env_version

    try:
        with open("package.json", "r", encoding="utf-8") as handle:
            return json.load(handle).get("version", "")
    except Exception:
        return ""


def build_info() -> Dict[str, Any]:
    """
    @description Provenance of the running container: the commit it was built
    from, the immutable image tag that carries that commit, the service that
    answered, the application version and the state of the organizational kill
    switch (the one setting whose value has to be identical across api, worker
    and beat).
    @returns Dict with ``service``, ``version``, ``git_sha``, ``image_tag`` and
        ``orca_org_units_enabled``. ``git_sha`` and ``image_tag`` are empty
        strings for an image not built by CI.
    """
    return {
        "service": os.environ.get(SERVICE_ENV, "api"),
        "version": _version(),
        "git_sha": os.environ.get(BUILD_SHA_ENV, ""),
        "image_tag": os.environ.get(IMAGE_TAG_ENV, ""),
        "orca_org_units_enabled": bool(getattr(settings, "ORCA_ORG_UNITS_ENABLED", True)),
    }
