# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
SCIM 2.0 ``/Groups`` endpoints — where an Entra group becomes an area.

A SCIM ``Group`` maps onto an ``OrganizationalUnit`` bound to it through
``external_id``. Two bindings are possible and both are supported on purpose:

* the directory pushes a group Plane has never seen, and a unit is created for
  it (when the connection allows it);
* an admin creates the unit first — naming it, linking its projects, choosing
  the inherited roles — and the directory later claims it by pushing a group
  whose ``displayName`` matches. Access design stays a Plane decision; the
  directory only supplies who belongs.

What the directory never decides is which projects a unit grants or at which
role: those live on ``OrganizationalUnitProject`` and are set by an admin. A
group arriving from Entra therefore grants nothing until somebody links it to
projects, which is the intended safe default.
"""

# Python imports
import re
import uuid

# Django imports
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.services.orca import project_unit, resolve_identity
from plane.db.models import (
    DirectorySyncSource,
    OrganizationalDirectoryGroupMembership,
    OrganizationalDirectoryIdentity,
    OrganizationalUnit,
)

from .base import (
    SCIM_CONTENT_TYPE,
    SCIMBaseView,
    SCIMError,
    list_response,
    parse_filter,
    read_pagination,
    scim_base_url,
)
from .resources import group_location, group_resource

# SCIM attribute names Entra filters groups by, mapped to unit columns.
GROUP_FILTER_ATTRIBUTES = {
    "displayname": "name",
    "externalid": "external_id",
    "id": "id",
}


# Core Group schema URN a patch path may be prefixed with.
GROUP_SCHEMA_URN = "urn:ietf:params:scim:schemas:core:2.0:Group:"

SUPPORTED_PATCH_OPS = ("add", "remove", "replace")

# The one filter Entra sends: ``members[value eq "<id>"]``. Either quote style
# is accepted; nothing else is parsed, and anything else is refused.
MEMBER_FILTER_PATTERN = re.compile(
    r"^members\[\s*value\s+eq\s+(?P<quote>[\"'])(?P<value>[^\"']+)(?P=quote)\s*\]$", re.IGNORECASE
)

# Steps a validated operation is translated into (see ``parse_operation``).
STEP_MEMBERS_ADD = "members_add"
STEP_MEMBERS_REPLACE = "members_replace"
STEP_MEMBERS_REMOVE = "members_remove"
STEP_MEMBERS_CLEAR = "members_clear"
STEP_NAME = "name"
STEP_EXTERNAL_ID = "external_id"


def unique_unit_slug(workspace_id, name, exclude_id=None) -> str:
    """
    Derive a unit slug that is free inside the workspace.

    @description Directory group names collide far more often than
    hand-created unit names ("Finance" in two nested groups), and the slug is
    unique per workspace, so a bare ``slugify`` would fail the write. Suffixes
    are appended until the slug is free.

    @param workspace_id: The workspace the unit belongs to.
    @param name: Display name to derive the slug from.
    @param exclude_id: Unit to ignore when checking, when renaming one.
    @returns: A slug that no other unit in the workspace holds.
    """
    base = slugify(name)[:80] or "unit"
    candidate = base
    suffix = 2
    while True:
        clash = OrganizationalUnit.objects.filter(workspace_id=workspace_id, slug=candidate)
        if exclude_id:
            clash = clash.exclude(pk=exclude_id)
        if not clash.exists():
            return candidate
        candidate = f"{base}-{suffix}"[:100]
        suffix += 1


def members_of(unit):
    """
    Directory identities the mirror lists in a unit's group.

    @description Deliberately two queries rather than one join. Filtering
    across ``group_memberships__...`` builds the join from the *base* manager,
    which does not know about soft deletion, so a membership the directory
    removed — the row is kept, with ``deleted_at`` set — would come back in
    ``GET /Groups/{id}`` and Entra would read the person as still a member.
    Selecting the live memberships first makes the default manager's filter
    apply.
    """
    live_identity_ids = OrganizationalDirectoryGroupMembership.objects.filter(
        organizational_unit_id=unit.id
    ).values_list("identity_id", flat=True)
    return OrganizationalDirectoryIdentity.objects.filter(id__in=list(live_identity_ids))


class SCIMGroupMixin:
    """Lookup and member-set helpers shared by the two group endpoints."""

    def assert_binding_is_free(self, unit):
        """
        Refuse a binding another unit already holds.

        @description ``external_id`` is unique per workspace so that a group
        resolves to exactly one unit. Catching the clash here turns what would
        be an IntegrityError and a 500 into the ``uniqueness`` conflict RFC 7644
        defines — and 500s are what make Entra retry a doomed call forever.

        @param unit: The unit about to be saved.
        @raises SCIMError: 409 when another unit already mirrors that group.
        """
        if not unit.external_id:
            return
        clash = (
            OrganizationalUnit.objects.filter(workspace_id=self.workspace.id, external_id=unit.external_id)
            .exclude(pk=unit.pk)
            .exists()
        )
        if clash:
            raise SCIMError(
                f"Group {unit.external_id} is already provisioned",
                status.HTTP_409_CONFLICT,
                scim_type="uniqueness",
            )

    def get_unit(self, unit_id):
        """Fetch a directory-bound unit, or 404 in SCIM shape."""
        unit = OrganizationalUnit.objects.filter(workspace_id=self.workspace.id, pk=unit_id).first()
        if unit is None:
            raise SCIMError(f"Group {unit_id} not found", status.HTTP_404_NOT_FOUND)
        return unit

    def identities_for(self, values):
        """
        Resolve SCIM member ``value`` entries to mirrored identities.

        @description Entra addresses members by the id Plane returned when it
        created the user, so a value matching nothing means the two sides are
        out of step — Entra is replaying an id from before the mirror was
        reset. Such a value is skipped rather than failing the operation, so
        one stale reference cannot block the rest of a group change; Entra
        re-pushes the user on its next cycle and the member lands then.

        The directory's own ``externalId`` is accepted alongside Plane's id,
        because a group can legitimately be provisioned before its members are.

        @param values: The ``value`` fields of the SCIM member entries.
        @returns: The identities that exist in this workspace.
        """
        wanted = [str(value) for value in values if value]
        if not wanted:
            return []
        return list(
            OrganizationalDirectoryIdentity.objects.filter(workspace_id=self.workspace.id).filter(
                Q(pk__in=[value for value in wanted if _looks_like_uuid(value)]) | Q(external_id__in=wanted)
            )
        )

    def add_members(self, unit, values):
        """Record group membership for the given SCIM member values."""
        for identity in self.identities_for(values):
            OrganizationalDirectoryGroupMembership.objects.get_or_create(
                organizational_unit=unit,
                identity=identity,
                defaults={"workspace_id": unit.workspace_id},
            )
            # An identity pushed before its person joined the workspace links
            # itself here, the first time the group references it.
            resolve_identity(identity)

    def remove_members(self, unit, values):
        """Drop group membership for the given SCIM member values."""
        identities = self.identities_for(values)
        if identities:
            OrganizationalDirectoryGroupMembership.objects.filter(
                organizational_unit_id=unit.id,
                identity_id__in=[identity.id for identity in identities],
            ).delete()

    def replace_members(self, unit, values):
        """Make the group's membership exactly the given SCIM member values."""
        identities = self.identities_for(values)
        keep = {identity.id for identity in identities}
        OrganizationalDirectoryGroupMembership.objects.filter(organizational_unit_id=unit.id).exclude(
            identity_id__in=keep
        ).delete()
        self.add_members(unit, values)


