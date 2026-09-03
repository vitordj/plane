# Orca automation API

`/api/v1/orca/` is how a system outside Plane creates work and hands it to the
area that owns it. One call creates the work item, records which external
record it belongs to, and lets the area's policy decide who does it.

Reference client: [`tools/orca-client/`](../tools/orca-client/README.md).
Design and rationale: [`docs/orca-work-management-rfc.md`](./orca-work-management-rfc.md).

> **Off by default.** The API exists only where `ORCA_PUBLIC_API_ENABLED=1`
> (and the organizational layer is on). Everywhere else every route answers
> `404`, deliberately: a workspace that has not enabled this should not be
> able to discover the endpoints, and switching it off during an incident
> should make it gone rather than merely refusing.

> **Cleared for production from Gate 2-minimum.** Until the coordinator
> screens existed, work this API queued had nowhere to be seen: an area with
> `manual` policy would accept a work item and nobody would ever look at it.
> That is why the flag stayed `0` in production while Phase 1 shipped. With
> the area queue, the SLA alert and the coordinator role in place, turning it
> on is a deployment decision rather than a risk — the checklist is in
> [`docs/plans/orca-work-management/02-queue-and-coordinator.md`](./plans/orca-work-management/02-queue-and-coordinator.md).

---

## Authentication

An API key, in the `X-Api-Key` header — the same key the rest of `/api/v1/`
uses, made in **Workspace settings → API tokens**.

A token acts as the person who made it. It can never do more than they can: if
they are a Guest in a project, the token is a Guest there. Creating work with
an area needs Member or Admin **in that project**.

```bash
curl -H "X-Api-Key: $PLANE_API_KEY" \
  https://plane.example.com/api/v1/orca/workspaces/acme/units/
```

## Headers

| Header | When | What it does |
| --- | --- | --- |
| `X-Api-Key` | always | Authentication. |
| `Idempotency-Key` | every mutation | Makes the call safe to repeat. Required; without it, `400 ORG_IDEMPOTENCY_KEY_REQUIRED`. |
| `If-Match` | `reassign` | The id of the decision you are acting on. Without it, `428`; if the work has moved since, `412`. |
| `Idempotent-Replay: true` | on responses | This answer came from an earlier identical call. Nothing was done twice. |

### Deriving the idempotency key

The key must be a function of **what the call means**, and of nothing else:

```
key = sha256(f"{source}:{external_id}:{operation}:{event_id}")
```

A key containing a timestamp, a random value or a retry counter makes every
retry a new request — which produces exactly the duplicate the key exists to
prevent. If the same key arrives with a different payload, the API refuses
(`409 ORG_IDEMPOTENCY_PAYLOAD_MISMATCH`) rather than guessing.

What a repeat gets back is the **original** answer, not the current state. If a
coordinator reassigned the work between your first call and your retry, the
replay still shows the first answer; ask `by-external` for the current state.

---

## Endpoints

### List areas

```bash
curl -H "X-Api-Key: $PLANE_API_KEY" \
  "https://plane.example.com/api/v1/orca/workspaces/acme/units/"
```

Each area comes with the projects it covers and the policy that applies there
— `default_mode` is what happens to work you send without asking for a mode,
and `allowed_modes` is what you may ask for.

### One area's queue

```bash
curl -H "X-Api-Key: $PLANE_API_KEY" \
  "https://plane.example.com/api/v1/orca/workspaces/acme/units/compliance/queue/?overdue=true"
```

Filters: `routing_state`, `project`, `overdue=true`. Rows carry `age_seconds`
and `assignment_overdue`, which is what an orchestrator needs to tell "moving
slowly" from "stuck". Readable by members of the area and workspace admins;
everyone else gets `404`.

### Create a work item with an area responsible

```bash
curl -X POST \
  -H "X-Api-Key: $PLANE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(printf 'espo:client-123:kyc:create:evt-9' | sha256sum | cut -d' ' -f1)" \
  -d '{
    "external": { "source": "espo-onboarding", "id": "client-123:kyc" },
    "work_item": {
      "name": "Validate customer documents",
      "description_html": "<p>Check the ID and the proof of address.</p>",
      "priority": "high",
      "target_date": "2026-09-10"
    },
    "responsibility": {
      "unit": "compliance",
      "assignment": { "mode": "default" },
      "assignment_due_at": "2026-09-04T12:00:00Z"
    }
  }' \
  "https://plane.example.com/api/v1/orca/workspaces/acme/projects/$PROJECT_ID/work-items/"
```

`201` the first time, `200` with `Idempotent-Replay: true` on a repeat.

```json
{
  "work_item": { "id": "…", "sequence_id": 128, "identifier": "ONB-128", "name": "Validate customer documents", "state": "…" },
  "binding": { "source": "espo-onboarding", "id": "client-123:kyc", "created": true },
  "responsibility": {
    "unit": { "id": "…", "slug": "compliance" },
    "routing_state": "assigned",
    "queue_reason": "",
    "primary_executor": { "id": "…", "email": "maria@acme.com", "display_name": "Maria" },
    "assignment_due_at": "2026-09-04T12:00:00Z"
  },
  "decision": {
    "id": "…", "requested_mode": "default", "effective_mode": "least_loaded",
    "policy_source": "unit_project", "policy_version": 3,
    "algorithm_version": "lb-1", "outcome": "assigned"
  },
  "operation": { "idempotency_key": "…", "replay": false }
}
```

