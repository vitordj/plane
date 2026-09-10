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
> **Production.** The code conditions for turning it on (R1.A5, R1.A6, the
> Fase 2 surface, the kill-switch runbook below) are in the tree. Gate
> 2-minimum still requires a staging deploy and a pilot area before
> `ORCA_PUBLIC_API_ENABLED=1` is set in production. Until then the switch stays
> off.

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

**A key is remembered for 30 days**, not forever. Receipts older than
`ORCA_AUTOMATION_OPERATION_RETENTION_DAYS` are expired daily, so a retry
arriving after that is a new operation rather than a replay. This is not a
window you need to design around: a creation still resolves through your
`external` key to the same work item and does not re-run the allocation — you
get the same `201`, without the `Idempotent-Replay` header and describing the
item's present state rather than the original snapshot — and a reassignment
retried with its original `If-Match` is refused as stale. A transfer is the one operation that would genuinely run again —
which matters only if something in your system can retry a call a month late.

**A key is unique per API token, not across the workspace.** Two integrations
sharing a workspace can reuse the same `Idempotency-Key` without one burning
the other's namespace (R1.A12).

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
    "algorithm_version": "lb-2",
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

| Route                                        | Requires                                                                   |
| -------------------------------------------- | -------------------------------------------------------------------------- |
| `POST work-items/`, `reassign/`, `transfer/` | Active project member, role Member or Admin                                |
| `GET by-external/`                           | Active member of the item's project, any role                              |
| `GET units/`                                 | Active workspace member; non-admin tokens only see projects they belong to |
| `GET units/{slug}/queue/`                    | Member of that area, or workspace Admin                                    |

Rate limit: `ORCA_PUBLIC_API_RATE_LIMIT`, default `300/minute`, **per token**,
answering `429` with `{"error_code": 5900, "error_message": "RATE_LIMIT_EXCEEDED"}`.
The limit is read at process start, so changing it needs a restart.

---

## Runbook: switching the API off

This is the operator's half of the Gate 2-minimum criterion "the automation
API can be switched off". It answers one question — _what happens to work
already in flight when somebody flips the switch_ — because that is the part
an operator cannot infer from the switch's name.

Every claim below names the file it was read from, so a reader can check it
rather than believe it.

### The switch, and what it takes to move it

`ORCA_PUBLIC_API_ENABLED` is read **once, when the process starts**: settings
resolve it through `env_flag` at import time
(`apps/api/plane/settings/common.py:609`, `apps/api/plane/utils/orca_env.py:57`).
Changing the variable in the environment does nothing to a running container.
Turning the API off is therefore two steps, and the second is the one that
matters:

```bash
# 1. Set it in the deployment's environment
ORCA_PUBLIC_API_ENABLED=0

# 2. Restart the services that carry it. docker-compose-orca.yml forwards the
#    variable to api, worker, beat-worker and migrator (lines 108, 162, 210,
#    258); only `api` serves /api/v1/orca/, but the others read the same flag
#    and must not disagree with it.
docker compose -f docker-compose-orca.yml up -d --no-deps api worker beat-worker
```

Confirm the value the app actually has, from inside the app rather than from
the shell that set it:

```bash
curl -s -H "Cookie: <session>" \
  https://plane.example.com/api/orca/workspaces/<slug>/config/
# {"organizational_units_enabled": true, "public_api_enabled": false}
```

That endpoint is deliberately outside both kill switches
(`apps/api/plane/app/views/organizational_unit.py:127`), so it keeps answering
when the API it reports on does not. `GET /api/orca/build-info/` tells you
which commit the container is running, which is the other half of "is this the
process I just restarted".

### What the API does while it is off

Every route under `/api/v1/orca/` answers **404** with a coded body:

```json
{
  "error": "The Orca public automation API is disabled on this instance",
  "error_code": 4922,
  "error_message": "ORG_PUBLIC_API_DISABLED"
}
```

All six routes (`apps/api/plane/api/urls/orca.py`) derive from
`OrcaPublicBaseAPIView`, and the refusal is raised in
`OrcaPublicApiFeatureMixin.initial` **after** `super().initial()` runs
(`apps/api/plane/api/views/orca/base.py`) — that is, after authentication and
the throttle (R1.A18). An anonymous caller is `401`'d and learns nothing about
the switch. A caller with a valid token then gets the coded 404
`ORG_PUBLIC_API_DISABLED`, and that call still counts against their budget.

`ORCA_PUBLIC_API_ENABLED` also requires `ORCA_ORG_UNITS_ENABLED`
(`apps/api/plane/app/services/orca/feature_flags.py:50`): turning the
organizational layer off shuts the automation API too, but not the other way
round.

### What happens to a receipt that was in progress

An `AutomationOperation` row is `in_progress` only for the duration of one
request: `begin_operation` opens it and the endpoint closes it with
`complete`/`fail`, and an unhandled exception inside the block records
`ORG_INTERNAL_ERROR` before re-raising
(`apps/api/plane/app/services/orca/automation_operation.py:278-306`).

**Flipping the switch cannot by itself strand a receipt.** The 404 is raised in
`initial()`, before `post()` reaches `begin_operation`, so a call refused by
the switch opens no receipt at all.

What _can_ strand one is the restart in step 2. A request already inside the
block is either allowed to finish — and writes its outcome normally — or the
process dies without unwinding (`SIGKILL`, an OOM kill, a stop timeout that
expires), in which case the `except` clause never runs and the row stays
`in_progress` with `completed_at` NULL. Prefer a graceful stop for exactly this
reason. To see whether it happened:

```sql
SELECT id, workspace_id, operation_type, idempotency_key, created_at
FROM automation_operations
WHERE status = 'in_progress'
ORDER BY created_at DESC;
```

