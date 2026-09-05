# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Migrated accounts must not ship with a password.

``tools/migration/create_users.py`` used to set the same literal password on
every account it created, with no forced rotation: one string, published in
the repository and in the tool's README, opened every migrated account in the
deployment. Accounts are now created with no usable password and the
``is_password_autoset`` flag the OAuth providers set, so the only ways in are
the identity provider and the magic link.

The script lives outside ``apps/api`` (it is executed inside the API container
by the operator, not imported by the application), so it is loaded here by
path. The Docker test stack mounts only ``apps/api``; there the file is
absent and these tests skip. The CI job runs from a full checkout, where they
run for real.
"""

import importlib.util
from pathlib import Path

import pytest

from plane.db.models import User, WorkspaceMember

SCRIPT_PATH = Path(__file__).resolve().parents[6] / "tools" / "migration" / "create_users.py"
SCRIPT_AVAILABLE = SCRIPT_PATH.is_file()

needs_script = pytest.mark.skipif(
    not SCRIPT_AVAILABLE,
    reason=f"{SCRIPT_PATH} is not mounted in this environment",
)


def load_script():
    """
    @description Import ``create_users.py`` by path, without putting the
    migration tools on ``sys.path`` for the rest of the suite.
    @returns The imported module.
    """
    spec = importlib.util.spec_from_file_location("orca_migration_create_users", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
@needs_script
class TestCreateUserFromPayload:
    def test_a_created_account_has_no_usable_password(self, db):
        script = load_script()
        member = script.MigratedMember("migrated@plane.so", "Mi", "Grated", 15)

        user, created, _ = script.create_user_from_payload(member)

        assert created is True
        assert user.has_usable_password() is False
        assert user.is_password_autoset is True
        # Re-read: the flag has to be persisted, not just set on the instance.
        stored = User.objects.get(email="migrated@plane.so")
        assert stored.has_usable_password() is False
        assert stored.is_password_autoset is True

    def test_an_existing_account_keeps_its_own_password(self, db, plain_user):
        script = load_script()
        member = script.MigratedMember(plain_user.email, "Ignored", "Ignored", 15)

        user, created, _ = script.create_user_from_payload(member)

        assert created is False
        assert user.pk == plain_user.pk
        # The owner may have chosen this password; a migration re-run must not
        # lock them out of an account that already works.
        assert user.check_password("orca@123") is True
        assert user.first_name == plain_user.first_name

    def test_the_member_is_attached_to_the_workspace_with_the_mapped_role(self, db, workspace_with_members):
        script = load_script()
        member = script.MigratedMember("attached@plane.so", "At", "Tached", 20)

        user, _, membership_created = script.create_user_from_payload(member, workspace_with_members)

        assert membership_created is True
        membership = WorkspaceMember.objects.get(workspace=workspace_with_members, member=user)
        assert membership.role == 20

    def test_running_it_twice_creates_nothing_the_second_time(self, db, workspace_with_members):
        script = load_script()
        member = script.MigratedMember("twice@plane.so", "Tw", "Ice", 15)

        script.create_user_from_payload(member, workspace_with_members)
        user, created, membership_created = script.create_user_from_payload(member, workspace_with_members)

        assert created is False
        assert membership_created is False
        assert User.objects.filter(email="twice@plane.so").count() == 1
        assert WorkspaceMember.objects.filter(workspace=workspace_with_members, member=user).count() == 1


@pytest.mark.unit
@needs_script
def test_no_literal_password_survives_in_the_script():
    """
    @description Guard the regression directly in the source: a helpful future
    edit that reintroduces a shared default password has to fail here, not in
    a deployment.
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "TemporaryOrca" not in source
    assert "DEFAULT_PASSWORD" not in source
    assert "set_password(" not in source
