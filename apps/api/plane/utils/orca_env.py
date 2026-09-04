# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Strict boolean parsing for the Orca environment switches.

Upstream reads its flags as ``os.environ.get(name, "0") == "1"``, which is fine
for a flag nobody reaches for in a hurry. The organizational layer's kill switch
is the opposite: an operator sets it to stop the layer writing native
``ProjectMember`` rows, and if they type ``ORCA_ORG_UNITS_ENABLED=false`` the
``== "1"`` comparison happens to disable it, but ``ORCA_ORG_UNITS_ENABLED=true``
or ``yes`` silently disables it too. A switch that reads "on" as "off" is not a
switch anyone can trust, so this module accepts the usual spellings and refuses
anything else at startup rather than guessing.
"""

# Python imports
import os

# Django imports
from django.core.exceptions import ImproperlyConfigured

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def parse_env_flag(name: str, raw_value, default: bool) -> bool:
    """
    Interpret one boolean environment value.

    @description Case-insensitive and whitespace-tolerant. An unset or blank
    value yields ``default``; any other spelling than the accepted ones raises,
    so a typo fails the process at boot instead of flipping the switch the
    wrong way in production.

    @param name: Variable name, used only in the error message.
    @param raw_value: The value as read from the environment (``None`` if unset).
    @param default: Value to use when the variable is unset or blank.
    @returns: The parsed boolean.
    @raises ImproperlyConfigured: When the value is not a recognised spelling.
    """
    if raw_value is None:
        return default
    normalized = str(raw_value).strip().lower()
    if normalized == "":
        return default
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ImproperlyConfigured(
        f"{name}={raw_value!r} is not a boolean. Use one of {sorted(TRUE_VALUES)} to enable "
        f"or {sorted(FALSE_VALUES)} to disable."
    )


def env_flag(name: str, default: bool) -> bool:
    """
    Read a boolean switch from ``os.environ`` with :func:`parse_env_flag`.

    @param name: Environment variable name.
    @param default: Value when the variable is unset or blank.
    @returns: The parsed boolean.
    """
    return parse_env_flag(name, os.environ.get(name), default)