def _looks_like_uuid(value: str) -> bool:
    """Cheap guard so a directory id never reaches the ORM as a bad UUID."""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def member_values(payload) -> list:
    """
    Read the member ids out of whatever shape the operation carried.

    @description Entra sends group members as ``[{"value": "<id>"}]`` in a
    resource body, and the same list under ``value`` in a patch operation —
    but for a single removal it may instead send a bare string, or encode the
    member in the path (handled by the caller).

    @param payload: The ``members`` value received.
    @returns: The member ids as strings.
    """
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        return [str(payload.get("value"))] if payload.get("value") else []
    values = []
    for entry in payload:
        if isinstance(entry, dict) and entry.get("value"):
            values.append(str(entry["value"]))
        elif isinstance(entry, str):
            values.append(entry)
    return values


class SCIMGroupListEndpoint(SCIMGroupMixin, SCIMBaseView):
    """List and create the directory-bound units of one workspace."""

    def get(self, request, slug):
        """
        List units, optionally filtered.

        @description Entra calls this with ``displayName eq`` to decide
        whether to create the group. Unbound units are included so an admin
        can pre-create a unit and have the directory adopt it by name.
        """
        field, value = parse_filter(request.query_params.get("filter"), GROUP_FILTER_ATTRIBUTES)
        queryset = OrganizationalUnit.objects.filter(workspace_id=self.workspace.id)
        if field:
            queryset = queryset.filter(**{f"{field}__iexact": value})

        start_index, count = read_pagination(request)
        total = queryset.count()
        page = list(queryset.order_by("name")[start_index - 1 : start_index - 1 + count])

        base_url = scim_base_url(request, slug)
        # Members are deliberately omitted from list responses: RFC 7644 allows
        # it, and a workspace-wide listing would otherwise fan out into every
        # group's full membership.
        return Response(
            list_response([group_resource(unit, base_url) for unit in page], total, start_index, len(page)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def post(self, request, slug):
        """
        Bind a directory group to a unit, creating one when allowed.

        @description A unit an admin already created with the same name is
        adopted rather than duplicated — that is the intended workflow, since
        the admin is the one who decided which projects the unit grants.
        """
        payload = request.data if isinstance(request.data, dict) else {}
        display_name = str(payload.get("displayName") or "").strip()
        external_id = str(payload.get("externalId") or "").strip()
        if not display_name:
            raise SCIMError("displayName is required", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue")

        if external_id:
            bound = OrganizationalUnit.objects.filter(workspace_id=self.workspace.id, external_id=external_id).first()
            if bound is not None:
                raise SCIMError(
                    f"Group {external_id} is already provisioned",
                    status.HTTP_409_CONFLICT,
                    scim_type="uniqueness",
                )

        with transaction.atomic():
            unit = (
                OrganizationalUnit.objects.filter(
                    workspace_id=self.workspace.id, name__iexact=display_name, external_id=""
                )
                .order_by("created_at")
                .first()
            )
            if unit is None:
                if not self.connection.auto_create_units:
                    raise SCIMError(
                        f"No organizational unit is bound to '{display_name}' and automatic creation is disabled",
                        status.HTTP_400_BAD_REQUEST,
                        scim_type="invalidValue",
                    )
                unit = OrganizationalUnit(
                    workspace_id=self.workspace.id,
                    name=display_name,
                    slug=unique_unit_slug(self.workspace.id, display_name),
                    sync_source=DirectorySyncSource.SCIM,
                )
            unit.external_id = external_id
            unit.directory_synced_at = timezone.now()
            unit.save()

            self.add_members(unit, member_values(payload.get("members")))
            result = project_unit(unit)

        self.record_sync(result.as_dict())
        base_url = scim_base_url(request, slug)
        response = Response(
            group_resource(unit, base_url, members=members_of(unit)),
            status=status.HTTP_201_CREATED,
            content_type=SCIM_CONTENT_TYPE,
        )
        response["Location"] = group_location(base_url, unit.id)
        return response


class SCIMGroupDetailEndpoint(SCIMGroupMixin, SCIMBaseView):
    """Read, replace, patch and unbind one directory-bound unit."""

    def get(self, request, slug, unit_id):
        """Return one group with its mirrored membership."""
        unit = self.get_unit(unit_id)
        return Response(
            group_resource(unit, scim_base_url(request, slug), members=members_of(unit)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def put(self, request, slug, unit_id):
        """
        Replace a group wholesale.

        @description ``PUT`` replaces the membership with exactly what the
        payload lists. The unit's projects and inherited roles are untouched:
        they are not SCIM attributes, and the directory has no say over them.
        """
        unit = self.get_unit(unit_id)
        payload = request.data if isinstance(request.data, dict) else {}
        display_name = str(payload.get("displayName") or "").strip()

        with transaction.atomic():
            if display_name and display_name != unit.name:
                unit.name = display_name
                unit.slug = unique_unit_slug(self.workspace.id, display_name, exclude_id=unit.id)
            if payload.get("externalId"):
                unit.external_id = str(payload["externalId"]).strip()
            self.assert_binding_is_free(unit)
            unit.directory_synced_at = timezone.now()
            unit.save()

            self.replace_members(unit, member_values(payload.get("members")))
            result = project_unit(unit)

        self.record_sync(result.as_dict())
        return Response(
            group_resource(unit, scim_base_url(request, slug), members=members_of(unit)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def patch(self, request, slug, unit_id):
        """
        Apply a SCIM ``PatchOp`` to a group.

        @description This is the hot path: every time somebody joins or leaves
        an Entra group, Entra sends one ``add`` or ``remove`` here. The removal
        shape in particular varies — a filtered path
        (``members[value eq "..."]``), a ``members`` path with the ids in
        ``value``, or a bare ``members`` remove meaning "empty the group" — and
        all three are handled. Anything else aimed at ``members`` (an unparseable
        filter, an unknown sub-path) or an unknown ``op`` is a 400 and changes
        nothing: guessing at an unknown removal used to empty the group. A path
        to an attribute this service does not store is ignored.
        """
        unit = self.get_unit(unit_id)
        payload = request.data if isinstance(request.data, dict) else {}
        operations = payload.get("Operations") or payload.get("operations") or []
        if not isinstance(operations, list):
            raise SCIMError("Operations must be a list", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue")

        # Validate everything first: a PATCH is applied whole or not at all.
        steps = [step for operation in operations for step in self.parse_operation(operation)]

        with transaction.atomic():
            for step, argument in steps:
                self.apply_step(unit, step, argument)
            self.assert_binding_is_free(unit)
            unit.directory_synced_at = timezone.now()
            unit.save()
            result = project_unit(unit)

        self.record_sync(result.as_dict())
        return Response(
            group_resource(unit, scim_base_url(request, slug), members=members_of(unit)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def parse_operation(self, operation) -> list:
        """
        Validate one ``Operations`` entry and translate it into steps.

        @description Nothing is written here. ``patch`` parses every operation
        before applying any, so a request containing one shape the module does
        not understand is refused whole instead of half-applied — and, above
        all, instead of falling through to "empty the group", which is what an
        unparsed removal used to do. The only thing that empties a group is a
        bare ``remove`` whose path is exactly ``members`` and that carries no
        ``value`` key at all; ``remove`` with an explicit empty list removes
        nothing.

        @param operation: One patch operation.
        @returns: A list of ``(step, argument)`` tuples for ``apply_step``.
        @raises SCIMError: 400 (``invalidSyntax``, ``invalidPath``,
            ``invalidFilter`` or ``invalidValue``) for anything unsupported.
        """
        if not isinstance(operation, dict):
            raise SCIMError("Each operation must be an object", status.HTTP_400_BAD_REQUEST, scim_type="invalidSyntax")

        op = str(operation.get("op", "")).strip().lower()
        if op not in SUPPORTED_PATCH_OPS:
            raise SCIMError(
                f"Unsupported patch operation '{operation.get('op')}'",
                status.HTTP_400_BAD_REQUEST,
                scim_type="invalidSyntax",
            )

        raw_path = _strip_group_schema(str(operation.get("path", "") or "").strip())
        path = raw_path.lower()
        has_value = "value" in operation
        value = operation.get("value")

        if not path:
            return self.parse_pathless(op, value)

        if path == "members":
            if op == "remove":
                if not has_value:
                    return [(STEP_MEMBERS_CLEAR, None)]
                return [(STEP_MEMBERS_REMOVE, self.read_member_values(value))]
            step = STEP_MEMBERS_REPLACE if op == "replace" else STEP_MEMBERS_ADD
            return [(step, self.read_member_values(value))]

        if path.startswith("members["):
            match = MEMBER_FILTER_PATTERN.match(raw_path)
            if match is None:
                raise SCIMError(
                    f"Unsupported members filter '{raw_path}'; only members[value eq \"<id>\"] is supported",
                    status.HTTP_400_BAD_REQUEST,
                    scim_type="invalidFilter",
                )
            if op != "remove":
                raise SCIMError(
                    "A filtered members path is only supported with op 'remove'",
                    status.HTTP_400_BAD_REQUEST,
                    scim_type="invalidPath",
                )
            return [(STEP_MEMBERS_REMOVE, [match.group("value")])]

        if path in ("displayname", "externalid"):
            if op == "remove":
                raise SCIMError(f"'{raw_path}' cannot be removed", status.HTTP_400_BAD_REQUEST, scim_type="mutability")
            text = _clean_text(value)
            if not text:
                raise SCIMError(
                    f"'{raw_path}' needs a non-empty string value",
                    status.HTTP_400_BAD_REQUEST,
                    scim_type="invalidValue",
                )
            return [(STEP_NAME if path == "displayname" else STEP_EXTERNAL_ID, text)]

        if path.startswith("members"):
            raise SCIMError(
                f"Unsupported patch path '{raw_path}'", status.HTTP_400_BAD_REQUEST, scim_type="invalidPath"
            )
        # An attribute this service does not store (a custom mapping in the
        # directory, say). It never touches membership, so it is ignored as it
        # always was: refusing it would fail a provisioning cycle over a value
        # that changes no access.
        return []

    def parse_pathless(self, op, value) -> list:
        """
        Translate a pathless operation: a partial group resource in ``value``.

        @description ``add`` adds the listed members, ``replace`` replaces the
        membership; ``remove`` needs a path. ``id``, ``schemas`` and ``meta``
        are ignored, and so is any attribute this service does not store.

        @param op: ``add`` or ``replace`` (``remove`` is refused).
        @param value: The partial group resource.
        @returns: A list of ``(step, argument)`` tuples for ``apply_step``.
        @raises SCIMError: 400 for a remove, a non-object value or an empty name.
        """
        if op == "remove":
            raise SCIMError("A remove operation needs a path", status.HTTP_400_BAD_REQUEST, scim_type="invalidPath")
        if not isinstance(value, dict):
            raise SCIMError(
                "A pathless operation needs an object value", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue"
            )
        steps = []
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in ("id", "schemas", "meta"):
                continue
            if lowered in ("displayname", "externalid"):
                text = _clean_text(item)
                if not text:
                    raise SCIMError(
                        f"{key} needs a non-empty string value",
                        status.HTTP_400_BAD_REQUEST,
                        scim_type="invalidValue",
                    )
                steps.append((STEP_NAME if lowered == "displayname" else STEP_EXTERNAL_ID, text))
            elif lowered == "members":
                steps.append(
                    (STEP_MEMBERS_REPLACE if op == "replace" else STEP_MEMBERS_ADD, self.read_member_values(item))
                )
            # Any other attribute is one this service does not store; it cannot
            # touch membership and is ignored, as it always was.
        return steps

    @staticmethod
    def read_member_values(value) -> list:
        """Read member ids from an operation value, refusing shapes that are not members."""
        if value is not None and not isinstance(value, (str, dict, list)):
            raise SCIMError("Invalid members value", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue")
        return member_values(value)

    def apply_step(self, unit, step, argument):
        """
        Apply one validated step produced by ``parse_operation``.

        @param unit: The unit bound to the group.
        @param step: One of the ``STEP_*`` constants.
        @param argument: The step's argument (member ids or text).
        """
        if step == STEP_MEMBERS_ADD:
            self.add_members(unit, argument)
        elif step == STEP_MEMBERS_REPLACE:
            self.replace_members(unit, argument)
        elif step == STEP_MEMBERS_REMOVE:
            self.remove_members(unit, argument)
        elif step == STEP_MEMBERS_CLEAR:
            self.replace_members(unit, [])
        elif step == STEP_NAME:
            unit.name = argument
            unit.slug = unique_unit_slug(self.workspace.id, unit.name, exclude_id=unit.id)
        elif step == STEP_EXTERNAL_ID:
            unit.external_id = argument

    def delete(self, request, slug, unit_id):
        """
        Unbind a group the directory removed from provisioning.

        @description The unit itself is kept and only unbound: it carries
        project links, inherited roles and possibly manual members that an
        admin owns, none of which the directory created. Every membership the
        directory *did* create is withdrawn first, so deleting the group in
        Entra really does revoke the access it granted.
        """
        unit = self.get_unit(unit_id)
        with transaction.atomic():
            OrganizationalDirectoryGroupMembership.objects.filter(organizational_unit_id=unit.id).delete()
            result = project_unit(unit)
            unit.external_id = ""
            unit.directory_synced_at = timezone.now()
            unit.save()

        self.record_sync(result.as_dict())
        return Response(status=status.HTTP_204_NO_CONTENT)


def _strip_group_schema(path: str) -> str:
    """Drop the optional core Group schema URN prefix from a patch path."""
    if path.lower().startswith(GROUP_SCHEMA_URN.lower()):
        return path[len(GROUP_SCHEMA_URN) :]
    return path


def _clean_text(value):
    """A stripped string, or ``""`` when the value is not a usable string."""
    return value.strip() if isinstance(value, str) else ""
