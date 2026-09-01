# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Translation between SCIM resources and the fork's directory mirror.

Kept apart from the views because the mapping is the part most likely to need
tuning against a real tenant: which claim an installation maps to ``userName``,
whether a group's ``displayName`` should drive the unit name, and so on. Having
it in one module means the views never grow provider-specific branches.

Plane-side, a SCIM ``User`` is an ``OrganizationalDirectoryIdentity`` and a
SCIM ``Group`` is an ``OrganizationalUnit`` bound to that group. The resource
``id`` is Plane's own primary key, which is what Entra stores and replays on
every later call; ``externalId`` carries the directory's own identifier.
"""

# Python imports
from urllib.parse import quote

# Module imports
from .base import SCHEMA_GROUP, SCHEMA_USER


def _timestamp(value):
    """Format a datetime the way SCIM expects, tolerating ``None``."""
    return value.isoformat() if value else None


def _meta(resource_type, instance, location):
    """Build the SCIM ``meta`` sub-attribute shared by every resource."""
    return {
        "resourceType": resource_type,
        "created": _timestamp(instance.created_at),
        "lastModified": _timestamp(instance.updated_at),
        "location": location,
    }


def user_location(base_url, identity_id):
    """Absolute URL of one SCIM ``User`` resource."""
    return f"{base_url}/Users/{quote(str(identity_id))}"


def group_location(base_url, unit_id):
    """Absolute URL of one SCIM ``Group`` resource."""
    return f"{base_url}/Groups/{quote(str(unit_id))}"


def split_display_name(display_name: str):
    """
    Split a directory display name into given and family names.

    @description Entra sends ``name.givenName``/``name.familyName`` when the
    tenant maps them, but plenty of tenants only map ``displayName``. Deriving
    the halves keeps the Plane profile from showing an empty name in that case.

    @param display_name: The directory's display name.
    @returns: ``(first_name, last_name)``; the last name may be empty.
    """
    parts = (display_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def user_resource(identity, base_url) -> dict:
    """
    Render a directory identity as a SCIM ``User``.

    @description ``active`` reports the directory's own view of the person, not
    whether Plane could resolve them: reporting ``false`` for someone Plane
    simply does not know would make Entra believe it had deprovisioned them.
    The resolution state is exposed separately under the fork's own extension
    URN so the provisioning log still shows why no access was granted.

    @param identity: The ``OrganizationalDirectoryIdentity``.
    @param base_url: SCIM base URL of this workspace.
    @returns: The SCIM resource as a dict.
    """
    first_name, last_name = split_display_name(identity.display_name)
    resource = {
        "schemas": [SCHEMA_USER],
        "id": str(identity.id),
        "userName": identity.user_name,
        "active": identity.is_active,
        "displayName": identity.display_name,
        "name": {
            "formatted": identity.display_name,
            "givenName": first_name,
            "familyName": last_name,
        },
        "emails": ([{"value": identity.email, "primary": True, "type": "work"}] if identity.email else []),
        "meta": _meta("User", identity, user_location(base_url, identity.id)),
        # Fork extension: tells the operator reading the Entra provisioning log
        # why a pushed user did not turn into workspace access.
        "urn:orca:params:scim:schemas:extension:2.0:User": {
            "resolutionState": identity.state,
            "workspaceMemberId": (str(identity.workspace_member_id) if identity.workspace_member_id else None),
        },
    }
    if identity.external_id:
        resource["externalId"] = identity.external_id
    return resource


def group_resource(unit, base_url, members=None) -> dict:
    """
    Render an organizational unit as a SCIM ``Group``.

    @param unit: The ``OrganizationalUnit`` bound to the directory group.
    @param base_url: SCIM base URL of this workspace.
    @param members: Iterable of ``OrganizationalDirectoryIdentity`` in the
        group, or ``None`` to omit the attribute (RFC 7644 allows a service
        provider to leave large sub-attributes out of list responses).
    @returns: The SCIM resource as a dict.
    """
    resource = {
        "schemas": [SCHEMA_GROUP],
        "id": str(unit.id),
        "displayName": unit.name,
        "meta": _meta("Group", unit, group_location(base_url, unit.id)),
    }
    if unit.external_id:
        resource["externalId"] = unit.external_id
    if members is not None:
        resource["members"] = [
            {
                "value": str(identity.id),
                "display": identity.display_name or identity.user_name,
                "$ref": user_location(base_url, identity.id),
            }
            for identity in members
        ]
    return resource


def primary_email(payload: dict) -> str:
    """
    Pick the address to match a directory identity against a workspace member.

    @description A SCIM ``emails`` array is unordered and only optionally
    flagged ``primary``. Preferring the flagged entry, then a ``work`` entry,
    then the first one, then ``userName``, mirrors what every mature SCIM
    implementation does and keeps matching stable when a tenant maps several
    addresses.

    @param payload: The SCIM ``User`` resource received.
    @returns: The email to match on, possibly empty.
    """
    emails = payload.get("emails") or []
    if isinstance(emails, dict):
        emails = [emails]

    flagged = [entry for entry in emails if isinstance(entry, dict) and entry.get("primary")]
    work = [entry for entry in emails if isinstance(entry, dict) and entry.get("type") == "work"]
    for candidates in (flagged, work, emails):
        for entry in candidates:
            if isinstance(entry, dict) and entry.get("value"):
                return str(entry["value"]).strip()
            if isinstance(entry, str) and entry:
                return entry.strip()

    user_name = payload.get("userName") or ""
    return user_name.strip() if "@" in user_name else ""


def display_name_from(payload: dict) -> str:
    """
    Derive a display name from whichever attributes the tenant mapped.

    @param payload: The SCIM ``User`` resource received.
    @returns: The best available human-readable name, possibly empty.
    """
    if payload.get("displayName"):
        return str(payload["displayName"]).strip()

    name = payload.get("name") or {}
    if isinstance(name, dict):
        if name.get("formatted"):
            return str(name["formatted"]).strip()
        joined = " ".join(part for part in [name.get("givenName"), name.get("familyName")] if part)
        if joined.strip():
            return joined.strip()
    return ""


def identity_fields_from(payload: dict) -> dict:
    """
    Map an incoming SCIM ``User`` onto the mirror's columns.

    @param payload: The SCIM resource received from the directory.
    @returns: Field values ready to assign to an identity.
    """
    user_name = (payload.get("userName") or "").strip()
    active = payload.get("active")
    return {
        "user_name": user_name,
        "email": primary_email(payload),
        "display_name": display_name_from(payload),
        "external_id": (payload.get("externalId") or "").strip(),
        # Entra sends `active` as a real boolean, but some connectors send the
        # strings "True"/"False"; treat an absent value as active, per RFC 7643.
        "is_active": coerce_boolean(active, default=True),
        "raw_payload": payload,
    }


def coerce_boolean(value, default=True) -> bool:
    """
    Read a SCIM boolean that may have arrived as a string.

    @param value: The raw value from the payload.
    @param default: What an absent value means.
    @returns: The boolean.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)
