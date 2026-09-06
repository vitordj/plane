# Orca automation client

A small Python client for the Orca automation API (`/api/v1/orca/`), and the
worked example the [API guide](../../docs/orca-public-api.md) points at.

It is deliberately minimal. Its job is to be read by somebody writing the same
integration in another language, and to be run by the contract suite — which is
what keeps the guide's examples honest. It has no retry policy, no connection
pooling and no async variant, because those would obscure the two things that
actually matter.

## Install

```bash
pip install requests
```

## Use

```python
from orca_client import OrcaClient, OrcaApiError

client = OrcaClient(
    base_url="https://plane.example.com",
    api_key="plane_api_...",
    workspace_slug="acme",
)

result = client.create_work_item(
    project_id="8f2c...",
    source="espo-onboarding",
    external_id="cliente-123:kyc",
    name="Validate registration documents",
    unit="compliance",
    mode="least_loaded",
)

print(result["responsibility"]["routing_state"])   # assigned | queued | allocation_failed
print(result.replayed)                             # True when this call did no work
```

## The two things that matter

**The idempotency key is derived from the event, not generated.**
`idempotency_key(source, external_id, operation, event_id)` hashes those four
into a stable 69-character key. Pass `event_id` from something that identifies
the *event* — a webhook delivery id, an outbox row — not the attempt. If it
changes on every retry, the retry is a new operation and you get two work
items for one event.

**A refusal spends the key.** The key is bound to the body it was first used
with; retrying it replays the same refusal, status included. After fixing a
payload the server rejected, derive a new key (`attempt=2`).

## What it covers

| Method | Route |
| --- | --- |
| `create_work_item` | `POST .../projects/{id}/work-items/` |
| `get_by_external` | `GET .../work-items/by-external/{source}/{id}/` |
| `reassign` | `POST .../work-items/{id}/reassign/` (sends `If-Match`) |
| `transfer` | `POST .../work-items/{id}/transfer/` |
| `list_units` | `GET .../units/` (follows the cursor) |
| `list_queue` | `GET .../units/{slug}/queue/` (follows the cursor) |

Failures raise `OrcaApiError`, carrying `status`, `error_code`,
`error_message` and the whole `body`. Branch on `error_message`, never on the
prose:

```python
try:
    client.reassign(project_id=..., issue_id=..., decision_id=known, primary_executor=...)
except OrcaApiError as exc:
    if exc.error_message == "ORG_DECISION_STALE":
        current = exc.body["current_decision_id"]   # somebody acted first; re-read
```
