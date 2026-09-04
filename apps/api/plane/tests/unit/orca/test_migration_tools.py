# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
A migrated account must not be reachable with a password anyone can read.

``tools/migration/create_users.py`` used to give every account it created the
same hard-coded password, printed in the repository and in its README. Anyone
who could read either could sign in as any person migrated from the old
install, before that person had ever signed in themselves. The accounts now
carry no usable password: the first sign-in goes through Entra ID or a magic
link, exactly as it does for accounts created by the OAuth providers.

The script lives outside ``apps/api`` and bootstraps Django only under
``__main__``, so it is loaded here from its path.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from plane.db.models import User, Workspace, WorkspaceMember


def _load_create_users():
    """
    @description Import ``tools/migration/create_users.py`` as a module.

    @returns the loaded module, or ``None`` when the migration tools are not
        present — the docker-compose test stack mounts only ``apps/api``.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tools" / "migration" / "create_users.py"
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("orca_migration_create_users", candidate)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    return None


create_users_module = _load_create_users()

pytestmark = pytest.mark.skipif(
    create_users_module is None,
    reason="tools/migration is not mounted in this environment (docker-compose-test mounts apps/api only)",
)

ROLE_MEMBER = 15
ROLE_ADMIN = 20


@pytest.mark.unit
class TestCreateUserFromPayload:
    def test_new_account_has_no_usable_password(self, db):
        user, created = create_users_module.create_user_from_payload(
            {"email": "migrated@plane.so", "first_name": "Mi", "last_name": "Grated"}
        )

        assert created is True
        assert user.has_usable_password() is False
        assert user.is_password_autoset is True

    def test_new_account_keeps_the_name_and_email_of_the_source_install(self, db):
        user, _ = create_users_module.create_user_from_payload(
            {"email": "named@plane.so", "first_name": "Na", "last_name": "Med"}
        )

        assert user.email == "named@plane.so"
        assert user.first_name == "Na"
        assert user.last_name == "Med"
        assert user.username == "named"
        assert user.is_active is True

    def test_running_the_script_twice_does_not_reset_an_existing_credential(self, db):
        existing = User.objects.create(email="already@plane.so", username="already")
        existing.set_password("a-password-its-owner-chose")
        existing.save()

        user, created = create_users_module.create_user_from_payload({"email": "already@plane.so"})

        assert created is False
        assert user.pk == existing.pk
        user.refresh_from_db()
        assert user.check_password("a-password-its-owner-chose") is True

    def test_the_module_carries_no_password_constant(self):
        constants = {
            name: value
            for name, value in vars(create_users_module).items()
            if isinstance(value, str) and "PASSWORD" in name.upper()
        }
        assert constants == {}


@pytest.mark.unit
class TestAddUserToWorkspace:
    def test_membership_is_created_with_the_role_from_the_source_install(self, db, workspace_with_members):
        user, _ = create_users_module.create_user_from_payload({"email": "fresh@plane.so"})

        created = create_users_module.add_user_to_workspace(user, workspace_with_members, ROLE_ADMIN)

        assert created is True
        membership = WorkspaceMember.objects.get(workspace=workspace_with_members, member=user)
        assert membership.role == ROLE_ADMIN

    def test_an_existing_membership_keeps_its_role(self, db, workspace_with_members, plain_user):
        created = create_users_module.add_user_to_workspace(plain_user, workspace_with_members, ROLE_ADMIN)

        assert created is False
        membership = WorkspaceMember.objects.get(workspace=workspace_with_members, member=plain_user)
        assert membership.role == ROLE_MEMBER


@pytest.mark.unit
class TestWorkspaceLookupIsUnchanged:
    def test_the_target_workspace_is_addressed_by_slug(self, db, workspace_with_members):
        assert Workspace.objects.get(slug=workspace_with_members.slug) == workspace_with_members
