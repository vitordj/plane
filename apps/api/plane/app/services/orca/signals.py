# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Signal receivers for the Orca layer.

Registered from ``plane.app.apps.AppApiConfig.ready()``. Everything here is a
sidecar behavior attached to a core model from the outside, per FORK.md — no
core model gains a column and no upstream call site is patched.
"""

# Django imports
from django.db.models.signals import post_save
from django.dispatch import receiver

# Module imports
from plane.db.models import Profile

from .language import get_default_language


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
    # update_fields keeps this to a single-column UPDATE and, because
    # post_save re-fires, the guards above make the second pass a no-op.
    instance.save(update_fields=["language"])