Such a row is not harmful while the API is off: nothing can reach it, and it
holds no lock. It is a spent key waiting to be resolved.

### How re-enabling resolves it (RFC §6.7)

Set `ORCA_PUBLIC_API_ENABLED=1` and restart, as above. Nothing sweeps the
stranded receipts in the background — **the client's own retry is what
resolves them**, through `start_operation` → `_existing`
(`automation_operation.py:168–228`). For a retry carrying the same
`Idempotency-Key`:

| The receipt is                   | The retry sends       | What happens                                                                                                                                                                                         |
| -------------------------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `succeeded` or `failed`          | the same payload      | Replay: the recorded body and status come back, with `Idempotent-Replay: true`. Nothing runs.                                                                                                        |
| `in_progress`, older than 60 s   | the same payload      | **Resumed.** `_resume` restarts the sixty-second clock and the operation runs again from the beginning. This is the case a restart leaves behind — every stranded receipt is far past sixty seconds. |
| `in_progress`, younger than 60 s | the same payload      | `409 ORG_OPERATION_IN_PROGRESS` — a real concurrent call. Back off and retry.                                                                                                                        |
| anything                         | a _different_ payload | `409 ORG_IDEMPOTENCY_PAYLOAD_MISMATCH`, checked before status (`_existing`). A client that corrects its body needs a new key.                                                                        |

"Runs again from the beginning" is safe to different degrees depending on what
the operation was, and this is the same analysis the retention window rests on:

- **Creation** (`POST work-items/`) is find-or-create on the external binding,
  and `_place` returns early when the area asking is the area that already owns
  the item (`apps/api/plane/api/views/orca/work_items.py:256–284`). The retry
  reports the item's current state; it does not create a second item and does
  not re-run the allocation.
- **Reassignment** requires `If-Match`. A retry carrying the decision id from
  the original call is stale by definition, so it is refused with
  `412 ORG_DECISION_STALE` rather than reassigning again.
- **Transfer** takes no `If-Match` and _does_ re-execute. If the item is
  already in the area the body names, the destination is unchanged, but the
  transfer is recorded again and the allocation may pick a different executor.
  This is the one case where an operator should look at the item after a
  resumed transfer.

If no client ever retries, the receipt simply stays `in_progress` until
retention removes it. That is a row in a table, not a stuck operation.

### What retention does to it (P0.20)

The daily beat task
`plane.bgtasks.orca_automation_cleanup_task.delete_orca_automation_operations`
(04:00 UTC, `apps/api/plane/celery.py:109`) deletes receipts by **`created_at`,
regardless of status** — deliberately, so a row that died mid-flight is not
leaked forever (`orca_automation_cleanup_task.py:59–80`). Two consequences for
an operator:

- The task consults **no** feature flag, so it keeps expiring receipts while
  the API is off, as long as `beat-worker` and `worker` are running.
- **Deleting a receipt un-spends its idempotency key.** If the API stays off
  longer than `ORCA_AUTOMATION_OPERATION_RETENTION_DAYS` (default 30), a client
  that retries after it is re-enabled is treated as a _first_ call, not a
  replay. For creation that is harmless for the reasons above; for transfer it
  re-executes. `0` expires every receipt at the next daily run rather than
  disabling the expiry — to stop the expiry, remove the beat entry.

### What the switch does not touch

- **The interface.** `/api/orca/…` is gated by `ORCA_ORG_UNITS_ENABLED` alone,
  through a different mixin
  (`OrganizationalUnitFeatureMixin`, `apps/api/plane/app/views/organizational_unit.py:103`).
  People go on marking areas, claiming, reassigning and returning work.
- **The queue itself.** No queued or assigned item changes state, and
  `queue_queryset` (`apps/api/plane/app/services/orca/queue.py`) is untouched.
  The one visible difference is that the _public_ reading of it,
  `GET /api/v1/orca/workspaces/<slug>/units/<unit_slug>/queue/`, answers 404
  like every other route in the namespace; the area's own queue in the app does
  not go through it.
- **Background work.** The access reconciler and the directory task answer to
  `ORCA_ORG_UNITS_ENABLED`, not to this switch.
- **Work items already created by the API.** They are ordinary Plane work
  items with an area, a decision log and a binding. Nothing about them depends
  on the switch that created them.

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

## Native webhooks

A work item created through this API is announced the same way a person
creating one in the UI is: `model_activity` runs after commit. Subscribe to
native `issue` webhooks.

The envelope already has `workspace_slug`. The work item in `data` carries
native `external_source` / `external_id`. An Orca sidecar is attached
without changing the upstream serializer:

```json
"orca": {
  "unit_slug": "compliance",
  "routing_state": "queued",
  "primary_executor": null
}
```

`orca` is `null` when the item has no area. Put the orchestrator's host in
`WEBHOOK_ALLOWED_HOSTS` (see `docs/orca-processes-runbook.md`).

---

## Not here yet

| Wanted                                           | Where it is                  |
| ------------------------------------------------ | ---------------------------- |
| Orchestrator sidecar (templates, event consumer) | Phase 4.4, outside this repo |

`process`, `completion_due_at` and `POST .../complete/` shipped in items 4.2
and 4.3, behind `ORCA_PROCESS_PROJECTION_ENABLED` (default off). Native
webhooks carry the Orca sidecar (4.5). The coordinator inbox groups by
process instance (4.6). Coordinator access to another area's queue shipped
in Phase 2. Availability affects ranking (`lb-2`) when
`ORCA_AVAILABILITY_ENABLED` is on.

The full design, including the invariants these endpoints preserve, is in
[`docs/orca-work-management-rfc.md`](./orca-work-management-rfc.md).
