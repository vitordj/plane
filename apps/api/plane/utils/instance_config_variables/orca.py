# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Instance configuration variables added by the fork (Orca).

Kept in its own module rather than appended to ``core.py`` so upstream can add
providers there without touching a line the fork owns. Only the one-line import
in ``__init__.py`` is shared surface.
"""

# Python imports
import os

# Interface language the instance falls back to: what new profiles are born
# with, and what the sign-in, sign-up and public pages render in before anyone
# has a profile at all. Must be one of the catalogue's locale codes — anything
# else is normalized to "en" when read. See
# plane/app/services/orca/language.py.
language_config_variables = [
    {
        "key": "DEFAULT_LANGUAGE",
        "value": os.environ.get("DEFAULT_LANGUAGE", "en"),
        "category": "LANGUAGE",
        "is_encrypted": False,
    },
]

orca_config_variables = [
    *language_config_variables,
]
