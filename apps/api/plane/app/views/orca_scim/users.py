# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
SCIM 2.0 ``/Users`` endpoints.

A SCIM user is mirrored, never created: Plane accounts are not provisioned from
the directory (see the v1 rule that a unit never invites anyone). What these
endpoints maintain is the *identity* — the directory's record of a person, and
the workspace member it resolves to, if any. Deprovisioning a user in Entra
therefore withdraws the access the directory granted, and leaves the Plane
account, and any access an admin granted by hand, alone.
"""

# Django imports
from django.db import transaction
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.services.orca import project_identity, resolve_identity
from plane.db.models import OrganizationalDirectoryGroupMembership, OrganizationalDirectoryIdentity

from .base import (
    SCIM_CONTENT_TYPE,
    SCIMBaseView,
    SCIMError,
    list_response,
    parse_filter,
    read_pagination,
    scim_base_url,
)
from .resources import coerce_boolean, identity_fields_from, primary_email, user_location, user_resource

# SCIM attribute names Entra filters users by, mapped to mirror columns.
USER_FILTER_ATTRIBUTES = {
    "username": "user_name",
    "externalid": "external_id",
    "id": "id",
    "emails.value": "email",
    "emails": "email",
}


class SCIMUserListEndpoint(SCIMBaseView):
    """List and create the directory identities of one workspace."""

    def get(self, request, slug):
        """
        List identities, optionally filtered.

        @description Entra calls this before every create to find out whether
        the user already exists, always with a ``userName eq`` filter.
        """
        field, value = parse_filter(request.query_params.get("filter"), USER_FILTER_ATTRIBUTES)
        queryset = OrganizationalDirectoryIdentity.objects.filter(workspace_id=self.workspace.id)
        if field:
            # Matching case-insensitively matters: SCIM declares userName
            # case-insensitive, and Entra does not normalize UPN casing.
            queryset = queryset.filter(**{f"{field}__iexact": value})

        start_index, count = read_pagination(request)
        total = queryset.count()
        page = list(queryset.order_by("user_name")[start_index - 1 : start_index - 1 + count])

        base_url = scim_base_url(request, slug)
        return Response(
            list_response([user_resource(identity, base_url) for identity in page], total, start_index, len(page)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def post(self, request, slug):
        """
        Mirror a user the directory is provisioning.

        @description Creating the identity is all that happens here — no Plane
        account and no workspace membership is created. If the person is
        already an active workspace member the identity links immediately;
        otherwise it is parked as unresolved and shows up in the report.
        """
        payload = request.data if isinstance(request.data, dict) else {}
        fields = identity_fields_from(payload)
        if not fields["user_name"]:
            raise SCIMError("userName is required", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue")

        existing = OrganizationalDirectoryIdentity.objects.filter(
            workspace_id=self.workspace.id, user_name__iexact=fields["user_name"]
        ).first()
        if existing is not None:
            raise SCIMError(
                f"User {fields['user_name']} already exists",
                status.HTTP_409_CONFLICT,
                scim_type="uniqueness",
            )

        with transaction.atomic():
            identity = OrganizationalDirectoryIdentity(
                workspace_id=self.workspace.id,
                last_seen_at=timezone.now(),
                **fields,
            )
            identity.save()
            resolve_identity(identity)
            # A brand-new identity holds no group memberships yet, but it can
            # arrive *after* the groups that reference it when a tenant
            # provisions groups first; projecting covers that ordering.
            result = project_identity(identity)

        self.record_sync(result.as_dict())
        base_url = scim_base_url(request, slug)
        response = Response(
            user_resource(identity, base_url),
            status=status.HTTP_201_CREATED,
            content_type=SCIM_CONTENT_TYPE,
        )
        response["Location"] = user_location(base_url, identity.id)
        return response


class SCIMUserDetailEndpoint(SCIMBaseView):
    """Read, replace, patch and deprovision one directory identity."""

    def assert_user_name_is_free(self, identity):
        """
        Refuse a rename that would collide with another mirrored identity.

        @description ``userName`` is unique per workspace, so without this the
        collision surfaces as an IntegrityError and a 500 — which Entra retries
        forever. A ``uniqueness`` conflict is the answer RFC 7644 defines, and
        the one that makes the provisioning log readable.

        @param identity: The identity about to be saved.
        @raises SCIMError: 409 when another identity already holds the name.
        """
        clash = (
            OrganizationalDirectoryIdentity.objects.filter(
                workspace_id=self.workspace.id, user_name__iexact=identity.user_name
            )
            .exclude(pk=identity.pk)
            .exists()
        )
        if clash:
            raise SCIMError(
                f"User {identity.user_name} already exists",
                status.HTTP_409_CONFLICT,
                scim_type="uniqueness",
            )

    def get_identity(self, identity_id):
        """Fetch an identity inside the authenticated workspace, or 404 in SCIM shape."""
        identity = OrganizationalDirectoryIdentity.objects.filter(
            workspace_id=self.workspace.id, pk=identity_id
        ).first()
        if identity is None:
            raise SCIMError(f"User {identity_id} not found", status.HTTP_404_NOT_FOUND)
        return identity

    def get(self, request, slug, identity_id):
        """Return one identity."""
        identity = self.get_identity(identity_id)
        return Response(
            user_resource(identity, scim_base_url(request, slug)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def put(self, request, slug, identity_id):
        """
        Replace an identity wholesale.

        @description RFC 7644 ``PUT`` semantics: attributes absent from the
        payload go back to their defaults, which is why this rebuilds the row
        from the payload rather than merging into it.
        """
        identity = self.get_identity(identity_id)
        payload = request.data if isinstance(request.data, dict) else {}
        fields = identity_fields_from(payload)
        if not fields["user_name"]:
            raise SCIMError("userName is required", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue")

        with transaction.atomic():
            for key, value in fields.items():
                setattr(identity, key, value)
            self.assert_user_name_is_free(identity)
            identity.last_seen_at = timezone.now()
            identity.save()
            resolve_identity(identity)
            result = project_identity(identity)

        self.record_sync(result.as_dict())
        return Response(
            user_resource(identity, scim_base_url(request, slug)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def patch(self, request, slug, identity_id):
        """
        Apply a SCIM ``PatchOp``.

        @description This is the call Entra uses for the change that matters
        most — ``active: false`` when somebody is deprovisioned — so the patch
        handling has to tolerate every shape Entra emits: an operation with a
        ``path``, one whose ``value`` is a whole object, and ``op`` names in
        any capitalization.
        """
        identity = self.get_identity(identity_id)
        payload = request.data if isinstance(request.data, dict) else {}
        operations = payload.get("Operations") or payload.get("operations") or []
        if not isinstance(operations, list):
            raise SCIMError("Operations must be a list", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue")

        with transaction.atomic():
            for operation in operations:
                if isinstance(operation, dict):
                    self.apply_operation(identity, operation)
            if not identity.user_name:
                raise SCIMError("userName cannot be cleared", status.HTTP_400_BAD_REQUEST, scim_type="invalidValue")
            self.assert_user_name_is_free(identity)
            identity.last_seen_at = timezone.now()
            identity.save()
            resolve_identity(identity)
            result = project_identity(identity)

        self.record_sync(result.as_dict())
        return Response(
            user_resource(identity, scim_base_url(request, slug)),
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )

    def apply_operation(self, identity, operation):
        """
        Apply one entry of a ``PatchOp`` to the mirrored identity.

        @description Unknown paths are ignored rather than rejected. A tenant
        may map attributes Plane has no column for (department, manager,
        employee number); failing the whole request over one of them would
        quarantine the user in Entra and stop the attributes that *do* matter
        from ever arriving.

        @param identity: The identity being patched.
        @param operation: One ``Operations`` entry.
        """
        op = str(operation.get("op", "")).lower()
        path = str(operation.get("path", "") or "").lower()
        value = operation.get("value")

        if op == "remove" and path:
            # Removing a scalar attribute means clearing it.
            self.assign(identity, path, None, clearing=True)
            return

        if not path:
            # Pathless add/replace: the value is a partial resource.
            if isinstance(value, dict):
                for key, nested in value.items():
                    self.assign(identity, str(key).lower(), nested)
            return

        self.assign(identity, path, value)

    def assign(self, identity, path, value, clearing=False):
        """
        Write one patched attribute onto the mirror.

        @param identity: The identity being patched.
        @param path: Lowercased SCIM attribute path.
        @param value: The new value.
        @param clearing: Whether this came from a ``remove`` operation.
        """
        if path == "active":
            identity.is_active = False if clearing else coerce_boolean(value, default=True)
        elif path == "username":
            identity.user_name = "" if clearing else str(value or "").strip()
        elif path == "externalid":
            identity.external_id = "" if clearing else str(value or "").strip()
        elif path == "displayname":
            identity.display_name = "" if clearing else str(value or "").strip()
        elif path.startswith("emails"):
            if clearing:
                identity.email = ""
            elif isinstance(value, (list, dict)):
                identity.email = primary_email({"emails": value})
            else:
                identity.email = str(value or "").strip()
        elif path == "name.formatted":
            identity.display_name = "" if clearing else str(value or "").strip()
        elif path == "name" and isinstance(value, dict):
            identity.display_name = str(value.get("formatted") or identity.display_name).strip()

    def delete(self, request, slug, identity_id):
        """
        Deprovision an identity.

        @description Entra usually deprovisions by patching ``active: false``,
        but may send ``DELETE`` when a user leaves the app's scope. Both paths
        must withdraw the same access, so this deactivates and projects before
        soft-deleting the mirror row — the memberships the directory created
        are withdrawn, and anything manual is untouched.
        """
        identity = self.get_identity(identity_id)
        with transaction.atomic():
            identity.is_active = False
            identity.save(update_fields=["is_active", "updated_at"])
            result = project_identity(identity)
            # Drop the mirrored group entries in the same transaction. Soft
            # deletion cascades through a Celery task, which is too late here:
            # the projector reads those rows, so leaving them behind would let
            # a deleted identity keep asserting group membership until the
            # task ran.
            OrganizationalDirectoryGroupMembership.objects.filter(identity_id=identity.id).delete()
            identity.delete()

        self.record_sync(result.as_dict())
        return Response(status=status.HTTP_204_NO_CONTENT)
