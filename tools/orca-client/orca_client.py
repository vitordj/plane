# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Reference client for the Orca automation API (``/api/v1/orca/``).

Small on purpose. It is here to be **read** — by somebody writing the same
thing in PHP or n8n — and to be run by the contract suite, which is how the
examples in ``docs/orca-public-api.md`` stay true. Anything an integration
would want and this does not have (retries with backoff, a connection pool, an
async variant) is deliberately left out: it would obscure the two things that
actually matter.

Those two things:

1. **The idempotency key is derived, not generated.** ``uuid4()`` is the wrong
   answer: a webhook redelivered after your process died gets a fresh key, and
   a fresh key is a fresh operation. The key has to be a function of the event,
   so that the *same event* always produces the *same key* — see
   ``idempotency_key``.
2. **A 4xx is spent.** The key is bound to the body it was first used with. A
   request refused for a bad payload keeps its key; fixing the payload changes
   the request, and a changed request needs a new key. Vary ``event_id`` (or
   pass ``attempt``) when you retry a corrected body.

Usage::

    client = OrcaClient("https://plane.example.com", "plane_api_...", "acme")
    result = client.create_work_item(
        project_id="8f2c...",
        source="espo-onboarding",
        external_id="cliente-123:kyc",
        name="Validate registration documents",
        unit="compliance",
        mode="least_loaded",
    )
    print(result["responsibility"]["primary_executor"])
