# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Pre-create the accounts of a source install's workspace members on the target.

Issues, module leads and project roles can only be mapped to accounts that
already exist, so this runs before ``migrate_data.py``.

The accounts are created **without a password**. A migrated account is a
placeholder for a person who has not signed in yet; giving every one of them
the same known password would hand anyone who reads this file — or the log of
whoever ran it — a way into all of them. People sign in through Entra ID or a
magic link, the same way accounts created by the OAuth providers do (see
``is_password_autoset`` in ``plane/authentication/adapter/base.py``).

Django is only bootstrapped when the file is run as a script, so the functions
below can be imported by the test suite, which configures Django itself.
"""

import os
import sys

import requests

# CONFIGURATION
OLD_PLANE_URL = os.getenv("MIGRATION_OLD_PLANE_URL", "").rstrip("/")
OLD_API_TOKEN = os.getenv("MIGRATION_OLD_API_TOKEN", "")
OLD_WORKSPACE_SLUG = os.getenv("MIGRATION_OLD_WORKSPACE_SLUG", "")

NEW_WORKSPACE_SLUG = os.getenv("MIGRATION_NEW_WORKSPACE_SLUG", "")

DEFAULT_ROLE = 15

old_headers = {
    "Authorization": f"Bearer {OLD_API_TOKEN}",
    "x-api-key": OLD_API_TOKEN,
    "Content-Type": "application/json",
}


def bootstrap_django():
    """
    @description Configure Django so the ORM can be used from this script.

    Only called from ``__main__``: the script runs inside the API container,
    where the code lives at ``/app``, but the test suite imports this module
    with Django already configured against the test settings.
    """
    import django

    sys.path.append("/app")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.production")
    django.setup()


def fetch_users_from_old_plane():
    """
    @description Read the workspace members of the source install over its API.

    @returns list of payload dicts with "email", "first_name", "last_name" and
        "role"; empty when the call fails or nothing is readable.
    """
    print(f"[+] Fetching workspace members from old Plane ({OLD_PLANE_URL})...")
    try:
        res = requests.get(
            f"{OLD_PLANE_URL}/api/v1/workspaces/{OLD_WORKSPACE_SLUG}/members/",
            headers=old_headers,
            timeout=(5, 30),
        )
        res.raise_for_status()
        members = res.json()

        users_list = []
        for m in members:
            email = m.get("member", {}).get("email") or m.get("email")
            first_name = m.get("member", {}).get("first_name", "") or m.get("first_name", "")
            last_name = m.get("member", {}).get("last_name", "") or m.get("last_name", "")
            role = m.get("role", DEFAULT_ROLE)
            if email:
                users_list.append(
                    {
                        "email": email,
                        "first_name": first_name,
                        "last_name": last_name,
                        "role": role,
                    }
                )

        return users_list
    except Exception as e:
        print(f"[-] Failed to fetch users: {e}")
        return []


def create_user_from_payload(payload):
    """
    @description Create — or find — the account of one migrated member.

    A newly created account gets no usable password and is flagged
    ``is_password_autoset``, which is how Plane marks an account whose owner
    has yet to choose a credential: the person signs in through Entra ID or a
    magic link and, if the instance allows passwords at all, sets one then.
    An account that already exists is returned untouched — this must never
    reset the credential of someone who already signed in.

    @param payload: mapping with "email" and, optionally, "first_name" and
        "last_name".
    @returns tuple ``(user, created)``.
    """
    from plane.db.models import User

    email = payload["email"]
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "first_name": payload.get("first_name", "") or "",
            "last_name": payload.get("last_name", "") or "",
            "username": email.split("@")[0],
            "is_active": True,
        },
    )

    if created:
        user.set_unusable_password()
        user.is_password_autoset = True
        user.save(update_fields=["password", "is_password_autoset"])

    return user, created


def add_user_to_workspace(user, workspace, role=DEFAULT_ROLE):
    """
    @description Attach a user to the target workspace with the role they had
    on the source install, leaving an existing membership as it is.

    @param user: the User returned by :func:`create_user_from_payload`.
    @param workspace: the target Workspace.
    @param role: numeric Plane role from the source install.
    @returns True when the membership was created by this call.
    """
    from plane.db.models import WorkspaceMember

    _, member_created = WorkspaceMember.objects.get_or_create(workspace=workspace, member=user, defaults={"role": role})
    return member_created


def create_users():
    """
    @description Read the source workspace's members and materialize them,
    with their roles, on the target workspace.
    """
    from plane.db.models import Workspace

    if not OLD_PLANE_URL or not OLD_API_TOKEN or not NEW_WORKSPACE_SLUG:
        print(
            "[-] Error: Make sure MIGRATION_OLD_PLANE_URL, MIGRATION_OLD_API_TOKEN, and MIGRATION_NEW_WORKSPACE_SLUG are set in .env"
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

    for payload in users_to_create:
        email = payload["email"]
        user, created = create_user_from_payload(payload)
        if created:
            print(f"[+] Created user account: {email} (no password; first sign-in is Entra ID or magic link)")
        else:
            print(f"[-] User account {email} already exists.")

        if add_user_to_workspace(user, workspace, payload.get("role", DEFAULT_ROLE)):
            print(
                f"    [+] Added {email} to workspace '{NEW_WORKSPACE_SLUG}' with role {payload.get('role', DEFAULT_ROLE)}."
            )
        else:
            print(f"    [-] {email} is already a member of workspace.")


if __name__ == "__main__":
    bootstrap_django()
    create_users()
