# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Pre-create the local accounts of members migrated from another Plane install.

Accounts are created **without a usable password**: first access goes through
the identity provider (Entra ID) or a magic link, exactly like accounts the
OAuth providers create. A shared, hard-coded password for every migrated user
would be a standing credential in the deployment — the migration is a bulk
account creation, not a way to hand out passwords.
"""

import os
import sys
from typing import NamedTuple, Optional

import django
import requests


def bootstrap_django() -> None:
    """
    @description Configure Django when this file is executed as a script
    inside the API container. Importing the module from the test suite must
    not re-run the bootstrap: pytest-django has already configured settings,
    and pointing ``DJANGO_SETTINGS_MODULE`` at production there would swap the
    database under the tests.
    @returns None
    """
    from django.apps import apps as django_apps

    if django_apps.ready:
        return

    sys.path.append("/app")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.production")
    django.setup()


bootstrap_django()

from plane.db.models import User, Workspace, WorkspaceMember  # noqa: E402

# CONFIGURATION
OLD_PLANE_URL = os.getenv("MIGRATION_OLD_PLANE_URL", "").rstrip("/")
OLD_API_TOKEN = os.getenv("MIGRATION_OLD_API_TOKEN", "")
OLD_WORKSPACE_SLUG = os.getenv("MIGRATION_OLD_WORKSPACE_SLUG", "")

NEW_WORKSPACE_SLUG = os.getenv("MIGRATION_NEW_WORKSPACE_SLUG", "")

# Requests to the old instance hang forever without this; a migration that
# stalls in the middle is harder to reason about than one that fails.
REQUEST_TIMEOUT = (5, 30)

old_headers = {
    "Authorization": f"Bearer {OLD_API_TOKEN}",
    "x-api-key": OLD_API_TOKEN,
    "Content-Type": "application/json",
}


class MigratedMember(NamedTuple):
    """
    @description One member read from the source instance.
    @param email Login address; the identity the accounts are matched on.
    @param first_name Given name, empty string when the source has none.
    @param last_name Family name, empty string when the source has none.
    @param role Workspace role code to grant on the target workspace.
    """

    email: str
    first_name: str
    last_name: str
    role: int


def fetch_users_from_old_plane():
    print(f"[+] Fetching workspace members from old Plane ({OLD_PLANE_URL})...")
    try:
        res = requests.get(
            f"{OLD_PLANE_URL}/api/v1/workspaces/{OLD_WORKSPACE_SLUG}/members/",
            headers=old_headers,
            timeout=REQUEST_TIMEOUT,
        )
        res.raise_for_status()
        members = res.json()

        users_list = []
        for m in members:
            email = m.get("member", {}).get("email") or m.get("email")
            first_name = m.get("member", {}).get("first_name", "") or m.get("first_name", "")
            last_name = m.get("member", {}).get("last_name", "") or m.get("last_name", "")
            role = m.get("role", 15)
            if email:
                users_list.append(MigratedMember(email, first_name, last_name, role))

        return users_list
    except Exception as e:
        print(f"[-] Failed to fetch users: {e}")
        return []


def create_user_from_payload(member: MigratedMember, workspace: Optional["Workspace"] = None):
    """
    @description Create (or find) the local account for one migrated member
    and, when a workspace is given, attach it with the mapped role.

    A newly created account gets no usable password and is flagged
    ``is_password_autoset``, the same flag the OAuth providers set. Nobody has
    authenticated at this point — unlike ``AuthAdapter``, which creates the
    account *during* a verified login and can therefore afford a random
    password — so the account is left with no password at all and can only be
    entered through the identity provider or a magic link. An account that
    already exists is never touched: its owner may have set a password of
    their own, and the migration has no business resetting it.

    @param member Member read from the source instance.
    @param workspace Target workspace, or ``None`` to only create the account.
    @returns tuple(user, user_created, membership_created); the third element
        is ``False`` when no workspace was given.
    """
    user, created = User.objects.get_or_create(
        email=member.email,
        defaults={
            "first_name": member.first_name,
            "last_name": member.last_name,
            "username": member.email.split("@")[0],
            "is_active": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.is_password_autoset = True
        user.save(update_fields=["password", "is_password_autoset"])

    membership_created = False
    if workspace is not None:
        _, membership_created = WorkspaceMember.objects.get_or_create(
            workspace=workspace, member=user, defaults={"role": member.role}
        )

    return user, created, membership_created


def create_users():
    if not OLD_PLANE_URL or not OLD_API_TOKEN or not NEW_WORKSPACE_SLUG:
        print(
            "[-] Error: Make sure MIGRATION_OLD_PLANE_URL, MIGRATION_OLD_API_TOKEN, "
            "and MIGRATION_NEW_WORKSPACE_SLUG are set in .env"
        )
        return

    users_to_create = fetch_users_from_old_plane()
    if not users_to_create:
        print("[-] No users found to pre-create.")
        return

    try:
        workspace = Workspace.objects.get(slug=NEW_WORKSPACE_SLUG)
    except Workspace.DoesNotExist:
        print(f"[-] Workspace with slug '{NEW_WORKSPACE_SLUG}' does not exist on target database.")
        return

    for member in users_to_create:
        user, created, membership_created = create_user_from_payload(member, workspace)

        if created:
            print(f"[+] Created user account: {member.email} (no password; first access via Entra ID or magic link)")
        else:
            print(f"[-] User account {member.email} already exists.")

        if membership_created:
            print(f"    [+] Added {member.email} to workspace '{NEW_WORKSPACE_SLUG}' with role {member.role}.")
        else:
            print(f"    [-] {member.email} is already a member of workspace.")


if __name__ == "__main__":
    create_users()
