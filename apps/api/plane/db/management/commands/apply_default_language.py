# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from typing import Any

# Django imports
from django.core.management import BaseCommand, CommandError
from django.utils import timezone

# Module imports
from plane.app.services.orca.language import (
    SUPPORTED_LANGUAGES,
    follower_profiles,
    get_default_language,
    normalize_language,
)
from plane.db.models import Profile


class Command(BaseCommand):
    help = (
        "Move everyone who follows the organization's language onto it. "
        "People who picked their own language are never touched. "
        "Previews by default; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--language",
            type=str,
            default=None,
            help=("Language code to apply. Defaults to the instance's DEFAULT_LANGUAGE configuration."),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this flag the command only previews them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        requested = options["language"]
        apply_changes = options["apply"]

        if requested is None:
            language = get_default_language()
        else:
            # An explicit --language is the operator's own words, so a code the
            # catalogue cannot render is an error rather than something to
            # quietly turn into English. get_default_language() normalizes
            # instead, because a bad *setting* must never break sign-in.
            language = normalize_language(requested)
            if language != requested:
                raise CommandError(
                    f"'{requested}' is not a supported language. Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
                )

        # Who counts as a follower: nobody who has picked a language of their
        # own. On a population that predates the sidecar that means nobody who
        # has moved off the language they would be reading in anyway — the one
        # every profile is born in, or the one the organization currently
        # declares. See follower_profiles for the full rule.
        followers = follower_profiles(
            Profile._meta.get_field("language").get_default(),
            get_default_language(),
        )
        candidates = followers.exclude(language=language)
        count = candidates.count()
        chosen = Profile.objects.count() - followers.count()

        if not count:
            self.stdout.write(self.style.SUCCESS(f"Everyone who follows the organization is already on '{language}'."))
            if chosen:
                self.stdout.write(f"{chosen} profile(s) have a language of their own and were not considered.")
            return

        if not apply_changes:
            self.stdout.write(f"Would move {count} profile(s) to '{language}'.")
            if chosen:
                self.stdout.write(f"{chosen} profile(s) picked their own language and would be left alone.")
            self.stdout.write("Re-run with --apply to write these changes.")
            return

        # queryset.update() on purpose: it skips the receiver that records a
        # personal choice — this write is the opposite of one — and writes a
        # single statement instead of N. updated_at is auto_now, which update()
        # bypasses, so it is set here rather than left stale.
        updated = candidates.update(language=language, updated_at=timezone.now())
        self.stdout.write(self.style.SUCCESS(f"Moved {updated} profile(s) to '{language}'."))
        if chosen:
            self.stdout.write(f"{chosen} profile(s) picked their own language and were left alone.")