**Assignment modes.** `assignment.mode` accepts:

| Mode | Result |
| --- | --- |
| `default` | Whatever the area's policy says. Use this unless you have a reason not to. |
| `manual` | Queued for a coordinator to assign. |
| `self_claim` | Queued for any eligible member of the area to take. |
| `least_loaded` | Given to whoever currently carries the least open work. |
| `explicit` | Given to `assignment.primary_executor`. Requires that person to be an eligible member of the area on that project. |

`assignment.collaborators` adds people alongside the executor. They see the
work item and carry none of the responsibility — and none of the load, so
adding somebody as a collaborator does not make the ranking treat them as busy.

A mode the area does not allow is **refused**, never quietly downgraded: an
automation that asked for automatic allocation and silently got a manual queue
looks like it worked while the work sits there.

**Assignees are not yours to set.** `work_item.assignees` is refused
(`400 ORG_ASSIGNEES_NOT_ALLOWED_HERE`). Who does the work is the area's
decision, and it is recorded as one.

### Read one back

```bash
curl -H "X-Api-Key: $PLANE_API_KEY" \
  "https://plane.example.com/api/v1/orca/workspaces/acme/work-items/by-external/espo-onboarding/client-123:kyc/"
```

Same envelope, current state. This is the cheap way to find out whether a call
landed — cheaper and safer than retrying blindly.

### Reassign, or send back to the queue

```bash
curl -X POST \
  -H "X-Api-Key: $PLANE_API_KEY" \
  -H "Idempotency-Key: $KEY" \
  -H "If-Match: $DECISION_ID" \
  -H "Content-Type: application/json" \
  -d '{ "primary_executor": "…", "reason": "specialist review" }' \
  ".../work-items/$ISSUE_ID/reassign/"
```

or `{"return_to_queue": true, "reason": "…"}`.

`If-Match` carries the decision id you last saw. If somebody moved the work
first, you get `412 ORG_DECISION_STALE` instead of silently undoing them —
re-read, then decide again. The previous executor stays on the work item as a
collaborator: they have context somebody will want.

### Transfer to another area

```bash
curl -X POST -H "X-Api-Key: $PLANE_API_KEY" -H "Idempotency-Key: $KEY" \
  -H "Content-Type: application/json" -d '{ "unit": "legal", "reason": "contract question" }' \
  ".../work-items/$ISSUE_ID/transfer/"
```

The destination has to cover the project. The work is re-allocated under the
new area's policy, and if the current executor does not belong to it, the work
goes back to that area's queue rather than staying with somebody the area
cannot direct.

---

## Errors

Every refusal answers with the same envelope:

```json
{ "error": "…", "error_code": 4916, "error_message": "ORG_UNIT_NOT_COVERING_PROJECT" }
```

| Code | HTTP | Meaning | What to do |
| --- | --- | --- | --- |
| `ORG_UNIT_NOT_COVERING_PROJECT` | 400 | The area does not cover that project | A workspace admin links them; do not retry |
| `ORG_ASSIGNMENT_MODE_NOT_ALLOWED` | 400 | The area does not allow that mode | Ask for one in `allowed_modes` |
| `ORG_EXECUTOR_NOT_ELIGIBLE` | 400 | That person is not in the area, or not in the project | Pick somebody else, or fix the membership |
| `ORG_ASSIGNEES_NOT_ALLOWED_HERE` | 400 | `assignees` on the work item block | Use `responsibility.assignment` |
| `ORG_IDEMPOTENCY_KEY_REQUIRED` | 400 | No `Idempotency-Key` | Send one; see above |
| `ORG_PROCESS_PROJECTION_DISABLED` | 400 | A `process` block, and the instance has it off | Phase 4 feature |
| `ORG_WORK_ITEM_ALREADY_CLAIMED` | 409 | Somebody took it first | Re-read; the work has an owner |
| `ORG_IDEMPOTENCY_PAYLOAD_MISMATCH` | 409 | Same key, different payload | Fix the key derivation; do not retry |
| `ORG_OPERATION_IN_PROGRESS` | 409 | An identical call is still running | Wait a second, retry the same key |
| `ORG_EXTERNAL_BINDING_CONFLICT` | 409 | That reference already points at a work item elsewhere | Two integrations share a reference; fix the namespace |
| `ORG_DECISION_STALE` | 412 | The work moved since you read it | Re-read and decide again |
| `ORG_IF_MATCH_REQUIRED` | 428 | No `If-Match` on a reassignment | Send the decision id you saw |
| — | 429 | Rate limit (per token, default 300/minute) | Back off |
| — | 404 on every route | The API is switched off here | Ask an admin about `ORCA_PUBLIC_API_ENABLED` |

## Rate limit

Per API token, `ORCA_PUBLIC_API_RATE_LIMIT`, 300/minute by default. Keyed on
the token rather than the address, so one integration's traffic never throttles
another's.
