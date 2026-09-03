# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
The instance-wide default interface language.

Covers the four places the setting is felt: the reader that validates it, the
receiver that seeds new profiles with it, the public configuration endpoint
that carries it to the apps, and the command that moves an existing population
onto it. Plus a drift guard, because the list of supported languages exists in
four places across two languages and nothing but a test keeps them equal.
"""

import json
import pathlib
import re
from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from plane.app.services.orca.language import (
    FALLBACK_LANGUAGE,
    SUPPORTED_LANGUAGES,
    get_default_language,
    normalize_language,
)
from plane.db.models import Profile, User
from plane.license.models import Instance, InstanceConfiguration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[6]


@pytest.fixture
def set_default_language(db):
    """Write the DEFAULT_LANGUAGE instance configuration, cache cleared."""

    def _set(value):
        InstanceConfiguration.objects.update_or_create(
            key="DEFAULT_LANGUAGE",
            defaults={"value": value, "category": "LANGUAGE", "is_encrypted": False},
        )
        # The instance endpoint caches its response for two hours; a test that
        # changed the setting must not read the previous test's answer.
        cache.clear()

    return _set


class TestNormalizeLanguage:
    """The boundary between a settings field and a locale we can render."""

    @pytest.mark.parametrize("code", SUPPORTED_LANGUAGES)
    def test_every_supported_code_survives(self, code):
        assert normalize_language(code) == code

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "klingon",
            "pt",  # a real language, but not a catalogue directory
            "PT-BR",  # right locale, wrong case: i18next would ignore it
            "pt_BR",  # underscore form, as an operator might type it
            123,
            ["pt-BR"],
        ],
    )
    def test_anything_unusable_becomes_english(self, value):
        assert normalize_language(value) == FALLBACK_LANGUAGE

    def test_surrounding_whitespace_is_forgiven(self):
        assert normalize_language("  pt-BR  ") == "pt-BR"


@pytest.mark.django_db
class TestGetDefaultLanguage:
    def test_absent_configuration_is_english(self):
        assert get_default_language() == FALLBACK_LANGUAGE

    def test_configured_value_is_returned(self, set_default_language):
        set_default_language("pt-BR")
        assert get_default_language() == "pt-BR"

    def test_unrecognized_configuration_falls_back(self, set_default_language):
        set_default_language("not-a-language")
        assert get_default_language() == FALLBACK_LANGUAGE

    def test_empty_configuration_falls_back(self, set_default_language):
        set_default_language("")
        assert get_default_language() == FALLBACK_LANGUAGE


@pytest.mark.django_db
class TestNewProfilesInheritTheDefault:
    """The receiver in plane/app/services/orca/signals.py."""

    def make_user(self, email="lang@plane.so"):
        return User.objects.create(email=email, username=email.split("@")[0])

    def test_profile_is_born_in_the_instance_language(self, set_default_language):
        set_default_language("pt-BR")
        profile = Profile.objects.create(user=self.make_user())
        profile.refresh_from_db()
        assert profile.language == "pt-BR"

    def test_without_a_configuration_nothing_changes(self):
        profile = Profile.objects.create(user=self.make_user())
        profile.refresh_from_db()
        assert profile.language == FALLBACK_LANGUAGE

    def test_an_explicit_language_at_creation_is_respected(self, set_default_language):
        set_default_language("pt-BR")
        profile = Profile.objects.create(user=self.make_user(), language="ja")
        profile.refresh_from_db()
        assert profile.language == "ja"

    def test_a_broken_configuration_does_not_break_profile_creation(self, set_default_language):
        set_default_language("klingon")
        profile = Profile.objects.create(user=self.make_user())
        profile.refresh_from_db()
        assert profile.language == FALLBACK_LANGUAGE

    def test_the_receiver_never_touches_an_existing_profile(self, set_default_language):
        # Somebody signed up before the setting existed and chose English.
        profile = Profile.objects.create(user=self.make_user())
        set_default_language("de")

        # Any later save must leave their language alone: the receiver is a
        # birth event, not a policy that keeps re-applying.
        profile.is_tour_completed = True
        profile.save()
        profile.refresh_from_db()
        assert profile.language == FALLBACK_LANGUAGE

    def test_a_person_who_picks_a_language_keeps_it(self, set_default_language):
        set_default_language("de")
        profile = Profile.objects.create(user=self.make_user())
        profile.language = "fr"
        profile.save()
        profile.refresh_from_db()
        assert profile.language == "fr"


@pytest.mark.django_db
class TestInstanceEndpointCarriesTheDefault:
    """What the web, space and admin apps actually read on boot."""

    @pytest.fixture(autouse=True)
    def instance(self, db):
        # current_version and last_checked_at have no defaults on the model.
        return Instance.objects.create(
            instance_name="Orca",
            instance_id="orca-test",
            current_version="1.4.1",
            last_checked_at=timezone.now(),
        )

    def get_config(self, client):
        response = client.get(reverse("instance"))
        assert response.status_code == 200
        return response.data["config"]

    def test_default_language_is_published(self, client, set_default_language):
        set_default_language("pt-BR")
        assert self.get_config(client)["default_language"] == "pt-BR"

    def test_english_when_unset(self, client):
        assert self.get_config(client)["default_language"] == FALLBACK_LANGUAGE

    def test_a_bad_value_reaches_the_clients_as_english(self, client, set_default_language):
        # i18next silently ignores a code outside supportedLngs, which would
        # look like the setting having no effect. Normalize before it ships.
        set_default_language("klingon")
        assert self.get_config(client)["default_language"] == FALLBACK_LANGUAGE


@pytest.mark.django_db
class TestApplyDefaultLanguageCommand:
    """The one-off rollout for a population that predates the setting.

    Changing DEFAULT_LANGUAGE in god-mode now moves followers by itself (see
    test_language_preference.py), so what is left for this command is the
    population it cannot reach: an instance that took its default from the
    environment, where no configuration row was ever saved and everybody is
    still sitting on the language they were born in. Every test here sets the
    configuration up front and then puts the profiles where that instance would
    have left them.
    """

    def make_profile(self, email, language=None):
        user = User.objects.create(email=email, username=email.split("@")[0])
        profile = Profile.objects.create(user=user)
        # update() rather than save(): it skips the receivers, which is exactly
        # what an instance configured through the environment looks like.
        Profile.objects.filter(pk=profile.pk).update(language=language or FALLBACK_LANGUAGE)
        profile.refresh_from_db()
        return profile

    def run(self, *args):
        out = StringIO()
        call_command("apply_default_language", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_without_writing(self, set_default_language):
        set_default_language("pt-BR")
        profile = self.make_profile("stock@plane.so")

        output = self.run()

        assert "Would move 1 profile(s)" in output
        profile.refresh_from_db()
        assert profile.language == FALLBACK_LANGUAGE

    def test_apply_moves_profiles_still_on_the_stock_default(self, set_default_language):
        set_default_language("pt-BR")
        profile = self.make_profile("stock@plane.so")

        self.run("--apply")

        profile.refresh_from_db()
        assert profile.language == "pt-BR"

    def test_apply_leaves_a_chosen_language_alone(self, set_default_language):
        set_default_language("pt-BR")
        chosen = self.make_profile("chose@plane.so", language="ja")

        self.run("--apply")

        chosen.refresh_from_db()
        assert chosen.language == "ja"

    def test_language_flag_overrides_the_configuration(self, set_default_language):
        set_default_language("pt-BR")
        profile = self.make_profile("stock@plane.so")

        self.run("--apply", "--language", "de")

        profile.refresh_from_db()
        assert profile.language == "de"

    def test_an_unsupported_language_flag_is_refused(self):
        # Unlike the configuration reader, the flag is the operator's own words:
        # silently turning a typo into English would rewrite the wrong rows.
        with pytest.raises(CommandError, match="not a supported language"):
            self.run("--apply", "--language", "klingon")

    def test_nothing_to_do_when_the_default_is_english(self):
        profile = self.make_profile("stock@plane.so")
        output = self.run("--apply")
        assert "already" in output
        profile.refresh_from_db()
        assert profile.language == FALLBACK_LANGUAGE


class TestSupportedLanguagesDoNotDrift:
    """
    The catalogue's locale list exists in four places: the locale directories
    themselves, ``SUPPORTED_LANGUAGES`` in the i18n package, ``LANGUAGE_CHOICES``
    in the constants package (a deliberate mirror, so the admin app need not
    boot i18next), and the Python tuple this module validates against. Adding a
    locale to one and forgetting the rest is the failure this catches.
    """

    def read_ts_codes(self, relative_path, array_name):
        path = REPO_ROOT / relative_path
        if not path.exists():
            pytest.skip(f"{relative_path} not in this checkout")
        source = path.read_text(encoding="utf-8")
        match = re.search(rf"{array_name}[^=]*=\s*\[(.*?)\];", source, re.DOTALL)
        assert match, f"could not find {array_name} in {relative_path}"
        return set(re.findall(r'value:\s*"([^"]+)"', match.group(1)))

    def test_matches_the_i18n_package(self):
        assert self.read_ts_codes("packages/i18n/src/constants/language.ts", "SUPPORTED_LANGUAGES") == set(
            SUPPORTED_LANGUAGES
        )

    def test_matches_the_constants_package(self):
        assert self.read_ts_codes("packages/constants/src/language.ts", "LANGUAGE_CHOICES") == set(SUPPORTED_LANGUAGES)

    def test_matches_the_locale_directories(self):
        locales_dir = REPO_ROOT / "packages/i18n/src/locales"
        if not locales_dir.exists():
            pytest.skip("locale catalogue not in this checkout")
        directories = {p.name for p in locales_dir.iterdir() if p.is_dir()}
        assert directories == set(SUPPORTED_LANGUAGES)

    def read_ts_union(self, relative_path, type_name):
        """Pull the string members out of a `type X = "a" | "b" | ...` union."""
        path = REPO_ROOT / relative_path
        if not path.exists():
            pytest.skip(f"{relative_path} not in this checkout")
        source = path.read_text(encoding="utf-8")
        match = re.search(rf"export type {type_name}\s*=(.*?);", source, re.DOTALL)
        assert match, f"could not find {type_name} in {relative_path}"
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    def test_the_i18n_type_union_matches_its_array(self):
        # A locale added to SUPPORTED_LANGUAGES but not to TLanguage type-errors
        # at the call site rather than here, but it is cheaper to say so plainly.
        assert self.read_ts_union("packages/i18n/src/types/language.ts", "TLanguage") == set(SUPPORTED_LANGUAGES)

    def test_the_constants_type_union_matches_its_array(self):
        assert self.read_ts_union("packages/constants/src/language.ts", "TLanguageCode") == set(SUPPORTED_LANGUAGES)

    def test_the_two_lists_show_the_same_labels(self):
        """Codes agreeing is not enough — the picker renders the labels."""
        import re as _re

        def labels(relative_path):
            path = REPO_ROOT / relative_path
            if not path.exists():
                pytest.skip(f"{relative_path} not in this checkout")
            source = path.read_text(encoding="utf-8")
            return dict((value, label) for label, value in _re.findall(r'label: "([^"]+)", value: "([^"]+)"', source))

        assert labels("packages/i18n/src/constants/language.ts") == labels("packages/constants/src/language.ts")

    def test_the_config_seed_default_is_a_supported_language(self):
        path = REPO_ROOT / "apps/api/plane/utils/instance_config_variables/orca.py"
        source = path.read_text(encoding="utf-8")
        match = re.search(r'os\.environ\.get\("DEFAULT_LANGUAGE",\s*"([^"]+)"\)', source)
        assert match, "DEFAULT_LANGUAGE seed not found"
        assert match.group(1) in SUPPORTED_LANGUAGES

    def test_english_is_the_fallback_and_is_supported(self):
        assert FALLBACK_LANGUAGE in SUPPORTED_LANGUAGES
        # The source catalogue must be complete by definition.
        en_dir = REPO_ROOT / "packages/i18n/src/locales/en"
        if not en_dir.exists():
            pytest.skip("locale catalogue not in this checkout")
        namespaces = sorted(p.stem for p in en_dir.glob("*.json"))
        assert namespaces, "English catalogue is empty"
        for namespace in namespaces:
            content = json.loads((en_dir / f"{namespace}.json").read_text(encoding="utf-8"))
            assert isinstance(content, dict)
