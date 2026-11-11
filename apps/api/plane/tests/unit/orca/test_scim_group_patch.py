# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Shape tests for ``PATCH /Groups/{id}``.

A SCIM group PATCH used to empty the group whenever it met a removal it could
not parse. These tests pin the opposite contract: only a bare ``remove`` of
``members`` with no ``value`` key empties a group, every shape the module does
not understand is a 400 with the standard error envelope, and a rejected
request changes nothing — including operations that were valid on their own.
"""

import pytest

from plane.db.models import OrganizationalDirectoryGroupMembership

from .conftest import scim_group_url

SCHEMA_PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
GROUP_URN = "urn:ietf:params:scim:schemas:core:2.0:Group:"

BOTH = {"a", "b"}


@pytest.fixture
def group_setup(workspace_with_members, directory_connection, bound_unit, make_identity, put_in_group):
    """A bound group holding identities ``a`` and ``b``, plus an outsider ``c``."""
    a = make_identity("plain@plane.so")
    b = make_identity("second@plane.so")
    c = make_identity("guest@plane.so")
    put_in_group(bound_unit, a)
    put_in_group(bound_unit, b)
    return {
        "unit": bound_unit,
        "ids": {"a": str(a.id), "b": str(b.id), "c": str(c.id)},
        "rows": {"a": a, "b": b, "c": c},
    }


def members_left(setup) -> set:
    """Names (``a``/``b``/``c``) of the identities still in the group."""
    by_id = {row.id: name for name, row in setup["rows"].items()}
    return {
        by_id[identity_id]
        for identity_id in OrganizationalDirectoryGroupMembership.objects.filter(
            organizational_unit=setup["unit"]
        ).values_list("identity_id", flat=True)
    }


def send(scim_client, workspace, setup, operations):
    """PATCH the group with the given operations."""
    return scim_client.patch(
        scim_group_url(workspace.slug, setup["unit"].id),
        {"schemas": [SCHEMA_PATCH_OP], "Operations": operations},
        format="json",
    )


# (case id, operation builder, members left)
ACCEPTED = [
    ("bare-remove-members-empties-the-group", lambda ids: [{"op": "remove", "path": "members"}], set()),
    (
        "bare-remove-with-urn-prefix-empties-the-group",
        lambda ids: [{"op": "remove", "path": GROUP_URN + "members"}],
        set(),
    ),
    ("bare-remove-path-is-case-insensitive", lambda ids: [{"op": "Remove", "path": "Members"}], set()),
    ("remove-with-an-empty-list-is-a-no-op", lambda ids: [{"op": "remove", "path": "members", "value": []}], BOTH),
    ("remove-with-a-null-value-is-a-no-op", lambda ids: [{"op": "remove", "path": "members", "value": None}], BOTH),
    (
        "remove-with-values-removes-only-those",
        lambda ids: [{"op": "remove", "path": "members", "value": [{"value": ids["a"]}]}],
        {"b"},
    ),
    (
        "filtered-remove-double-quotes",
        lambda ids: [{"op": "remove", "path": f'members[value eq "{ids["a"]}"]'}],
        {"b"},
    ),
    (
        "filtered-remove-single-quotes",
        lambda ids: [{"op": "remove", "path": f"members[value eq '{ids['a']}']"}],
        {"b"},
    ),
    (
        "filtered-remove-with-urn-prefix",
        lambda ids: [{"op": "remove", "path": f'{GROUP_URN}members[value eq "{ids["a"]}"]'}],
        {"b"},
    ),
    (
        "add-with-a-members-path-adds",
        lambda ids: [{"op": "add", "path": "members", "value": [{"value": ids["c"]}]}],
        BOTH | {"c"},
    ),
    (
        "replace-with-a-members-path-replaces",
        lambda ids: [{"op": "replace", "path": "members", "value": [{"value": ids["c"]}]}],
        {"c"},
    ),
    (
        "pathless-add-adds-instead-of-replacing",
        lambda ids: [{"op": "add", "value": {"members": [{"value": ids["c"]}]}}],
        BOTH | {"c"},
    ),
    (
        "pathless-replace-replaces",
        lambda ids: [{"op": "replace", "value": {"members": [{"value": ids["c"]}]}}],
        {"c"},
    ),
    # Attributes the service does not store never touch membership: ignored,
    # not refused, so a custom mapping in the directory cannot fail a cycle.
    ("unknown-path-is-ignored", lambda ids: [{"op": "replace", "path": "nickName", "value": "x"}], BOTH),
    ("unknown-path-with-urn-prefix-is-ignored", lambda ids: [{"op": "remove", "path": GROUP_URN + "nickName"}], BOTH),
    ("pathless-unknown-attribute-is-ignored", lambda ids: [{"op": "add", "value": {"nickName": "x"}}], BOTH),
]

# (case id, operation builder, expected scimType)
REJECTED = [
    (
        "filter-on-another-attribute",
        lambda ids: [{"op": "remove", "path": 'members[display eq "x"]'}],
        "invalidFilter",
    ),
    ("unparseable-filter", lambda ids: [{"op": "remove", "path": "members[value pr]"}], "invalidFilter"),
    (
        "unterminated-filter",
        lambda ids: [{"op": "remove", "path": f'members[value eq "{ids["a"]}"'}],
        "invalidFilter",
    ),
    (
        "mismatched-quotes-in-filter",
        lambda ids: [{"op": "remove", "path": f"members[value eq \"{ids['a']}']"}],
        "invalidFilter",
    ),
    (
        "filtered-path-with-add",
        lambda ids: [{"op": "add", "path": f'members[value eq "{ids["c"]}"]'}],
        "invalidPath",
    ),
    ("unsupported-op", lambda ids: [{"op": "move", "path": "members"}], "invalidSyntax"),
    ("missing-op", lambda ids: [{"path": "members", "value": []}], "invalidSyntax"),
    ("operation-that-is-not-an-object", lambda ids: ["remove"], "invalidSyntax"),
    ("sub-attribute-of-members", lambda ids: [{"op": "remove", "path": "members.value"}], "invalidPath"),
    ("pathless-remove", lambda ids: [{"op": "remove", "value": {"members": []}}], "invalidPath"),
    ("pathless-with-a-non-object-value", lambda ids: [{"op": "add", "value": "members"}], "invalidValue"),
    (
        "empty-display-name",
        lambda ids: [{"op": "replace", "path": "displayName", "value": "  "}],
        "invalidValue",
    ),
    ("removing-the-display-name", lambda ids: [{"op": "remove", "path": "displayName"}], "mutability"),
    (
        "a-valid-removal-followed-by-an-invalid-one-is-not-half-applied",
        lambda ids: [
            {"op": "remove", "path": f'members[value eq "{ids["a"]}"]'},
            {"op": "remove", "path": 'members[display eq "x"]'},
        ],
        "invalidFilter",
    ),
    (
        "an-invalid-operation-first-blocks-a-later-valid-one",
        lambda ids: [
            {"op": "bogus", "path": "members"},
            {"op": "add", "path": "members", "value": [{"value": ids["c"]}]},
        ],
        "invalidSyntax",
    ),
]


@pytest.mark.unit
class TestGroupPatchShapes:
    @pytest.mark.parametrize("build,expected", [case[1:] for case in ACCEPTED], ids=[case[0] for case in ACCEPTED])
    def test_understood_shapes_are_applied(self, scim_client, workspace_with_members, group_setup, build, expected):
        response = send(scim_client, workspace_with_members, group_setup, build(group_setup["ids"]))

        assert response.status_code == 200
        assert members_left(group_setup) == expected

    @pytest.mark.parametrize("build,scim_type", [case[1:] for case in REJECTED], ids=[case[0] for case in REJECTED])
    def test_unknown_shapes_are_refused_and_change_nothing(
        self, scim_client, workspace_with_members, group_setup, build, scim_type
    ):
        response = send(scim_client, workspace_with_members, group_setup, build(group_setup["ids"]))

        assert response.status_code == 400
        body = response.json()
        assert body["scimType"] == scim_type
        assert body["status"] == "400"
        assert members_left(group_setup) == BOTH

    def test_a_rejected_patch_does_not_rename_the_group(self, scim_client, workspace_with_members, group_setup):
        unit = group_setup["unit"]
        original = unit.name

        response = send(
            scim_client,
            workspace_with_members,
            group_setup,
            [
                {"op": "replace", "path": "displayName", "value": "Renamed"},
                {"op": "remove", "path": 'members[display eq "x"]'},
            ],
        )

        assert response.status_code == 400
        unit.refresh_from_db()
        assert unit.name == original

    def test_a_pathless_replace_can_also_rename_and_rebind(self, scim_client, workspace_with_members, group_setup):
        response = send(
            scim_client,
            workspace_with_members,
            group_setup,
            [{"op": "replace", "value": {"displayName": "Renamed", "externalId": "new-ext"}}],
        )

        assert response.status_code == 200
        unit = group_setup["unit"]
        unit.refresh_from_db()
        assert (unit.name, unit.external_id) == ("Renamed", "new-ext")
        assert members_left(group_setup) == BOTH
