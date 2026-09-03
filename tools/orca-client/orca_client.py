# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Reference client for the Orca automation API.

Small on purpose: it exists to be read, copied and adapted, not installed.
What it demonstrates is the part integrations get wrong — deriving an
idempotency key from something reproducible, sending If-Match when moving
work, and treating a replay as success rather than as a duplicate.

    from orca_client import OrcaClient

    client = OrcaClient("https://plane.example.com", token="plane_api_...")
    result = client.create_work_item(
        workspace="acme",
        project_id="8f0d...",
        source="espo-onboarding",
        external_id="client-123:kyc",
        name="Validate documents",
        unit="compliance",
    )
    print(result["work_item"]["identifier"], result["operation"]["replay"])
"""

# Python imports
import hashlib
import json
from typing import Any, Optional

# Third party imports
import requests

# Connect and read timeouts. A hung server must not hold an orchestrator's
# worker open: the whole point of the API is that a retry is safe.
TIMEOUT = (5, 30)


def idempotency_key(source: str, external_id: str, operation: str, event_id: str = "") -> str:
    """
    Derive a key the caller can reproduce after a crash.

    @description This is the one thing an integration must get right. The key
    has to be a function of *what the call means*, never of when it was made —
    a key with a timestamp in it makes every retry a new request, which is the
    duplicate the API exists to prevent.
    @param source: The external system's name.
    @param external_id: The record this work belongs to.
    @param operation: create, reassign, transfer.
    @param event_id: The event being processed, when there is one.
    @returns: A stable key, safe to send as a header.
    """
    raw = f"{source}:{external_id}:{operation}:{event_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OrcaError(RuntimeError):
    """A refusal from the API, carrying the code so callers can branch on it."""

    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self.body = body or {}
        self.error_code = self.body.get("error_code")
        self.error_message = self.body.get("error_message", "")
        super().__init__(f"{status_code} {self.error_message or self.body}")


class OrcaClient:
    """A thin wrapper over the endpoints under ``/api/v1/orca/``."""

    def __init__(self, base_url: str, token: str, timeout=TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # --- plumbing ---------------------------------------------------------

    def _headers(self, key: Optional[str] = None, if_match: Optional[str] = None) -> dict:
        headers = {"X-Api-Key": self.token, "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        if if_match:
            headers["If-Match"] = if_match
        return headers

    def _request(self, method: str, path: str, *, key=None, if_match=None, payload=None) -> dict:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(key, if_match),
            data=json.dumps(payload) if payload is not None else None,
            timeout=self.timeout,
        )
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            raise OrcaError(response.status_code, body)

        # A replay is a success: it means an earlier identical call already
        # did the work. Callers that treat it as a duplicate re-do the work.
        if isinstance(body, dict):
            body.setdefault("operation", {})
            body["operation"]["replayed_header"] = response.headers.get("Idempotent-Replay") == "true"
        return body

    # --- reads ------------------------------------------------------------

    def list_units(self, workspace: str) -> dict:
        """@description The areas work can be sent to, and what each does with it."""
        return self._request("GET", f"/api/v1/orca/workspaces/{workspace}/units/")

    def list_queue(self, workspace: str, unit: str, **filters) -> dict:
        """@description What is waiting in one area. Filters: routing_state, project, overdue."""
        query = "&".join(f"{name}={value}" for name, value in filters.items() if value is not None)
        suffix = f"?{query}" if query else ""
        return self._request("GET", f"/api/v1/orca/workspaces/{workspace}/units/{unit}/queue/{suffix}")

    def get_by_external(self, workspace: str, source: str, external_id: str) -> dict:
        """
        @description Ask whether a previous call landed.

        Cheaper and safer than retrying blindly, and the answer is the current
        state rather than the snapshot a replay would return.
        """
        return self._request(
            "GET", f"/api/v1/orca/workspaces/{workspace}/work-items/by-external/{source}/{external_id}/"
        )

    # --- mutations --------------------------------------------------------

    def create_work_item(
        self,
        *,
        workspace: str,
        project_id: str,
        source: str,
        external_id: str,
        name: str,
        unit: str,
        mode: str = "default",
        primary_executor: Optional[str] = None,
        collaborators=(),
        assignment_due_at: Optional[str] = None,
        event_id: str = "",
        **work_item_fields,
    ) -> dict:
        """
        @description Create a work item and give it to an area, in one call.
        @param mode: default, manual, self_claim, least_loaded or explicit.
        @param primary_executor: required when ``mode`` is explicit.
        @returns: The response envelope; ``operation.replay`` says whether an
            earlier identical call had already done it.
        """
        assignment = {"mode": mode}
        if primary_executor:
            assignment["primary_executor"] = primary_executor
        if collaborators:
            assignment["collaborators"] = list(collaborators)

        payload = {
            "external": {"source": source, "id": external_id},
            "work_item": {"name": name, **work_item_fields},
            "responsibility": {"unit": unit, "assignment": assignment},
        }
        if assignment_due_at:
            payload["responsibility"]["assignment_due_at"] = assignment_due_at

        return self._request(
            "POST",
            f"/api/v1/orca/workspaces/{workspace}/projects/{project_id}/work-items/",
            key=idempotency_key(source, external_id, "create", event_id),
            payload=payload,
        )

    def reassign(
        self,
        *,
        workspace: str,
        project_id: str,
        issue_id: str,
        decision_id: str,
        primary_executor: Optional[str] = None,
        return_to_queue: bool = False,
        reason: str = "",
        source: str = "",
        external_id: str = "",
        event_id: str = "",
    ) -> dict:
        """
        @description Move a work item to somebody else, or back to the queue.
        @param decision_id: The decision the caller last saw — sent as
            ``If-Match``. If a person moved the work first, the call is refused
            (412) instead of undoing them.
        """
        payload = {"reason": reason}
        if return_to_queue:
            payload["return_to_queue"] = True
        else:
            payload["primary_executor"] = primary_executor

        return self._request(
            "POST",
            f"/api/v1/orca/workspaces/{workspace}/projects/{project_id}/work-items/{issue_id}/reassign/",
            key=idempotency_key(source or "orca", external_id or issue_id, "reassign", event_id),
            if_match=decision_id,
            payload=payload,
        )

    def transfer(
        self,
        *,
        workspace: str,
        project_id: str,
        issue_id: str,
        unit: str,
        reason: str = "",
        source: str = "",
        external_id: str = "",
        event_id: str = "",
    ) -> dict:
        """@description Hand a work item to a different area."""
        return self._request(
            "POST",
            f"/api/v1/orca/workspaces/{workspace}/projects/{project_id}/work-items/{issue_id}/transfer/",
            key=idempotency_key(source or "orca", external_id or issue_id, "transfer", event_id),
            payload={"unit": unit, "reason": reason},
        )
