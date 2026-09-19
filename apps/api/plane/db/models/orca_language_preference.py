# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models

# Module imports
from .base import BaseModel


class UserLanguagePreference(BaseModel):
    """Whether a person's interface language is theirs or the organization's.

    ``Profile.language`` holds a language code and nothing else, so it cannot
    tell somebody who deliberately picked English from somebody who has never
    opened the setting — both rows read ``en``. That ambiguity is why the
    instance default could only ever apply to profiles created after it was
    set: reaching back would have overwritten real choices.

    This row is the missing bit. Absent or ``follows_organization_default``, the
    person's language tracks the instance's; false, they chose it and nothing
    changes it but them. A sidecar table rather than a column on ``Profile``,
    per FORK.md — the core model stays exactly as upstream has it.

    Absence means "follows", so the table starts empty and stays small: only
    people who have actually chosen get a row.
    """

    user = models.OneToOneField(
        "db.User",
        on_delete=models.CASCADE,
        related_name="orca_language_preference",
    )
    follows_organization_default = models.BooleanField(default=True)

    class Meta:
        verbose_name = "User Language Preference"
        verbose_name_plural = "User Language Preferences"
        db_table = "orca_user_language_preferences"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user_id} - follows_default={self.follows_organization_default}"
