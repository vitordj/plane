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
    get_default_language,
    normalize_language,
)
from plane.db.models import Profile


class Command(BaseCommand):
    help = (
        "Move profiles still on the stock English default onto the instance's "
        "default language. Previews by default; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--language",
            type=str,
            default=None,
            help=(
                "Language code to apply. Defaults to the instance's "
                "DEFAULT_LANGUAGE configuration."
            ),
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
                    f"'{requested}' is not a supported language. "
                    f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
                )

        # The stock value every profile is born with. Profiles still holding it
        # are the ones nobody has expressed a preference for — as far as the
        # schema can tell. See the caveat below.
        stock_default = Profile._meta.get_field("language").get_default()

        if language == stock_default:
            self.stdout.write(
                self.style.WARNING(
                    f"The default language is already '{language}'; nothing to change."
                )
            )
            return

        candidates = Profile.objects.filter(language=stock_default)
        count = candidates.count()

        if not count:
            self.stdout.write(self.style.SUCCESS(f"No profiles are still on '{stock_default}'."))
            return

        if not apply_changes:
            self.stdout.write(
                f"Would move {count} profile(s) from '{stock_default}' to '{language}'."
            )
            self.stdout.write("Re-run with --apply to write these changes.")
            return

        # queryset.update() on purpose: it skips the post_save receiver (which
        # only acts on creation anyway) and writes one statement instead of N.
        # updated_at is auto_now, which update() bypasses, so it is set here
        # rather than left stale.
        updated = candidates.update(language=language, updated_at=timezone.now())
        self.stdout.write(
            self.style.SUCCESS(f"Moved {updated} profile(s) from '{stock_default}' to '{language}'.")
        )
        self.stdout.write(
            self.style.WARNING(
                "Caveat: Profile.language cannot distinguish somebody who chose "
                f"'{stock_default}' from somebody who never opened the setting. "
                "Anyone in the first group has just been switched and will need "
                "to set their language again."
            )
        )
