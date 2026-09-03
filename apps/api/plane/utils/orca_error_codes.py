# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Stable error codes for the Orca organizational layer.

The layer's views answered with English prose in an ``{"error": ...}`` body,
which the web app put straight into a toast — so a workspace working in
Portuguese got an English sentence at the one moment it least wants one.

Translating on the server would mean gettext, a locale middleware and ``.po``
catalogues, none of which upstream has. Instead each failure carries a stable
code the client can look up in the i18n catalogue, and the English prose stays
in the body for API clients, logs and anyone reading the response by hand.

Numbers live in their own 4900 band, clear of upstream's ``ERROR_CODES``
(4091-4702), so upstream can grow its table without renumbering this one.
A code is permanent once shipped: clients key off it.
"""

# Django imports
from rest_framework import status
from rest_framework.response import Response

ORCA_ERROR_CODES = {
    # organizational units
    "ORG_UNIT_NOT_FOUND": 4900,
    "ORG_UNIT_NAME_REQUIRED": 4901,
    "ORG_UNIT_SLUG_TAKEN": 4902,
    "ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE": 4903,
    "ORG_UNIT_MEMBERSHIP_NOT_FOUND": 4904,
    "ORG_UNIT_LEAD_ALREADY_SET": 4905,
    "ORG_UNIT_NOT_IN_WORKSPACE": 4906,
    # units linked to projects
    "ORG_UNIT_PROJECT_REQUIRED": 4907,
    "ORG_UNIT_INVALID_ROLE": 4908,
    "ORG_UNIT_PROJECT_NOT_IN_WORKSPACE": 4909,
    "ORG_UNIT_LINK_NOT_FOUND": 4910,
    # work items
    "ORG_WORK_ITEM_NOT_FOUND": 4911,
    "ORG_WORK_ITEM_HAS_NO_UNIT": 4912,
    "ORG_INVALID_ASSIGNMENT_MODE": 4913,
    "ORG_UNIT_NOT_COVERING_PROJECT": 4916,
    # assignment service
    "ORG_ASSIGNMENT_MODE_NOT_ALLOWED": 4917,
    "ORG_EXECUTOR_NOT_ELIGIBLE": 4918,
    "ORG_WORK_ITEM_ALREADY_CLAIMED": 4919,
    "ORG_DECISION_STALE": 4920,
    "ORG_INVALID_ROUTING_TRANSITION": 4921,
    # public automation API
    "ORG_IDEMPOTENCY_KEY_REQUIRED": 4922,
    "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH": 4923,
    "ORG_OPERATION_IN_PROGRESS": 4924,
    "ORG_EXTERNAL_BINDING_CONFLICT": 4925,
    "ORG_ASSIGNEES_NOT_ALLOWED_HERE": 4926,
    "ORG_IF_MATCH_REQUIRED": 4927,
    "ORG_INTERNAL_ERROR": 4928,
    "ORG_PROCESS_PROJECTION_DISABLED": 4929,
    # availability and allocation limits
    "ORG_AVAILABILITY_INTERVAL_INVALID": 4930,
    "ORG_AVAILABILITY_NOT_FOUND": 4931,
    "ORG_WORKSPACE_MEMBER_NOT_FOUND": 4932,
    "ORG_ALLOCATION_LIMIT_FORBIDDEN": 4933,
    # executive view
    "ORG_INVALID_EXECUTIVE_PERIOD": 4934,
    # directory provisioning
    "ORG_DIRECTORY_WORKSPACE_NOT_FOUND": 4914,
    "ORG_DIRECTORY_TOKEN_REQUIRED": 4915,
}

# The English prose each code carries. Kept here rather than at the call sites
# so the same failure reads the same way wherever it is raised, and so this file
# alone tells you what a code means.
ORCA_ERROR_MESSAGES = {
    "ORG_UNIT_NOT_FOUND": "Organizational unit not found",
    "ORG_UNIT_NAME_REQUIRED": "Name is required",
    "ORG_UNIT_SLUG_TAKEN": "An organizational unit with this slug already exists",
    "ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE": "All members must be active members of this workspace",
    "ORG_UNIT_MEMBERSHIP_NOT_FOUND": "Membership not found",
    "ORG_UNIT_LEAD_ALREADY_SET": "This organizational unit already has an active lead",
    "ORG_UNIT_NOT_IN_WORKSPACE": "Organizational unit not found in this workspace",
    "ORG_UNIT_PROJECT_REQUIRED": "Project is required",
    "ORG_UNIT_INVALID_ROLE": "Invalid role",
    "ORG_UNIT_PROJECT_NOT_IN_WORKSPACE": "Project not found in this workspace",
    "ORG_UNIT_LINK_NOT_FOUND": "Linked project not found",
    "ORG_WORK_ITEM_NOT_FOUND": "Work item not found",
    "ORG_WORK_ITEM_HAS_NO_UNIT": "No organizational unit is responsible for this work item",
    "ORG_UNIT_NOT_COVERING_PROJECT": "This organizational unit does not cover this work item's project",
    "ORG_ASSIGNMENT_MODE_NOT_ALLOWED": "This assignment mode is not allowed by the unit's policy",
    "ORG_EXECUTOR_NOT_ELIGIBLE": "That person cannot take this work item for this unit",
    "ORG_WORK_ITEM_ALREADY_CLAIMED": "Somebody else has already taken this work item",
    "ORG_DECISION_STALE": "This work item has moved since you last read it",
    "ORG_INVALID_ROUTING_TRANSITION": "This work item is not in a state that allows this",
    "ORG_IDEMPOTENCY_KEY_REQUIRED": "This request must carry an Idempotency-Key header",
    "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH": "This idempotency key was already used with a different payload",
    "ORG_OPERATION_IN_PROGRESS": "An identical request is still being processed",
    "ORG_EXTERNAL_BINDING_CONFLICT": "This external reference already points at another work item",
    "ORG_ASSIGNEES_NOT_ALLOWED_HERE": "Assignees are set through the responsibility block, not on the work item",
    "ORG_IF_MATCH_REQUIRED": "This request must carry an If-Match header with the current decision id",
    "ORG_INTERNAL_ERROR": "The operation failed unexpectedly and was recorded as failed",
    "ORG_PROCESS_PROJECTION_DISABLED": "Process projection is not enabled on this instance",
    "ORG_INVALID_ASSIGNMENT_MODE": "Invalid mode",
    "ORG_AVAILABILITY_INTERVAL_INVALID": "An absence has to end after it starts",
    "ORG_AVAILABILITY_NOT_FOUND": "Absence not found",
    "ORG_WORKSPACE_MEMBER_NOT_FOUND": "That person is not an active member of this workspace",
    "ORG_ALLOCATION_LIMIT_FORBIDDEN": "Only a coordinator can set how much work somebody is given",
    "ORG_INVALID_EXECUTIVE_PERIOD": "Choose one of the periods this report offers",
    "ORG_DIRECTORY_WORKSPACE_NOT_FOUND": "Workspace not found",
    "ORG_DIRECTORY_TOKEN_REQUIRED": "Issue a SCIM token before enabling directory provisioning",
}


def orca_error(name, status_code=status.HTTP_400_BAD_REQUEST):
    """Build the standard error response for an Orca failure.

    @description Carries three things at once: ``error`` so an API client or a
        log line reads the same as before, ``error_code`` so the web app can
        translate it, and ``error_message`` with the symbolic name so a human
        reading the response does not have to look the number up.
    @param name: A key of ``ORCA_ERROR_CODES``. Unknown names raise, on the
        theory that a typo should fail in tests rather than ship a null code.
    @param status_code: HTTP status; defaults to 400.
    @returns: A DRF ``Response``.
    """
    return Response(
        {
            "error": ORCA_ERROR_MESSAGES[name],
            "error_code": ORCA_ERROR_CODES[name],
            "error_message": name,
        },
        status=status_code,
    )


def orca_not_found(name):
    """``orca_error`` with a 404, for the many not-found cases."""
    return orca_error(name, status.HTTP_404_NOT_FOUND)
