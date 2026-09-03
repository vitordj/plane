# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Telling "the organization's language" apart from "the language I chose".

``Profile.language`` holds a code and nothing else, so English on a profile can
mean either "I picked English" or "nobody ever opened the setting". That
ambiguity is what kept the instance default from reaching people who were
already signed up. These tests cover the sidecar that resolves it: the rule
that decides who follows, the receiver that notices a choice being made, the
receiver that moves followers when the organization changes its mind, and the
endpoint that lets a person hand the choice back.
"""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from plane.app.services.orca.language import FALLBACK_LANGUAGE, follower_profiles
from plane.db.models import Profile, User, UserLanguagePreference
from plane.license.models import InstanceConfiguration

URL = "/api/orca/users/me/language-preference/"


@pytest.fixture
def set_default_language(db):
    """Write the DEFAULT_LANGUAGE instance configuration, cache cleared.

    Goes through ``save()`` rather than ``update()`` on purpose: the receiver
    under test is a ``post_save``, and god-mode writes this setting the same
    way.
    """

    def _set(value):
        InstanceConfiguration.objects.update_or_create(
            key="DEFAULT_LANGUAGE",
            defaults={"value": value, "category": "LANGUAGE", "is_encrypted": False},
        )
        cache.clear()

    return _set


@pytest.fixture
def make_profile(db):
    """A profile, with the seeding receiver's effect out of the way.

    Creating a profile while a default is configured runs the seeding receiver,
    which is a different behavior with its own tests. Tests here want a profile
    sitting on a known language, so the language is written afterwards with
    ``update()`` — which skips signals and therefore records no choice.
    """
    counter = {"n": 0}

    def _make(language=FALLBACK_LANGUAGE):
        counter["n"] += 1
        email = f"person{counter['n']}@plane.so"
        user = User.objects.create(email=email, username=email.split("@")[0])
        profile = Profile.objects.create(user=user)
        Profile.objects.filter(pk=profile.pk).update(language=language)
        profile.refresh_from_db()
        return profile

    return _make


def choose(profile, language):
    """Pick a language the way the preferences screen does: save the profile."""
    profile.language = language
    profile.save()
    profile.refresh_from_db()
    return profile


@pytest.mark.django_db
class TestWhoFollowsTheOrganization:
    """``follower_profiles`` — the rule every mover shares."""

    def test_a_profile_on_the_current_default_follows(self, make_profile):
        profile = make_profile()
        assert profile in follower_profiles(FALLBACK_LANGUAGE)

    def test_a_profile_on_another_language_does_not(self, make_profile):
        # No row records the choice — this profile predates the sidecar — but
        # somebody moved it off the birth language, and only a person does that.
        profile = make_profile(language="ja")
        assert profile not in follower_profiles(FALLBACK_LANGUAGE)

    def test_a_recorded_choice_wins_over_the_language(self, make_profile):
        profile = make_profile()
        UserLanguagePreference.objects.create(user=profile.user, follows_organization_default=False)
        assert profile not in follower_profiles(FALLBACK_LANGUAGE)

    def test_handing_the_choice_back_wins_too(self, make_profile):
        # Reading Japanese while following the organization is a real state:
        # they followed, the default was Japanese, and it has since changed.
        profile = make_profile(language="ja")
        UserLanguagePreference.objects.create(user=profile.user, follows_organization_default=True)
        assert profile in follower_profiles(FALLBACK_LANGUAGE)

    def test_the_rule_reads_against_the_language_it_is_given(self, make_profile):
        profile = make_profile(language="pt-BR")
        assert profile in follower_profiles("pt-BR")
        assert profile not in follower_profiles(FALLBACK_LANGUAGE)


@pytest.mark.django_db
class TestChoosingALanguageIsRecorded:
    """``record_explicit_language_choice`` — the Profile receiver."""

    def test_changing_the_language_records_the_choice(self, make_profile):
        profile = make_profile()
        choose(profile, "de")
        preference = UserLanguagePreference.objects.get(user=profile.user)
        assert preference.follows_organization_default is False

    def test_choosing_english_is_still_choosing(self, make_profile):
        # The whole point of the sidecar: this profile looks identical to one
        # nobody has touched, and must never be moved again.
        profile = make_profile(language="de")
        choose(profile, FALLBACK_LANGUAGE)
        assert UserLanguagePreference.objects.filter(user=profile.user, follows_organization_default=False).exists()

    def test_creating_a_profile_records_nothing(self, make_profile):
        profile = make_profile()
        assert not UserLanguagePreference.objects.filter(user=profile.user).exists()

    def test_being_seeded_with_the_default_records_nothing(self, set_default_language):
        # Birth in the organization's language is the organization's doing.
        set_default_language("pt-BR")
        user = User.objects.create(email="born@plane.so", username="born")
        profile = Profile.objects.create(user=user)
        profile.refresh_from_db()
        assert profile.language == "pt-BR"
        assert not UserLanguagePreference.objects.filter(user=user).exists()

    def test_saving_another_field_records_nothing(self, make_profile):
        profile = make_profile()
        profile.is_tour_completed = True
        profile.save()
        assert not UserLanguagePreference.objects.filter(user=profile.user).exists()

    def test_saving_the_same_language_records_nothing(self, make_profile):
        profile = make_profile()
        choose(profile, FALLBACK_LANGUAGE)
        assert not UserLanguagePreference.objects.filter(user=profile.user).exists()

    def test_changing_twice_keeps_one_row(self, make_profile):
        profile = make_profile()
        choose(profile, "de")
        choose(profile, "fr")
        assert UserLanguagePreference.objects.filter(user=profile.user).count() == 1

    def test_choosing_again_after_handing_the_choice_back(self, make_profile):
        profile = make_profile()
        UserLanguagePreference.objects.create(user=profile.user, follows_organization_default=True)
        choose(profile, "ko")
        preference = UserLanguagePreference.objects.get(user=profile.user)
        assert preference.follows_organization_default is False


@pytest.mark.django_db
class TestChangingTheOrganizationDefault:
    """``apply_default_language_to_followers`` — the InstanceConfiguration receiver."""

    def test_followers_move(self, make_profile, set_default_language):
        profile = make_profile()
        set_default_language("pt-BR")
        profile.refresh_from_db()
        assert profile.language == "pt-BR"

    def test_a_chosen_language_is_left_alone(self, make_profile, set_default_language):
        profile = make_profile()
        choose(profile, "ja")
        set_default_language("pt-BR")
        profile.refresh_from_db()
        assert profile.language == "ja"

    def test_a_pre_existing_language_is_left_alone(self, make_profile, set_default_language):
        # Nothing recorded this choice, because the person made it before the
        # sidecar existed. The language itself is the record.
        profile = make_profile(language="ja")
        set_default_language("pt-BR")
        profile.refresh_from_db()
        assert profile.language == "ja"

    def test_somebody_who_handed_the_choice_back_moves(self, make_profile, set_default_language):
        profile = make_profile(language="ja")
        UserLanguagePreference.objects.create(user=profile.user, follows_organization_default=True)
        set_default_language("pt-BR")
        profile.refresh_from_db()
        assert profile.language == "pt-BR"

    def test_a_second_change_moves_the_same_people(self, make_profile, set_default_language):
        follower = make_profile()
        chooser = make_profile()
        choose(chooser, "ja")

        set_default_language("pt-BR")
        set_default_language("de")

        follower.refresh_from_db()
        chooser.refresh_from_db()
        assert follower.language == "de"
        assert chooser.language == "ja"

    def test_moving_followers_does_not_look_like_a_choice(self, make_profile, set_default_language):
        profile = make_profile()
        set_default_language("pt-BR")
        assert not UserLanguagePreference.objects.filter(user=profile.user).exists()

    def test_an_unusable_value_moves_people_to_english(self, make_profile, set_default_language):
        profile = make_profile()
        set_default_language("pt-BR")
        set_default_language("klingon")
        profile.refresh_from_db()
        assert profile.language == FALLBACK_LANGUAGE

    def test_setting_the_same_value_again_changes_nothing(self, make_profile, set_default_language):
        set_default_language("pt-BR")
        profile = make_profile(language="ja")
        set_default_language("pt-BR")
        profile.refresh_from_db()
        assert profile.language == "ja"

    def test_another_setting_does_not_touch_languages(self, make_profile):
        profile = make_profile()
        InstanceConfiguration.objects.update_or_create(
            key="EMAIL_HOST",
            defaults={"value": "smtp.example.com", "category": "SMTP", "is_encrypted": False},
        )
        profile.refresh_from_db()
        assert profile.language == FALLBACK_LANGUAGE


@pytest.mark.django_db
class TestLanguagePreferenceEndpoint:
    """``/api/orca/users/me/language-preference/``"""

    @pytest.fixture
    def person(self, make_profile):
        return make_profile()

    @pytest.fixture
    def client_for(self):
        def _client(user):
            client = APIClient()
            client.force_authenticate(user=user)
            return client

        return _client

    def test_signing_in_is_required(self):
        assert APIClient().get(URL).status_code == 401

    def test_no_row_reads_as_following(self, person, client_for, set_default_language):
        set_default_language("pt-BR")
        response = client_for(person.user).get(URL)
        assert response.status_code == 200
        assert response.data["follows_organization_default"] is True
        assert response.data["organization_default_language"] == "pt-BR"

    def test_a_choice_reads_as_not_following(self, person, client_for):
        choose(person, "de")
        response = client_for(person.user).get(URL)
        assert response.data["follows_organization_default"] is False

    def test_following_again_moves_the_person_onto_the_default(self, person, client_for, set_default_language):
        set_default_language("pt-BR")
        choose(person, "de")

        response = client_for(person.user).patch(URL, {"follows_organization_default": True}, format="json")

        assert response.status_code == 200
        assert response.data["follows_organization_default"] is True
        person.refresh_from_db()
        assert person.language == "pt-BR"

    def test_following_again_survives_a_later_change_of_default(self, person, client_for, set_default_language):
        set_default_language("pt-BR")
        choose(person, "de")
        client_for(person.user).patch(URL, {"follows_organization_default": True}, format="json")

        set_default_language("ja")

        person.refresh_from_db()
        assert person.language == "ja"

    def test_stopping_following_keeps_the_language(self, person, client_for, set_default_language):
        set_default_language("pt-BR")
        person.refresh_from_db()
        assert person.language == "pt-BR"

        response = client_for(person.user).patch(URL, {"follows_organization_default": False}, format="json")

        assert response.data["follows_organization_default"] is False
        person.refresh_from_db()
        assert person.language == "pt-BR"

    def test_the_flag_must_be_a_boolean(self, person, client_for):
        response = client_for(person.user).patch(URL, {"follows_organization_default": "yes"}, format="json")
        assert response.status_code == 400

    def test_the_flag_is_required(self, person, client_for):
        response = client_for(person.user).patch(URL, {}, format="json")
        assert response.status_code == 400

    def test_patching_twice_is_idempotent(self, person, client_for, set_default_language):
        set_default_language("pt-BR")
        client = client_for(person.user)
        client.patch(URL, {"follows_organization_default": True}, format="json")
        client.patch(URL, {"follows_organization_default": True}, format="json")
        assert UserLanguagePreference.objects.filter(user=person.user).count() == 1

    def test_one_person_cannot_see_another(self, make_profile, client_for):
        mine = make_profile()
        theirs = make_profile()
        choose(theirs, "de")

        response = client_for(mine.user).get(URL)

        assert response.data["follows_organization_default"] is True