"""

import hashlib
import json
from urllib.parse import quote

import requests

# Anything an operation could reasonably take, plus room for a slow allocation
# behind an advisory lock. Not None: a client with no timeout is a client that
# hangs forever the one time the server does.
DEFAULT_TIMEOUT = 30


class OrcaApiError(Exception):
    """
    A refusal from the API, with the parts a caller can branch on.

    Attributes:
        status (int): HTTP status.
        error_code (int | None): The stable numeric Orca code, e.g. 4924.
        error_message (str | None): Its symbolic name, e.g.
            ``ORG_IDEMPOTENCY_PAYLOAD_MISMATCH``. Branch on this, not on prose.
        body (dict): The whole response, including extras such as
            ``current_decision_id`` on a stale reassignment.
    """

    def __init__(self, status, body):
        self.status = status
        self.body = body if isinstance(body, dict) else {"error": body}
        self.error_code = self.body.get("error_code")
        self.error_message = self.body.get("error_message")
        super().__init__(f"{status} {self.error_message or self.body.get('error') or 'request failed'}")


class OrcaResult(dict):
    """
    The response body, with one extra fact the body cannot carry.

    Attributes:
        replayed (bool): True when the server answered from its record of an
            earlier identical call (``Idempotent-Replay: true``). The work
            happened once; this call did none of it.
    """

    def __init__(self, payload, replayed=False):
        super().__init__(payload or {})
        self.replayed = replayed


def idempotency_key(source, external_id, operation, event_id, attempt=None):
    """
    @description Derive the key for one event, so a redelivery of that event
    derives the same one.
    @param source: The calling system, matching ``external.source``.
    @param external_id: That system's key for the work.
    @param operation: ``create``, ``reassign`` or ``transfer`` — the same event
        may legitimately drive more than one operation, and each needs its own
        key.
    @param event_id: The identifier of the *event*, not of the attempt. A
        webhook delivery id, an outbox row id, a state-change id. If this
        changes on every retry, the whole mechanism is defeated.
    @param attempt: Include only when you are deliberately sending a
        **different** body — after fixing a payload the server refused. A
        refusal spends its key.
    @returns A 69-character key: ``orca-`` and a sha256 digest.
    """
    parts = [source, external_id, operation, str(event_id)]
    if attempt is not None:
        parts.append(str(attempt))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"orca-{digest}"


class OrcaClient:
    """
    A thin wrapper over the six routes.

    @param base_url: Root of the Plane instance, e.g. ``https://plane.acme.com``.
    @param api_key: An API token of a user who has the rights the call needs;
        the token grants nothing of its own (RFC §7.1).
    @param workspace_slug: The workspace every call is scoped to.
    @param timeout: Seconds, per request.
    """

    def __init__(self, base_url, api_key, workspace_slug, timeout=DEFAULT_TIMEOUT, session=None):
        self.base_url = base_url.rstrip("/")
        self.workspace_slug = workspace_slug
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})

    # --- plumbing ------------------------------------------------------------

    def _url(self, path):
        return f"{self.base_url}/api/v1/orca/workspaces/{quote(self.workspace_slug)}{path}"

    def _request(self, method, path, *, body=None, key=None, if_match=None, params=None):
        headers = {}
        if key:
            headers["Idempotency-Key"] = key
        if if_match:
            headers["If-Match"] = str(if_match)

        response = self.session.request(
            method,
            self._url(path),
            data=json.dumps(body) if body is not None else None,
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text}

        if response.status_code >= 400:
            raise OrcaApiError(response.status_code, payload)
        return OrcaResult(payload, replayed=response.headers.get("Idempotent-Replay") == "true")

    # --- work items ----------------------------------------------------------

    def create_work_item(
        self,
        *,
        project_id,
        source,
        external_id,
        name,
        unit,
        mode="default",
        primary_executor=None,
        collaborators=None,
        assignment_due_at=None,
        description_html=None,
        priority=None,
        state=None,
        labels=None,
        start_date=None,
        target_date=None,
        event_id=None,
        attempt=None,
        idempotency=None,
    ):
        """
        @description Create the work item behind an external key, or find the
        one an earlier call already created, and put an area in charge of it.
        @param event_id: What the key is derived from; defaults to
            ``external_id``, which is right when one external record maps to
            one work item.
        @param idempotency: Pass your own key to bypass the derivation.
        @returns An ``OrcaResult``; check ``.replayed`` to tell a first call
            from a retry.
        """
        work_item = {"name": name}
        for field, value in (
            ("description_html", description_html),
            ("priority", priority),
            ("state", state),
            ("labels", labels),
            ("start_date", start_date),
            ("target_date", target_date),
        ):
            if value is not None:
                work_item[field] = value

        assignment = {"mode": mode}
        if primary_executor is not None:
            assignment["primary_executor"] = primary_executor
        if collaborators:
            assignment["collaborators"] = collaborators

        responsibility = {"unit": unit, "assignment": assignment}
        if assignment_due_at is not None:
            responsibility["assignment_due_at"] = assignment_due_at

        body = {
            "external": {"source": source, "id": external_id},
            "work_item": work_item,
            "responsibility": responsibility,
        }
        key = idempotency or idempotency_key(source, external_id, "create", event_id or external_id, attempt)
        return self._request("POST", f"/projects/{project_id}/work-items/", body=body, key=key)

    def get_by_external(self, source, external_id):
        """@description Current state of the item behind an external key. @returns OrcaResult."""
        path = f"/work-items/by-external/{quote(source, safe='')}/{quote(external_id, safe='')}/"
        return self._request("GET", path)

    def reassign(
        self,
        *,
        project_id,
        issue_id,
        decision_id,
        primary_executor=None,
        return_to_queue=False,
        reason="",
        source=None,
        external_id=None,
        event_id=None,
        idempotency=None,
    ):
        """
        @description Hand the item to somebody else, or put it back in the
        area's queue.
        @param decision_id: The decision the caller believes is current, sent
            as ``If-Match``. Read it from ``decision.id`` of any response. A
            mismatch answers 412 and means somebody acted first — re-read and
            decide again rather than retrying blindly.
        @returns An ``OrcaResult``.
        @raises OrcaApiError: 412 ``ORG_DECISION_STALE`` on a mismatch, whose
            body carries ``current_decision_id``.
        """
        body = {"reason": reason}
        if return_to_queue:
            body["return_to_queue"] = True
        else:
            body["primary_executor"] = primary_executor

        key = idempotency or idempotency_key(
            source or "orca", external_id or str(issue_id), "reassign", event_id or decision_id
        )
        return self._request(
            "POST",
            f"/projects/{project_id}/work-items/{issue_id}/reassign/",
            body=body,
            key=key,
            if_match=decision_id,
        )

    def transfer(
        self, *, project_id, issue_id, unit, reason="", source=None, external_id=None, event_id=None, idempotency=None
    ):
        """@description Move responsibility to another area. @returns OrcaResult."""
        key = idempotency or idempotency_key(
            source or "orca", external_id or str(issue_id), "transfer", event_id or unit
        )
        return self._request(
            "POST",
            f"/projects/{project_id}/work-items/{issue_id}/transfer/",
            body={"unit": unit, "reason": reason},
            key=key,
        )

    # --- reads ---------------------------------------------------------------

    def list_units(self, per_page=100):
        """@description Every active area with its projects and policies. @returns list of dicts."""
        return self._paginate("/units/", {"per_page": per_page})

    def list_queue(self, unit_slug, *, routing_state=None, overdue=None, project=None, per_page=100):
        """
        @description What an area has waiting, overdue first then oldest first.
        @param routing_state: One state, or ``"all"`` to include assigned work.
        @returns list of dicts.
        """
        params = {"per_page": per_page}
        if routing_state:
            params["routing_state"] = routing_state
        if overdue is not None:
            params["overdue"] = "true" if overdue else "false"
        if project:
            params["project"] = project
        return self._paginate(f"/units/{quote(unit_slug)}/queue/", params)

    def _paginate(self, path, params):
        """@description Follow the cursor to the end. @returns every row."""
        rows, cursor = [], None
        while True:
            page = self._request("GET", path, params={**params, **({"cursor": cursor} if cursor else {})})
            rows.extend(page.get("results", []))
            if not page.get("next_page_results"):
                return rows
            cursor = page.get("next_cursor")
