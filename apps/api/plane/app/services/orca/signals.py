# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Signal receivers for the Orca layer.

Registered from ``plane.app.apps.AppApiConfig.ready()``. Everything here is a
sidecar behavior attached to a core model from the outside, per FORK.md — no
core model gains a column and no upstream call site is patched.
"""

# Django imports
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

# Module imports
from plane.db.models import Profile, UserLanguagePreference
from plane.license.models import InstanceConfiguration

from .language import DEFAULT_LANGUAGE_KEY, follower_profiles, get_default_language, normalize_language

# Set on a Profile instance while this module is the one changing its language,
# so the receiver that records a personal choice can tell its own writes apart
# from somebody using the language picker.
_SEEDING = "_orca_seeding_language"
# Where pre_save stashes the language as it stands in the database, so post_save
# can see whether this save changed it.
_PREVIOUS = "_orca_previous_language"
# The same trick for the instance setting: post_save needs the default that is
# being replaced in order to tell who was following it.
_PREVIOUS_DEFAULT = "_orca_previous_default_language"


@receiver(post_save, sender=Profile)
def seed_profile_language(sender, instance, created, **kwargs):
    """Give a brand-new profile the instance's default interface language.

    @description ``Profile.language`` is born as ``"en"`` for everybody, which
        on a single-organization instance means every person has to go and
        change it once. This applies the instance default instead, at the one
        moment there is no user preference to overwrite: profile creation.

        A receiver rather than an edit to the four places that create profiles
        (the OAuth adapter, the magic-link view, the redirection backfill and
        the god-mode first admin) — all four are upstream files, and patching
        them would be four merge conflicts on every upstream sync for one
        behavior.

        Only a profile still holding the field's own default is touched. Code
        that creates a profile with an explicit language keeps it. At creation
        nobody has expressed a preference yet, so treating the model default as
        "unset" costs nothing; from here on the person's own choice is theirs
        and this receiver never fires for it again.
    @param created: Django's flag; updates are ignored entirely.
    @returns: None. Saves at most one field, and only when it would change.
    """
    if not created:
        return

    field_default = Profile._meta.get_field("language").get_default()
    if instance.language != field_default:
        return

    default_language = get_default_language()
    if default_language == instance.language:
        return

    instance.language = default_language
    # The flag keeps record_explicit_language_choice from reading this write as
    # the person picking a language. update_fields keeps it to a single-column
    # UPDATE, and the guards above make the re-entrant post_save a no-op.
    setattr(instance, _SEEDING, True)
    try:
        instance.save(update_fields=["language"])
    finally:
        delattr(instance, _SEEDING)


@receiver(pre_save, sender=Profile)
def stash_previous_language(sender, instance, **kwargs):
    """Remember the stored language so post_save can see whether it changed.

    @description Django hands post_save the saved row, not the one it replaced,
        so the comparison has to be set up here. Reads one column, and only for
        a profile that already exists.
    """
    if instance._state.adding or instance.pk is None:
        setattr(instance, _PREVIOUS, None)
        return
    setattr(
        instance,
        _PREVIOUS,
        Profile.objects.filter(pk=instance.pk).values_list("language", flat=True).first(),
    )


@receiver(post_save, sender=Profile)
def record_explicit_language_choice(sender, instance, created, **kwargs):
    """Note that this person's language is theirs, not the organization's.

    @description ``Profile.language`` is one column holding a code, so it cannot
        distinguish somebody who chose English from somebody who never opened
        the setting. Both read ``en``, and that ambiguity is what stopped the
        instance default from ever reaching existing members safely.

        A change to the column that this module did not make is somebody using
        the language picker, so it writes the sidecar row that says so. From
        then on nothing moves their language but them: not the rollout command,
        not a later change to the organization's default.

        Deliberately watches the column rather than the profile endpoint. The
        endpoint is upstream, and the picker is not the only thing that can set
        a language — an IdP sync or a future onboarding step would be missed.
    @returns: None. Writes at most one small row, once per person.
    """
    if created or getattr(instance, _SEEDING, False):
        return

    previous = getattr(instance, _PREVIOUS, None)
    if previous is None or previous == instance.language:
        return

    UserLanguagePreference.objects.update_or_create(
        user_id=instance.user_id,
        defaults={"follows_organization_default": False},
    )


@receiver(pre_save, sender=InstanceConfiguration)
def stash_previous_default_language(sender, instance, **kwargs):
    """Remember the organization's language before this save replaces it.

    @description The people who need to move when the default changes are the
        ones sitting on the *old* default, and post_save only sees the new one.
        Reads a single column, and only for the one key this module cares
        about.
    """
    if instance.key != DEFAULT_LANGUAGE_KEY:
        return

    stored = None
    if not instance._state.adding and instance.pk is not None:
        stored = InstanceConfiguration.objects.filter(pk=instance.pk).values_list("value", flat=True).first()
    # No previous row means the organization never had a default, so the people
    # following it are the ones still on the language every profile is born in.
    setattr(
        instance,
        _PREVIOUS_DEFAULT,
        normalize_language(stored) if stored is not None else Profile._meta.get_field("language").get_default(),
    )


@receiver(post_save, sender=InstanceConfiguration)
def apply_default_language_to_followers(sender, instance, **kwargs):
    """Move everyone who has not chosen onto the organization's new language.

    @description Without this the default only reaches profiles created after
        it was set, which makes changing it in god-mode look broken: the
        sign-in screen switches and everybody's workspace stays as it was.

        Only people who follow the organization move — see
        ``follower_profiles`` for what that means and why a language somebody
        set for themselves is never touched, sidecar row or not.

        One UPDATE, and ``queryset.update`` skips signals, so it cannot be
        mistaken for the choices it is careful not to touch.
    @returns: None.
    """
    if instance.key != DEFAULT_LANGUAGE_KEY:
        return

    language = normalize_language(instance.value)
    previous = getattr(instance, _PREVIOUS_DEFAULT, None)
    if previous is None or previous == language:
        return

    follower_profiles(previous).exclude(language=language).update(language=language, updated_at=timezone.now())
