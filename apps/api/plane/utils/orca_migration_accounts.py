# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Account creation for the one-shot data migration scripts.

The scripts under ``tools/migration`` are piped into a running container and
seed the accounts a migrated workspace refers to — assignees, leads, members.
Those accounts outlive the migration, so how they are created is a security
decision, not a convenience one, and it belongs in code the test suite can
reach rather than in a script that only exists on someone's laptop.

An account seeded here has no usable password. The person signs in through
Microsoft Entra ID or a magic link, both of which prove the address first,
which leaves no shared secret to distribute, leak or rotate.
"""

# Django imports
from plane.db.models import User


def create_user_from_payload(payload):
    """
    Create — or find — the account a migrated member will sign in to.

    @description The account exists so that issues, leads and roles can point
    at a real user. Nobody is meant to authenticate with a password chosen by
    the migration, so none is set: the account is created the way the Entra
    provider creates one, without a usable password and flagged as autoset.
    @param payload dict carrying ``email`` and, optionally, ``first_name`` and
        ``last_name``.
    @returns tuple of the ``User`` and whether this call created it.
    """
    email = payload["email"]
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "first_name": payload.get("first_name") or "",
            "last_name": payload.get("last_name") or "",
            "username": email.split("@")[0],
            "is_active": True,
            "is_password_autoset": True,
        },
    )
    if created:
        # Unusable password: the only ways in are Entra ID and the magic link,
        # and both verify the address before granting a session.
        user.set_unusable_password()
        user.is_password_autoset = True
        user.save(update_fields=["password", "is_password_autoset"])
    return user, created
