# Orca automation API

`/api/v1/orca/` is how a program puts work into Plane and lets an **area**
decide who does it. It exists because the native work-item API answers a
different question: it creates an item and takes a list of assignees, which
means the calling system has to know who is available, who is on holiday and
who is already overloaded. It is not in a position to know any of that, and
the moment it guesses, the guess is wrong.

This API takes the other half of the decision away from the caller. You say
_which area is responsible_; the area's own policy decides _who_.

- Base: `https://<your-plane>/api/v1/orca/`
- Authentication: `X-Api-Key`, an ordinary Plane API token
- Everything is JSON; every mutation needs `Idempotency-Key`

> **This API ships switched off.** It answers `404` until an instance
> administrator sets `ORCA_PUBLIC_API_ENABLED=1` (and `ORCA_ORG_UNITS_ENABLED=1`,
> which gates the organizational layer as a whole). A 404 with
> `"error_message": "ORG_PUBLIC_API_DISABLED"` means the instance has it off —
> as opposed to a bare 404, which means you have the URL wrong.
>
> **When it gets switched on in production** is Gate 2-minimum of the
> execution plan, not this document's decision. The gate exists because an
> automation that files work into an area is only useful once somebody runs
> that area's queue: before the coordinator role and the queue's own screens,
> an item the API queued had nowhere to be seen. Both now exist (Phase 2), so
> what the gate still asks for is an area with a named coordinator, that
> coordinator doing the round trip in staging — see the queue, get the
> `allocation_failed` alert, assign by hand, hand it back — and the runbook
> line for switching the API off again. See
> [`docs/plans/orca-work-management/02-queue-and-coordinator.md`](./plans/orca-work-management/02-queue-and-coordinator.md).

---

## The one thing to get right: idempotency keys

Every mutation requires an `Idempotency-Key` header, and the key must be
**derived from the event**, never generated fresh per attempt:

```python
key = "orca-" + sha256(f"{source}|{external_id}|{operation}|{event_id}").hexdigest()
```

`uuid4()` is the wrong answer. A webhook redelivered after your worker was
killed gets a new UUID, and a new key is a new operation — you get two work
items for one event, which is the failure this whole mechanism exists to
prevent. The key must be a function of the _event_, so the same event always
produces the same key.

What the server does with it (RFC §6.7):

| Situation                                    | Answer                                                                            |
| -------------------------------------------- | --------------------------------------------------------------------------------- |
| First call                                   | The operation runs. `201` on creation, `200` on the others.                       |
| Same key, **same** body, first call finished | The recorded response, with header `Idempotent-Replay: true`. Nothing runs again. |
| Same key, **different** body                 | `409 ORG_IDEMPOTENCY_PAYLOAD_MISMATCH`. Nothing runs.                             |
| Same key, first call still running           | `409 ORG_OPERATION_IN_PROGRESS`. Back off and retry.                              |
| Same key, first call died mid-flight         | After 60 seconds the key is taken over and the operation runs.                    |

Two consequences worth internalizing:

**A replay answers the original, not the present.** If somebody reassigned the
item in your interface between your first call and your retry, the retry still
reports the _first_ allocation. That is deliberate: a retry must not read as
though it changed something. When you want current state, do a `GET`.

**A 4xx spends the key.** A request refused for a bad payload is recorded as a
failed operation, and retrying that key replays the same failure — including
its status. Fixing the payload changes the request, and a changed request needs
a **new key**. Derive a new one (vary `event_id`, or add an attempt counter)
when you resend a corrected body.

---

## Creating work

```http
POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/
```

One call finds-or-creates the work item behind _your_ key, makes an area
responsible, runs that area's policy, and records which call caused all of it.
Doing it as four calls would mean four chances to half-succeed, and a work item
nobody owns is worse than no work item at all.

```bash
curl -sS -X POST \
  "https://plane.example.com/api/v1/orca/workspaces/acme/projects/8f2c.../work-items/" \
  -H "X-Api-Key: $PLANE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: orca-9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08" \
  -d '{
    "external": { "source": "espo-onboarding", "id": "cliente-123:kyc" },
    "work_item": {
      "name": "Validate registration documents",
      "description_html": "<p>Documents attached in the CRM.</p>",
      "priority": "high",
      "target_date": "2026-09-30"
    },
    "responsibility": {
      "unit": "compliance",
      "assignment": { "mode": "least_loaded" },
      "assignment_due_at": "2026-09-25T12:00:00Z"
    }
  }'
```

