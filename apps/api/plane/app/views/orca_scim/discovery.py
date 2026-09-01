# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
SCIM 2.0 discovery endpoints.

``ServiceProviderConfig``, ``ResourceTypes`` and ``Schemas`` are how a client
learns what this service supports before it starts provisioning. Entra tolerates
their absence, but Microsoft's SCIM validator — the tool an administrator runs
to certify the endpoint before wiring the enterprise application — requires all
three, so shipping them is what makes the endpoint testable ahead of the real
tenant connection.

The declarations here are honest rather than aspirational: ``patch`` is
supported because the group endpoints implement it, ``bulk`` and ``sort`` are
declared unsupported because they are not, and ``filter`` advertises the real
page cap.
"""

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from .base import (
    MAX_PAGE_SIZE,
    SCHEMA_GROUP,
    SCHEMA_LIST_RESPONSE,
    SCHEMA_RESOURCE_TYPE,
    SCHEMA_SERVICE_PROVIDER_CONFIG,
    SCHEMA_USER,
    SCIM_CONTENT_TYPE,
    SCIMBaseView,
    scim_base_url,
)


class SCIMServiceProviderConfigEndpoint(SCIMBaseView):
    """Advertise which parts of SCIM this service implements."""

    def get(self, request, slug):
        base_url = scim_base_url(request, slug)
        return Response(
            {
                "schemas": [SCHEMA_SERVICE_PROVIDER_CONFIG],
                "documentationUri": "https://github.com/vitordj/plane/blob/preview/docs/entra-directory-sync.md",
                "patch": {"supported": True},
                "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                "filter": {"supported": True, "maxResults": MAX_PAGE_SIZE},
                "changePassword": {"supported": False},
                "sort": {"supported": False},
                "etag": {"supported": False},
                "authenticationSchemes": [
                    {
                        "type": "oauthbearertoken",
                        "name": "OAuth Bearer Token",
                        "description": "Authentication using the workspace's SCIM bearer token.",
                        "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                        "primary": True,
                    }
                ],
                "meta": {
                    "resourceType": "ServiceProviderConfig",
                    "location": f"{base_url}/ServiceProviderConfig",
                },
            },
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )


class SCIMResourceTypesEndpoint(SCIMBaseView):
    """List the resource types this service exposes."""

    def get(self, request, slug):
        base_url = scim_base_url(request, slug)
        resources = [
            {
                "schemas": [SCHEMA_RESOURCE_TYPE],
                "id": "User",
                "name": "User",
                "endpoint": "/Users",
                "description": "A person as the directory knows them, mirrored into the workspace.",
                "schema": SCHEMA_USER,
                "schemaExtensions": [],
                "meta": {"resourceType": "ResourceType", "location": f"{base_url}/ResourceTypes/User"},
            },
            {
                "schemas": [SCHEMA_RESOURCE_TYPE],
                "id": "Group",
                "name": "Group",
                "endpoint": "/Groups",
                "description": "A directory group, bound to an organizational unit in the workspace.",
                "schema": SCHEMA_GROUP,
                "schemaExtensions": [],
                "meta": {"resourceType": "ResourceType", "location": f"{base_url}/ResourceTypes/Group"},
            },
        ]
        return Response(
            {
                "schemas": [SCHEMA_LIST_RESPONSE],
                "totalResults": len(resources),
                "startIndex": 1,
                "itemsPerPage": len(resources),
                "Resources": resources,
            },
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )


def _attribute(name, attr_type="string", multi_valued=False, required=False, case_exact=False, mutability="readWrite"):
    """Build one SCIM schema attribute declaration."""
    return {
        "name": name,
        "type": attr_type,
        "multiValued": multi_valued,
        "required": required,
        "caseExact": case_exact,
        "mutability": mutability,
        "returned": "default",
        "uniqueness": "none",
    }


class SCIMSchemasEndpoint(SCIMBaseView):
    """Describe the attributes of the resources this service accepts."""

    def get(self, request, slug):
        base_url = scim_base_url(request, slug)
        schemas = [
            {
                "id": SCHEMA_USER,
                "name": "User",
                "description": "A directory identity mirrored into a Plane workspace.",
                "attributes": [
                    _attribute("userName", required=True),
                    _attribute("displayName"),
                    _attribute("externalId"),
                    _attribute("active", attr_type="boolean"),
                    _attribute("emails", attr_type="complex", multi_valued=True),
                    _attribute("name", attr_type="complex"),
                ],
                "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{SCHEMA_USER}"},
            },
            {
                "id": SCHEMA_GROUP,
                "name": "Group",
                "description": "A directory group bound to an organizational unit.",
                "attributes": [
                    _attribute("displayName", required=True),
                    _attribute("externalId"),
                    _attribute("members", attr_type="complex", multi_valued=True),
                ],
                "meta": {"resourceType": "Schema", "location": f"{base_url}/Schemas/{SCHEMA_GROUP}"},
            },
        ]
        return Response(
            {
                "schemas": [SCHEMA_LIST_RESPONSE],
                "totalResults": len(schemas),
                "startIndex": 1,
                "itemsPerPage": len(schemas),
                "Resources": schemas,
            },
            status=status.HTTP_200_OK,
            content_type=SCIM_CONTENT_TYPE,
        )
