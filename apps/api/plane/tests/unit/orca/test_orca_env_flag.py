# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The kill switch has to read "on" as on and "off" as off, whatever the spelling.

``ORCA_ORG_UNITS_ENABLED`` was parsed as ``== "1"``, so an operator who wrote
``true`` — the spelling every other tool they run that day accepts — switched
the organizational layer *off* without knowing. The strict parser in
``plane.utils.orca_env`` accepts the common spellings and refuses the rest at
boot, which is the only safe answer for a switch that gates writes to
``ProjectMember``.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from plane.utils.orca_env import FALSE_VALUES, TRUE_VALUES, env_flag, parse_env_flag


@pytest.mark.unit
class TestParseEnvFlag:
    @pytest.mark.parametrize("raw", sorted(TRUE_VALUES) + ["TRUE", "Yes", " on ", "On"])
    def test_every_accepted_spelling_of_on_enables(self, raw):
        assert parse_env_flag("ORCA_ORG_UNITS_ENABLED", raw, default=False) is True

    @pytest.mark.parametrize("raw", sorted(FALSE_VALUES) + ["FALSE", "No", " off ", "Off"])
    def test_every_accepted_spelling_of_off_disables(self, raw):
        assert parse_env_flag("ORCA_ORG_UNITS_ENABLED", raw, default=True) is False

    @pytest.mark.parametrize("default", [True, False])
    def test_unset_and_blank_fall_back_to_the_default(self, default):
        assert parse_env_flag("ORCA_ORG_UNITS_ENABLED", None, default=default) is default
        assert parse_env_flag("ORCA_ORG_UNITS_ENABLED", "", default=default) is default
        assert parse_env_flag("ORCA_ORG_UNITS_ENABLED", "   ", default=default) is default

    @pytest.mark.parametrize("raw", ["enabled", "2", "y", "n", "null", "disable"])
    def test_anything_else_refuses_to_guess(self, raw):
        """
        The defect being pinned: ``true`` used to disable the layer silently.
        A value the parser does not recognise must stop the process, and the
        message must name the variable so the operator knows what to fix.
        """
        with pytest.raises(ImproperlyConfigured) as excinfo:
            parse_env_flag("ORCA_ORG_UNITS_ENABLED", raw, default=True)
        assert "ORCA_ORG_UNITS_ENABLED" in str(excinfo.value)
        assert raw in str(excinfo.value)

    def test_env_flag_reads_the_process_environment(self, monkeypatch):
        monkeypatch.setenv("ORCA_TEST_FLAG", "true")
        assert env_flag("ORCA_TEST_FLAG", default=False) is True
        monkeypatch.setenv("ORCA_TEST_FLAG", "off")
        assert env_flag("ORCA_TEST_FLAG", default=True) is False
        monkeypatch.delenv("ORCA_TEST_FLAG")
        assert env_flag("ORCA_TEST_FLAG", default=True) is True