```json
{
  "work_item": {
    "id": "0f0e...",
    "sequence_id": 128,
    "identifier": "ONB-128",
    "url": "https://plane.example.com/acme/browse/ONB-128/"
  },
  "binding": { "source": "espo-onboarding", "id": "cliente-123:kyc", "created": true },
  "responsibility": {
    "unit": { "id": "6a1b...", "slug": "compliance" },
    "routing_state": "assigned",
    "queue_reason": "",
    "primary_executor": { "id": "3c7d...", "email": "maria@acme.com", "display_name": "Maria" },
    "assignment_due_at": "2026-09-25T12:00:00Z"
  },
  "decision": {
    "id": "b2f0...",
    "requested_mode": "least_loaded",
    "effective_mode": "least_loaded",
    "policy_source": "unit_project",
    "policy_version": 3,
    "algorithm_version": "lb-1",
    "outcome": "assigned"
  },
  "operation": { "idempotency_key": "orca-9f86...", "replay": false }
}
```

### `external` — your key, and what it buys you

`source` and `id` together are yours to choose, and they are what makes a
redelivered event find the item it already created rather than making a second
one. Keep them stable for the life of the work: `"cliente-123:kyc"` is a good
id, `"2026-09-05T10:32:11"` is not.

One external key maps to exactly one work item and vice versa. Sending a key
that is already bound to an item **in another project** answers
`409 ORG_EXTERNAL_BINDING_CONFLICT`, with the offending `issue_id` in the body —
because silently moving work between projects is not something an API should
do behind your back.

**This route is safe to call on every webhook.** If the key already names a
work item and the `unit` you send is the area that already owns it, the
response reports the current state and **changes nothing** — no re-ranking, no
second decision, `"created": false`. That matters because each webhook is a
different event and therefore derives a different idempotency key: without
this, an item assigned under `least_loaded` would be handed to somebody else
every time the source record was touched. To actually move work, use
`reassign/` or `transfer/`. Sending a _different_ `unit` is a real instruction
and does transfer the item.

### `work_item` — ordinary Plane content

`name` is required. Also accepted: `description_html`, `state`, `priority`,
`labels`, `start_date`, `target_date`, `parent`, `estimate_point`.

**`assignees` is not accepted** — `400 ORG_ASSIGNEES_NOT_ALLOWED_HERE`. The
area decides who does the work. To name a person, use
`assignment.mode = "explicit"` below, which validates that the person is
actually able to hold work of that area on that project.

Any other unknown key is refused as well, with the accepted keys listed in the
error. A silently ignored field is a bug you find weeks later.

### `responsibility` — the part this API is for

`unit` is the area's slug (read them from `GET /units/`).

`assignment.mode` is one of:

| Mode           | What happens                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------------------------- |
| `default`      | Whatever the area's policy says. Use this unless you have a reason not to.                                    |
| `least_loaded` | The area's ranking picks the least loaded eligible member and assigns them.                                   |
| `manual`       | The item waits in the area's queue for a coordinator.                                                         |
| `self_claim`   | The item waits for a member of the area to claim it.                                                          |
| `explicit`     | You name the person: `"primary_executor": "<user-uuid>"`, optionally with `"collaborators": ["<user-uuid>"]`. |

A mode the area's policy forbids is **refused**, never quietly downgraded
(`400 ORG_ASSIGNMENT_MODE_NOT_ALLOWED`). A caller that asked for
`least_loaded` and silently got `manual` would believe the work was assigned
while it sat in a queue.

`assignment_due_at` is when somebody must be _on_ the item — a deadline for
the allocation, not for the work.

### What "assigned" and "queued" mean in the answer

`responsibility.routing_state` tells you where the item stands:

| State               | Meaning                                                  |
| ------------------- | -------------------------------------------------------- |
| `assigned`          | Somebody is on it; `primary_executor` says who.          |
| `queued`            | Waiting for a person. `queue_reason` says what for.      |
| `allocation_failed` | The area tried and found nobody eligible. Needs a human. |
| `suspended`         | Parked.                                                  |

