# Orca automation client (reference)

A small Python client for the Orca automation API (`/api/v1/orca/`), meant to
be read and copied rather than installed. It shows the three things
integrations get wrong: deriving an idempotency key from something
reproducible, sending `If-Match` when moving work somebody else may have
moved, and treating a replay as success.

Full API documentation: [`docs/orca-public-api.md`](../../docs/orca-public-api.md).

## Use

```bash
pip install requests
```

```python
from orca_client import OrcaClient, OrcaError

client = OrcaClient("https://plane.example.com", token="plane_api_...")

result = client.create_work_item(
    workspace="acme",
    project_id="8f0d...",
    source="espo-onboarding",
    external_id="client-123:kyc",
    name="Validate customer documents",
    unit="compliance",
    mode="least_loaded",
)

print(result["work_item"]["identifier"])       # ONB-128
print(result["operation"]["replay"])           # False the first time, True on a retry
```

## The key

`idempotency_key(source, external_id, operation, event_id)` hashes exactly
those four things. Nothing else may go in — in particular not a timestamp or a
random value, which would make every retry a new request and produce the
duplicate the API exists to prevent.

If your integration processes events, use the event id: two different events
about the same record are two different calls, and the same event redelivered
is the same call.

## Errors

`OrcaError` carries `status_code` and `error_code`. The ones worth branching on:

| `error_code` | What to do |
| --- | --- |
| `ORG_OPERATION_IN_PROGRESS` (4924) | An identical call is still running. Wait a second and retry the same key. |
| `ORG_IDEMPOTENCY_PAYLOAD_MISMATCH` (4923) | Your key is not a function of the payload. Fix the derivation; do not retry. |
| `ORG_DECISION_STALE` (4920) | Somebody moved the work first. Re-read with `get_by_external` and decide again. |
| `ORG_UNIT_NOT_COVERING_PROJECT` (4916) | The area does not cover that project. A workspace admin has to link them. |
| `ORG_EXECUTOR_NOT_ELIGIBLE` (4918) | The person you named is not in that area, or not in that project. |
| `404` on every route | The API is switched off on that instance (`ORCA_PUBLIC_API_ENABLED`). |
