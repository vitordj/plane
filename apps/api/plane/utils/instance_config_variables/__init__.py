# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from .core import core_config_variables
from .extended import extended_config_variables
from .orca import orca_config_variables

instance_config_variables = [
    *core_config_variables,
    *extended_config_variables,
    # Fork additions, kept last and in their own module so upstream can grow
    # core.py and extended.py without colliding. See FORK.md.
    *orca_config_variables,
]