`queued` is a **success**, not a failure: the area accepted responsibility and
the item is in its inbox. `decision` records why it went the way it did, which
is what makes "why does this person have this?" answerable a week later.

---

## Reading state back

```http
GET /api/v1/orca/workspaces/{slug}/work-items/by-external/{source}/{id}/
```

Same envelope, current state, `operation: null`. This is what you call when
you want the present rather than the record of your own call — percent-encode
`source` and `id`.

```bash
curl -sS "https://plane.example.com/api/v1/orca/workspaces/acme/work-items/by-external/espo-onboarding/cliente-123%3Akyc/" \
  -H "X-Api-Key: $PLANE_API_KEY"
```

```http
GET /api/v1/orca/workspaces/{slug}/units/
```

Every active area, the projects it covers, and the resolved policy for each —
what _would_ happen, not which rows exist. Read it at startup so you never
guess at a slug or discover a forbidden mode by having a request refused.

```http
GET /api/v1/orca/workspaces/{slug}/units/{unit_slug}/queue/
```

What the area has waiting, **overdue first, then oldest first**. Filters:
`routing_state` (a state, or `all` to include assigned work), `overdue=true|false`,
`project=<uuid>`. Paginated like the rest of the v1 API (`per_page`, `cursor`).

Visible to members of the area and to workspace admins — these rows carry the
titles of real work.

---

## Changing who holds it

```http
POST .../work-items/{issue_id}/reassign/
```

Requires **`If-Match`** carrying the `decision.id` you believe is current, plus
the usual `Idempotency-Key`. Body is exactly one of:

```json
{ "primary_executor": "<user-uuid>", "reason": "on leave" }
{ "return_to_queue": true, "reason": "wrong specialty" }
```

```bash
curl -sS -X POST \
  ".../work-items/0f0e.../reassign/" \
  -H "X-Api-Key: $PLANE_API_KEY" \
  -H "Idempotency-Key: orca-..." \
  -H 'If-Match: b2f0...' \
  -H "Content-Type: application/json" \
  -d '{"primary_executor": "7d21...", "reason": "on leave"}'
```

Without `If-Match`: `428 ORG_IF_MATCH_REQUIRED`. With a decision that is no
longer current: `412 ORG_DECISION_STALE`, whose body carries
`current_decision_id` so you can re-read and decide again. **Do not retry a
412 blindly** — somebody acted between your read and your write, and that is
information, not an obstacle.

The previous executor keeps their assignee row on the item. Plane shows
assignees to everyone, and quietly detaching a person is a human's call.

```http
POST .../work-items/{issue_id}/transfer/
```

Body `{"unit": "legal", "reason": "..."}`, plus `Idempotency-Key`. No
`If-Match`: "this work belongs to Legal" is not a contested edit of one
decision.

If the current executor is not a member of the receiving area, the item goes
back to that area's queue and they stay on it as a collaborator. If the
receiving area does not cover the project, the transfer is refused
(`400 ORG_UNIT_NOT_COVERING_PROJECT`).

---

## Errors

Every failure carries three fields: `error` (English prose, for logs and
humans), `error_code` (a stable number), and `error_message` (its symbolic
name). **Branch on `error_message` or `error_code`, never on the prose** — the
prose may be reworded, the codes are permanent once shipped.

```json
{
  "error": "This Idempotency-Key was already used with a different payload",
  "error_code": 4924,
  "error_message": "ORG_IDEMPOTENCY_PAYLOAD_MISMATCH"
}
```

