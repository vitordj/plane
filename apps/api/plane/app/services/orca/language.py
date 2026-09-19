# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Instance-wide default language for the interface (Orca sidecar).

Upstream Plane has exactly one place where an interface language is decided:
``Profile.language``, which every profile is born with as ``"en"``. On a
self-managed instance run by a single organization that is the wrong default
for everybody at once — each person has to find Preferences and change it
before the product speaks their language.

This module holds the instance's answer to "what language do we work in",
read from the ``DEFAULT_LANGUAGE`` instance configuration. It is deliberately
only a *default*: it seeds new profiles and dresses the screens shown before
anyone is signed in. A person who has chosen a language keeps it.

Nothing here writes to a core model or adds a column, per FORK.md — the value
lives in ``InstanceConfiguration`` alongside the other instance settings.
"""

# Python imports
import os

# Django imports
from django.db.models import Q

# Module imports
from plane.db.models import Profile
from plane.license.utils.instance_value import get_configuration_value

# The interface languages the catalogue actually ships. Mirrors
# SUPPORTED_LANGUAGES in packages/i18n/src/constants/language.ts, which is the
# source of truth — a language missing from the catalogue renders raw keys.
# test_default_language.py asserts this list matches both that file and the
# locale directories, so adding a locale on one side fails the build until the
# other side follows.
SUPPORTED_LANGUAGES = (
    "en",
    "fr",
    "es",
    "ja",
    "zh-CN",
    "zh-TW",
    "ru",
    "it",
    "cs",
    "sk",
    "de",
    "ua",
    "pl",
    "ko",
    "pt-BR",
    "id",
    "ro",
    "vi-VN",
    "tr-TR",
)

# What we fall back to whenever the configured value is absent or unusable.
# Same fallback as the frontend's FALLBACK_LANGUAGE, for the same reason: it is
# the one locale guaranteed to be complete, since it is the source catalogue.
FALLBACK_LANGUAGE = "en"

# The instance configuration key, and the environment variable of the same name
# that seeds it. Read through get_configuration_value like every other instance
# setting, so SKIP_ENV_VAR decides whether the database or the environment wins.
DEFAULT_LANGUAGE_KEY = "DEFAULT_LANGUAGE"


def normalize_language(value):
    """Coerce a stored or submitted language code to one we can actually render.

    @description Guards the boundary between "a string somebody typed into the
        instance configuration" and "a locale the catalogue has". Matching is
        exact and case-sensitive on purpose: the codes are the catalogue's own
        directory names, and accepting ``pt-br`` here would hand i18next a code
        it silently ignores, which looks like the setting having no effect.
    @param value: The candidate language code, or ``None``.
    @returns: A supported language code, or ``FALLBACK_LANGUAGE``.
    """
    if isinstance(value, str) and value.strip() in SUPPORTED_LANGUAGES:
        return value.strip()
    return FALLBACK_LANGUAGE


def get_default_language():
    """The interface language this instance uses when nobody has chosen one.

    @description Reads the ``DEFAULT_LANGUAGE`` instance configuration and
        validates it. An unset, empty or unrecognized value yields English
        rather than raising: a typo in a settings field must not be able to
        stop sign-in or profile creation.
    @returns: A language code that is always in ``SUPPORTED_LANGUAGES``.
    """
    (configured,) = get_configuration_value(
        [
            {
                "key": DEFAULT_LANGUAGE_KEY,
                "default": os.environ.get(DEFAULT_LANGUAGE_KEY, FALLBACK_LANGUAGE),
            }
        ]
    )
    return normalize_language(configured)


def follower_profiles(*current_languages):
    """The profiles whose language is the organization's rather than their own.

    @description Somebody follows the organization when they have never picked
        a language of their own. ``Profile.language`` cannot say that by itself
        — English is both the field's birth value and a language people
        genuinely choose — so two signals are combined here.

        The sidecar row is definitive when it exists: ``False`` means this
        person chose, and nothing but them changes their language again;
        ``True`` means they handed the choice back and should move with the
        organization whatever they are reading right now.

        Where there is no row, the language stands in for one. A follower
        reads in whatever the organization last said — so a profile sitting on
        something else was moved there by the person, and that is a choice even
        though nothing recorded it as one. This is what makes the feature safe
        to switch on for an organization that is already running.
    @param current_languages: The language, or languages, a follower could
        currently be reading in. The receiver passes the default being
        replaced; the rollout command passes both the field's birth value and
        the configured default, since an instance that took its default from
        the environment never ran the receiver at all.
    @returns: An unevaluated ``Profile`` queryset. Nothing is written here.
    """
    return Profile.objects.filter(
        Q(language__in=current_languages) | Q(user__orca_language_preference__follows_organization_default=True)
    ).exclude(user__orca_language_preference__follows_organization_default=False)
