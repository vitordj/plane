# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Tests for the account seeding used by the one-shot migration scripts.

The scripts run by hand, once, inside a production container, and the accounts
they create outlive the migration. The property worth pinning is that none of
them ends up with a password somebody could know: an earlier revision seeded
every account with the same hard-coded string, committed to this repository.
"""

import pytest

from plane.utils.orca_migration_accounts import create_user_from_payload


@pytest.mark.unit
@pytest.mark.django_db
def test_seeded_account_has_no_usable_password():
    """A migrated account cannot be signed into with a password."""
    user, created = create_user_from_payload(
        {"email": "migrated.person@example.com", "first_name": "Migrated", "last_name": "Person"}
    )

    assert created is True
    assert user.has_usable_password() is False
    assert user.is_password_autoset is True
    user.refresh_from_db()
    assert user.has_usable_password() is False


@pytest.mark.unit
@pytest.mark.django_db
def test_seeding_twice_reuses_the_account():
    """Re-running the script neither duplicates the person nor re-credentials them."""
    first, created_first = create_user_from_payload({"email": "repeat@example.com"})
    second, created_second = create_user_from_payload({"email": "repeat@example.com"})

    assert created_first is True
    assert created_second is False
    assert first.pk == second.pk
    assert second.has_usable_password() is False


@pytest.mark.unit
@pytest.mark.django_db
def test_names_default_to_empty_strings():
    """A member with no name in the source does not become the string 'None'."""
    user, _ = create_user_from_payload({"email": "nameless@example.com", "first_name": None})

    assert user.first_name == ""
    assert user.last_name == ""
