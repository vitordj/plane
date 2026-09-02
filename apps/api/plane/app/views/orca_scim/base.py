# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Shared plumbing for the SCIM 2.0 service the fork exposes to Microsoft Entra ID.

SCIM is not a normal Plane API: the caller is a machine in Microsoft's cloud
holding a bearer token, not a signed-in user, and both the wire format and the
error shape are dictated by RFC 7644. This module concentrates everything that
differs from the rest of ``/api/orca/`` so the resource views stay readable:

* a renderer and parser for ``application/scim+json``, which Entra asks for by
  ``Accept`` header and which plain DRF would answer with a 406;
* bearer-token authentication against the workspace's directory connection;
* the SCIM error envelope, list envelope and 1-based pagination;
* the small slice of SCIM filter syntax Entra actually emits.

Per FORK.md this lives entirely in the fork's own namespace and touches no
upstream view.
"""

# Python imports
import re
import secrets

# Django imports
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

# Module imports
from plane.db.models import OrganizationalDirectoryConnection, Workspace, hash_directory_token
from plane.app.views.organizational_unit import organizational_units_enabled

SCIM_CONTENT_TYPE = "application/scim+json"

# Schema URNs, quoted verbatim from RFC 7643/7644 — Entra matches on them.
SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_ENTERPRISE_USER = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
SCHEMA_LIST_RESPONSE = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCHEMA_SERVICE_PROVIDER_CONFIG = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCHEMA_RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"

# Entra pages with count=100 by default; the cap keeps a bad request from
# asking for the whole directory in one response.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


class SCIMRenderer(JSONRenderer):
    """Render responses as ``application/scim+json``, which Entra asks for."""

    media_type = SCIM_CONTENT_TYPE
    format = "scim+json"


class SCIMParser(JSONParser):
    """Accept request bodies sent as ``application/scim+json``."""

    media_type = SCIM_CONTENT_TYPE


class SCIMError(Exception):
    """
    A SCIM-shaped failure.

    @description Raised anywhere inside a resource view and turned into the
    RFC 7644 error envelope by ``SCIMBaseView.handle_exception``, so error
    handling never has to be repeated per endpoint.

    @param detail: Human-readable message returned to the provisioning log.
    @param status_code: HTTP status to answer with.
    @param scim_type: Optional SCIM error type (``uniqueness``,
        ``invalidValue``, ``invalidFilter``, ``mutability``…).
    """

    def __init__(self, detail, status_code=status.HTTP_400_BAD_REQUEST, scim_type=None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.scim_type = scim_type


def scim_error_response(detail, status_code, scim_type=None) -> Response:
    """
    Build the RFC 7644 error envelope.

    @param detail: Human-readable message.
    @param status_code: HTTP status code.
    @param scim_type: Optional SCIM error type.
    @returns: A DRF ``Response`` carrying the envelope.
    """
    body = {"schemas": [SCHEMA_ERROR], "detail": str(detail), "status": str(status_code)}
    if scim_type:
        body["scimType"] = scim_type
    return Response(body, status=status_code, content_type=SCIM_CONTENT_TYPE)


def list_response(resources, total, start_index, items_per_page) -> dict:
    """
    Build the RFC 7644 ``ListResponse`` envelope.

    @param resources: Already-serialized SCIM resources for this page.
    @param total: Total number of matches, not just this page.
    @param start_index: 1-based index of the first item returned.
    @param items_per_page: How many items this page actually holds.
    @returns: The envelope as a plain dict.
    """
    return {
        "schemas": [SCHEMA_LIST_RESPONSE],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": items_per_page,
        "Resources": list(resources),
    }


def read_pagination(request):
    """
    Read SCIM's 1-based pagination parameters.

    @description SCIM counts from 1, not 0, and a client may legitimately omit
    both parameters. Out-of-range values are clamped rather than rejected:
    RFC 7644 treats a ``startIndex`` below 1 as 1.

    @param request: The incoming request.
    @returns: ``(start_index, count)`` with ``start_index`` 1-based.
    """
    try:
        start_index = int(request.query_params.get("startIndex", 1))
    except (TypeError, ValueError):
        start_index = 1
    try:
        count = int(request.query_params.get("count", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        count = DEFAULT_PAGE_SIZE

    start_index = max(start_index, 1)
    count = max(min(count, MAX_PAGE_SIZE), 0)
    return start_index, count


# ``userName eq "someone@example.com"`` — the only filter shape Entra emits for
# provisioning, optionally with the attribute in any case.
_FILTER_PATTERN = re.compile(r'^\s*(?P<attr>[\w.:]+)\s+(?P<op>eq)\s+"(?P<value>[^"]*)"\s*$', re.IGNORECASE)


def parse_filter(raw_filter, allowed_attributes):
    """
    Parse the slice of SCIM filter syntax Entra uses for provisioning.

    @description Entra looks a resource up before creating it, always with a
    single ``attribute eq "value"`` term. Supporting exactly that — and
    rejecting anything else as ``invalidFilter`` — is far safer than a partial
    expression evaluator that might silently match the wrong resources.

    @param raw_filter: The raw ``filter`` query parameter, may be ``None``.
    @param allowed_attributes: Mapping of SCIM attribute name (lowercased) to
        the model field to filter on.
    @returns: ``(field, value)``, or ``(None, None)`` when no filter was sent.
    @raises SCIMError: When the filter is malformed or names another attribute.
    """
    if not raw_filter:
        return None, None

    match = _FILTER_PATTERN.match(raw_filter)
    if match is None:
        raise SCIMError(
            f"Unsupported filter: {raw_filter}",
            status.HTTP_400_BAD_REQUEST,
            scim_type="invalidFilter",
        )

    attribute = match.group("attr").lower()
    if attribute not in allowed_attributes:
        raise SCIMError(
            f"Filtering on '{match.group('attr')}' is not supported",
            status.HTTP_400_BAD_REQUEST,
            scim_type="invalidFilter",
        )
    return allowed_attributes[attribute], match.group("value")


def scim_base_url(request, slug) -> str:
    """
    Absolute base URL of one workspace's SCIM service.

    @description This is the value an administrator pastes into Entra as the
    "Tenant URL", and the prefix of every ``meta.location`` and ``$ref`` the
    service returns. Derived from the request so it stays correct behind a
    proxy or on a custom domain, instead of a configured host that can drift.

    @param request: The incoming request.
    @param slug: Workspace slug.
    @returns: e.g. ``https://plane.example.com/api/orca/scim/v2/workspaces/acme``.
    """
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{request.get_host()}/api/orca/scim/v2/workspaces/{slug}"


class SCIMBaseView(APIView):
    """
    Base for every SCIM resource view.

    @description Authenticates the caller with the workspace's SCIM bearer
    token instead of a Plane session — the caller is Microsoft's provisioning
    service, which has no user identity in Plane — and converts ``SCIMError``
    into the RFC 7644 envelope.

    The authenticated connection is available to subclasses as
    ``self.connection`` and the workspace as ``self.workspace``.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [SCIMRenderer, JSONRenderer]
    parser_classes = [SCIMParser, JSONParser]

    # Discovery endpoints (ServiceProviderConfig, Schemas) are still gated on a
    # valid token: they say nothing secret, but leaving them open would let
    # anyone enumerate which workspaces have provisioning configured.
    requires_authentication = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # The organizational layer's kill switch closes provisioning too: a
        # SCIM write is a unit membership write, and an operator who turned the
        # layer off must not find Entra still filling units through this door.
        # Checked before authentication so the answer is the same 404 the rest
        # of the layer gives, whatever token the caller holds.
        if not organizational_units_enabled():
            raise SCIMError("The organizational layer is disabled on this instance", status.HTTP_404_NOT_FOUND)
        self.workspace = None
        self.connection = None
        if self.requires_authentication:
            self.workspace, self.connection = self.authenticate_directory(request, kwargs.get("slug"))

    def authenticate_directory(self, request, slug):
        """
        Verify the bearer token against the workspace's directory connection.

        @description Answers 401 for every failure mode — no header, wrong
        token, connection switched off, unknown workspace — so an unauthorized
        caller cannot use the response to learn which workspaces exist or
        whether provisioning is enabled on them.

        @param request: The incoming request.
        @param slug: Workspace slug from the URL.
        @returns: ``(workspace, connection)``.
        @raises SCIMError: 401 when the caller may not provision this workspace.
        """
        unauthorized = SCIMError("Invalid or missing bearer token", status.HTTP_401_UNAUTHORIZED)

        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.lower().startswith("bearer "):
            raise unauthorized
        token = header[7:].strip()
        if not token:
            raise unauthorized

        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            raise unauthorized

        connection = OrganizationalDirectoryConnection.objects.filter(workspace_id=workspace.id).first()
        if connection is None or not connection.is_enabled or not connection.token_hash:
            raise unauthorized

        # Constant-time comparison: the digests are the same length, so a
        # short-circuiting comparison would leak a prefix oracle.
        if not secrets.compare_digest(connection.token_hash, hash_directory_token(token)):
            raise unauthorized

        connection.token_last_used_at = timezone.now()
        connection.save(update_fields=["token_last_used_at", "updated_at"])
        return workspace, connection

    def record_sync(self, summary: dict):
        """
        Stamp the connection with the outcome of a provisioning write.

        @param summary: Counters from the projector, shown on the settings screen.
        """
        if self.connection is None:
            return
        self.connection.last_sync_at = timezone.now()
        self.connection.last_sync_summary = summary
        self.connection.save(update_fields=["last_sync_at", "last_sync_summary", "updated_at"])

    def handle_exception(self, exc):
        """Render ``SCIMError`` as the RFC 7644 envelope, delegate the rest."""
        if isinstance(exc, SCIMError):
            return scim_error_response(exc.detail, exc.status_code, exc.scim_type)
        return super().handle_exception(exc)