| Code | Name                               | HTTP    | When                                                      |
| ---- | ---------------------------------- | ------- | --------------------------------------------------------- |
| 4900 | `ORG_UNIT_NOT_FOUND`               | 404     | The area in the URL does not exist here                   |
| 4906 | `ORG_UNIT_NOT_IN_WORKSPACE`        | 400     | The `unit` in the body does not exist, or is retired      |
| 4911 | `ORG_WORK_ITEM_NOT_FOUND`          | 404     | No such item in this project, or no binding for that key  |
| 4912 | `ORG_WORK_ITEM_HAS_NO_UNIT`        | 400     | The item exists but no area is responsible for it         |
| 4916 | `ORG_UNIT_NOT_COVERING_PROJECT`    | 400     | The area is not linked to that project                    |
| 4917 | `ORG_ASSIGNMENT_MODE_NOT_ALLOWED`  | 400     | The area's policy forbids the mode you asked for          |
| 4918 | `ORG_EXECUTOR_NOT_ELIGIBLE`        | 400     | That person cannot hold work of this area on this project |
| 4919 | `ORG_WORK_ITEM_ALREADY_CLAIMED`    | 409     | Somebody took it first                                    |
| 4920 | `ORG_DECISION_STALE`               | **412** | Your `If-Match` is not the current decision               |
| 4921 | `ORG_INVALID_ROUTING_TRANSITION`   | 400     | The item cannot move that way from where it is            |
| 4922 | `ORG_PUBLIC_API_DISABLED`          | 404     | This instance has the automation API switched off         |
| 4923 | `ORG_IDEMPOTENCY_KEY_REQUIRED`     | 400     | Header missing, empty, or over 255 characters             |
| 4924 | `ORG_IDEMPOTENCY_PAYLOAD_MISMATCH` | 409     | Key reused with a different body                          |
| 4925 | `ORG_OPERATION_IN_PROGRESS`        | 409     | The first call with this key is still running             |
| 4926 | `ORG_EXTERNAL_BINDING_CONFLICT`    | 409     | That external key belongs to another work item            |
| 4927 | `ORG_ASSIGNEES_NOT_ALLOWED_HERE`   | 400     | `assignees` in the `work_item` block                      |
| 4928 | `ORG_IF_MATCH_REQUIRED`            | 428     | `reassign` without `If-Match`                             |
| 4929 | `ORG_PROCESS_PROJECTION_DISABLED`  | 400     | A `process` block (Phase 4)                               |
| 4931 | `ORG_INTERNAL_ERROR`               | 500     | The operation failed and was recorded as failed           |

A malformed body that is not one of these answers `400` with
`"error_message": "VALIDATION_ERROR"` and a `detail` object naming the
offending fields.

**On 412 and 409, `ORG_DECISION_STALE` differs from the interface.** Plane's
own web app receives `409` for the same condition. The public API answers
`412`, because over HTTP that is what a failed precondition header means.

### Permissions and limits

The token grants nothing of its own — the effective permission is that of the
**user the token belongs to** (RFC §7.1):

| Route                                        | Requires                                      |
| -------------------------------------------- | --------------------------------------------- |
| `POST work-items/`, `reassign/`, `transfer/` | Active project member, role Member or Admin   |
| `GET by-external/`                           | Active member of the item's project, any role |
| `GET units/`                                 | Active workspace member                       |
| `GET units/{slug}/queue/`                    | Member of that area, or workspace Admin       |

Rate limit: `ORCA_PUBLIC_API_RATE_LIMIT`, default `300/minute`, **per token**,
answering `429` with `{"error_code": 5900, "error_message": "RATE_LIMIT_EXCEEDED"}`.
The limit is read at process start, so changing it needs a restart.

---

## Reference client

[`tools/orca-client/`](../tools/orca-client/README.md) is a small Python
client that does the above, including the key derivation. The contract suite
runs against it, so its behaviour and these examples stay in step.

```python
from orca_client import OrcaClient

client = OrcaClient("https://plane.example.com", api_key, "acme")
result = client.create_work_item(
    project_id="8f2c...",
    source="espo-onboarding",
    external_id="cliente-123:kyc",
    name="Validate registration documents",
    unit="compliance",
    mode="least_loaded",
)
if result.replayed:
    print("already done; nothing ran")
```

---

## Not here yet

| Wanted                                          | Where it is                                                                                                                             |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `process` block (template, instance, step)      | Phase 4 — refused with `ORG_PROCESS_PROJECTION_DISABLED` today                                                                          |
| `completion_due_at`                             | Phase 4, with the service-level record that stores it. Refused rather than accepted and dropped                                         |
| `POST .../complete/`                            | Phase 4                                                                                                                                 |
| Coordinator access to another area's queue      | Shipped in Phase 2, for the app's own API (`/api/orca/`); this namespace still scopes a queue read to areas the token's user belongs to |
| Availability and holidays affecting the ranking | Phase 3                                                                                                                                 |

The full design, including the invariants these endpoints preserve, is in
[`docs/orca-work-management-rfc.md`](./orca-work-management-rfc.md).
