# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The Orca layer's error codes.

A code is a contract: the web app looks it up in the translation catalogue, so
renumbering one silently changes which sentence a person reads. These tests pin
the table itself, the response shape built from it, that real endpoints send it,
and that the four places the table is mirrored still agree.
"""

import json
import pathlib
import re

import pytest
from rest_framework import status

from plane.utils.error_codes import ERROR_CODES
from plane.utils.orca_error_codes import (
    ORCA_ERROR_CODES,
    ORCA_ERROR_MESSAGES,
    orca_error,
    orca_not_found,
)

from .conftest import unit_url, units_url

REPO_ROOT = pathlib.Path(__file__).resolve().parents[6]
CONSTANTS_TS = REPO_ROOT / "packages/constants/src/orca/error-codes.ts"
EN_CATALOGUE = REPO_ROOT / "packages/i18n/src/locales/en/workspace-settings.json"


@pytest.mark.unit
class TestTheTableItself:
    def test_every_code_is_unique(self):
        # A duplicate number means two failures translate to the same sentence.
        numbers = list(ORCA_ERROR_CODES.values())
        assert len(numbers) == len(set(numbers))

    def test_every_code_has_a_message(self):
        assert set(ORCA_ERROR_CODES) == set(ORCA_ERROR_MESSAGES)

    def test_no_message_is_empty(self):
        assert all(m.strip() for m in ORCA_ERROR_MESSAGES.values())

    def test_codes_stay_in_the_fork_band(self):
        # 4900-4999 is the fork's; upstream owns everything below it.
        assert all(4900 <= n <= 4999 for n in ORCA_ERROR_CODES.values())

    def test_codes_do_not_collide_with_upstream(self):
        assert not (set(ORCA_ERROR_CODES.values()) & set(ERROR_CODES.values()))


@pytest.mark.unit
class TestTheResponseShape:
    def test_carries_prose_code_and_name(self):
        response = orca_error("ORG_UNIT_NOT_FOUND")
        assert response.data == {
            "error": "Organizational unit not found",
            "error_code": 4900,
            "error_message": "ORG_UNIT_NOT_FOUND",
        }

    def test_defaults_to_bad_request(self):
        assert orca_error("ORG_UNIT_NAME_REQUIRED").status_code == status.HTTP_400_BAD_REQUEST

    def test_status_can_be_overridden(self):
        response = orca_error("ORG_UNIT_NOT_FOUND", status.HTTP_409_CONFLICT)
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_not_found_helper_uses_404(self):
        assert orca_not_found("ORG_UNIT_NOT_FOUND").status_code == status.HTTP_404_NOT_FOUND

    def test_an_unknown_name_raises(self):
        # A typo should fail here rather than ship a response with a null code.
        with pytest.raises(KeyError):
            orca_error("ORG_UNIT_TYPOED")

    @pytest.mark.parametrize("name", sorted(ORCA_ERROR_CODES))
    def test_every_code_builds(self, name):
        response = orca_error(name)
        assert response.data["error_code"] == ORCA_ERROR_CODES[name]
        assert response.data["error_message"] == name
        assert response.data["error"] == ORCA_ERROR_MESSAGES[name]


@pytest.mark.unit
@pytest.mark.django_db
class TestEndpointsSendTheCode:
    """The contract is only real if the views actually use it."""

    def test_a_missing_unit_answers_with_its_code(self, admin_client, workspace_with_members):
        response = admin_client.get(unit_url(workspace_with_members.slug, "00000000-0000-0000-0000-000000000000"))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NOT_FOUND"]
        assert response.data["error_message"] == "ORG_UNIT_NOT_FOUND"
        # The English prose stays for API clients and logs.
        assert response.data["error"] == ORCA_ERROR_MESSAGES["ORG_UNIT_NOT_FOUND"]

    def test_a_nameless_unit_answers_with_its_code(self, admin_client, workspace_with_members):
        response = admin_client.post(units_url(workspace_with_members.slug), {"name": ""}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_NAME_REQUIRED"]

    def test_a_duplicate_slug_answers_with_its_code(self, admin_client, workspace_with_members, unit):
        response = admin_client.post(units_url(workspace_with_members.slug), {"name": unit.name}, format="json")
        # 409, not 400: the conversion to coded errors kept each call site's
        # original status rather than flattening everything to bad request.
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error_code"] == ORCA_ERROR_CODES["ORG_UNIT_SLUG_TAKEN"]


@pytest.mark.unit
class TestTheTableDoesNotDrift:
    """
    The same codes appear in four places: this Python table, the
    ORCA_ERROR_CODE_KEYS map the web app reads, the catalogue keys that map
    points at, and the English values behind them. Any one of them going out of
    step ships a blank or wrong message, and nothing else would notice.
    """

    def load_ts_map(self):
        if not CONSTANTS_TS.exists():
            pytest.skip("constants package not in this checkout")
        source = CONSTANTS_TS.read_text(encoding="utf-8")
        match = re.search(r"ORCA_ERROR_CODE_KEYS[^=]*=\s*\{(.*?)\n\};", source, re.DOTALL)
        assert match, "could not find ORCA_ERROR_CODE_KEYS"
        return {int(code): key for code, key in re.findall(r'(\d+):\s*"([^"]+)"', match.group(1))}

    def flatten(self, node, prefix=""):
        for key, value in node.items():
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                yield from self.flatten(value, f"{path}.")
            else:
                yield path, value

    def test_the_web_map_covers_exactly_these_codes(self):
        assert set(self.load_ts_map()) == set(ORCA_ERROR_CODES.values())

    def test_every_mapped_key_exists_in_english(self):
        if not EN_CATALOGUE.exists():
            pytest.skip("catalogue not in this checkout")
        catalogue = dict(self.flatten(json.loads(EN_CATALOGUE.read_text(encoding="utf-8"))))
        missing = [key for key in self.load_ts_map().values() if key not in catalogue]
        assert not missing, f"catalogue keys referenced by error codes but absent: {missing}"

    def test_each_code_maps_to_its_own_key(self):
        keys = list(self.load_ts_map().values())
        assert len(keys) == len(set(keys)), "two codes share a message"
