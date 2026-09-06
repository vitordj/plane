# What the orchestrator may assume

Orca does not run processes. It records them.

The thing that knows an onboarding has five steps, that step 3 waits on step 2,
that a new hire starting on the 15th means the first step is due on the 12th —
that is the **orchestrator**, and it lives outside this monorepo (FORK.md §1.B
keeps a service that is not Plane out of Plane's tree). What lives here is the
API it calls and the projection it writes: the instance, the step, the
completion rule, so that four work items read as four steps of one onboarding
instead of four unrelated items in four different queues.

This document is the contract between those two halves. It says what the
orchestrator may rely on, what it may not, and the tests it has to pass against
staging before anybody points it at production. It is written for whoever
builds that service — most likely in its own repository, suggested name
`orca-orchestrator`.

> Everything here is behind `ORCA_PROCESS_PROJECTION_ENABLED`, which ships
> **off**. With it off, the `process` block and `POST .../complete/` are
> refused (`ORG_PROCESS_PROJECTION_DISABLED`) and every other route of
> [`docs/orca-public-api.md`](./orca-public-api.md) behaves exactly as it did
> before Phase 4. An orchestrator pointed at an instance with the flag off gets
> a clear refusal, not a silent half-write.

---

## The division of labour

| The orchestrator owns                                          | Orca owns                                                   |
| -------------------------------------------------------------- | ----------------------------------------------------------- |
| The template: steps, order, `depends_on`, deadlines, branching | Which area is responsible for a step                        |
| Which step to create next, and when                            | Which **person** in that area does it                       |
| The event log of the source system, and de-duplicating it      | The queue, the coordinator's screens, the alerts            |
| Deciding a step is finished by its own rule                    | What "finished" does to the work item                       |
| Template versioning in Git                                     | Recording which template version each instance actually ran |

The asymmetry is deliberate: the orchestrator knows the _process_, and knows
nothing about who is on holiday. Orca knows who is on holiday, and nothing
about what an onboarding is. Neither side is allowed to guess at the other's
half — which is why `assignees` is refused on creation
(`ORG_ASSIGNEES_NOT_ALLOWED_HERE`) and why Orca never creates a step by itself.

---

## What it may assume

**1. One external key, one work item, forever.** `external.source` +
`external.id` is the orchestrator's own key. Creating with a key that already
exists finds the item rather than making a second one. The pair is unique per
workspace; the same key bound to an item in a different project is a
`409 ORG_EXTERNAL_BINDING_CONFLICT`, never a silent move.

Derive it from the process, not from the clock:
`f"{template}:{instance_id}:{step_key}"` is a good external id.

**2. Creation is find-or-create, and finding changes nothing.** Calling
`POST work-items/` again for an existing key with the _same_ `unit` reports
current state and does not re-rank, does not write a second `AssignmentDecision`,
does not move anybody. This is what makes it safe to call on every event the
source system emits. Sending a _different_ `unit` is a real instruction and
does transfer the item.

**3. Idempotency keys are per operation, and derived from the event.** The
recommended shape:

```python
key = f"{source}:{instance_id}:{step_key}:{event_id}"
```

`uuid4()` is the wrong answer — a redelivery after a crash gets a new UUID,
and a new key is a new operation. What the server does with a reused key is in
[the API doc](./orca-public-api.md#the-one-thing-to-get-right-idempotency-keys);
the two consequences that bite an orchestrator are that **a replay answers the
original call, not the present** (use `GET` when you want the present) and that
**a 4xx spends the key** (a corrected body needs a new one).

**4. The instance survives the orchestrator.** `ProcessInstanceReference` and
`ProcessInstanceItem` live in Plane's database. An orchestrator that loses its
state file can rebuild what it created by reading
`GET /process-instances/{source}/{instance_id}/`: every step it ever projected,
with the step key it used.

**5. The instance keeps the template version it started under.** The first step
to arrive fixes `template.version` for the whole run. A later step arriving
under a different version is accepted and logged, and does not rewrite the
instance — an instance that ran under `v3` says `v3` a year later, whatever
`v4` says today.

**6. `status` is derived, not stored.** The read endpoint computes `completed`
from the steps' native state groups. A person closing the last step in Plane's
own interface finishes the instance just as truly as a `complete/` call does,
and the read reflects that immediately.

**7. Native webhooks carry the area.** Plane's own `issue` webhook payload
gains an `orca` key: `unit_id`, `unit_slug`, `routing_state`, `queue_reason`,
`primary_executor` (id), `assignment_due_at`, and a `process` object with
`source`, `instance_id`, `template_version`, `step_key`, `completion_mode`.
Ids and slugs only, no names or emails. A listener can decide from the event
alone whether a change matters, instead of following every webhook with a read.

An item no area owns has no `orca` key at all — a workspace not using areas
sees exactly the payload it saw before this existed.

Set `WEBHOOK_ALLOWED_HOSTS` to include the orchestrator's host, or Plane will
refuse to call it.

**8. Deadlines are recorded with their provenance.** `completion_due_at` sent
on creation is written to `IssueServiceLevel` with `source="process"` and
`source_version` = the template version. `original_assignment_due_at` and
`original_completion_due_at` are immutable: a later change records the change,
it does not erase what was first promised.

---

## What it may not assume

**It may not assume it can name the executor.** `assignment.mode = "explicit"`
exists and is validated, but a template that hard-codes people is a template
that breaks on the first resignation. Send `default` and let the area's policy
decide.

**It may not assume a step will be assigned.** `routing_state: "queued"` is a
**success** — the area accepted responsibility and the item is in its inbox.
`allocation_failed` means the area has nobody eligible, and a human has been
alerted. Neither is a reason to retry; both are reasons to carry on.

**It may not assume `complete/` completes.** The step's own `completion_mode`
decides:

| Mode                    | What `complete/` does                                                                                                               |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `automatic`             | Moves the item to a completed state. `applied: true`.                                                                               |
| `automatic_with_review` | Moves it to the area's review state, or applies the `aguardando-validacao` label when the area named none. The item stays **open**. |
| `manual`                | **Refused**, `409 ORG_COMPLETION_MANUAL_ONLY`. The claim is recorded anyway.                                                        |

`manual` is the default for a step created without an explicit mode. A robot
declaring a manual step finished turns a checklist into a lie, so it is refused
rather than downgraded — and the refusal still writes a `ProcessCompletionEvent`
with `applied: false`, because a robot that keeps claiming a manual step is
finished is a fact worth being able to see.

**It may not assume ordering.** Orca does not enforce `depends_on`. Either
create the steps up front and express the dependency with Plane's native
`blocked_by` relations, or create each step late, when the previous one
completes. Pick one per template and say which in the template; do not mix.

**It may not assume a step can be un-completed.** `ProcessCompletionEvent` is
append-only. Correcting a step means a person changing the item's state in
Plane, which the read endpoint then reflects.

**It may not assume the area covers the project.** An area is linked to the
projects it works in. Filing into a project the area does not cover is
`400 ORG_UNIT_NOT_COVERING_PROJECT`. Read `GET /units/` at startup and validate
templates against it rather than discovering it at 3am.

**It may not assume its token is enough.** The token carries the permissions of
the user it belongs to. The orchestrator's service account must be an active
project member (role Member or Admin) of every project its templates touch.

---

## Projecting a step

```http
POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/
Idempotency-Key: onboarding:cliente-123:kyc:evt-9911
```

```json
{
  "external": { "source": "orca-orchestrator", "id": "onboarding:cliente-123:kyc" },
  "work_item": { "name": "Validate registration documents", "target_date": "2026-09-30" },
  "responsibility": {
    "unit": "compliance",
    "assignment": { "mode": "default" },
    "assignment_due_at": "2026-09-25T12:00:00Z",
    "completion_due_at": "2026-09-30T18:00:00Z"
  },
  "process": {
    "source": "orca-orchestrator",
    "instance_id": "cliente-123",
    "template_name": "onboarding-cliente",
    "template_version": "3",
    "step_key": "kyc",
    "completion_mode": "automatic_with_review"
  }
}
```

`source`, `instance_id`, `template_version` and `step_key` are required.
`template_name` and `completion_mode` are optional (`manual` by default).

The whole call — binding, area, allocation, decision, instance, step, service
level — is **one transaction**. There is no state in which the work item exists
and the step does not.

## Closing a step

```http
POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/complete/
Idempotency-Key: onboarding:cliente-123:kyc:done:evt-10024
```

```json
{
  "evidence": { "document_id": "DOC-991", "checked_by": "kyc-rules" },
  "rule_version": "2026.09",
  "source": "kyc-service",
  "event_id": "evt-10024"
}
```

The answer is the ordinary work-item envelope plus:

```json
"completion": {
  "mode": "automatic_with_review",
  "applied": true,
  "event_id": "1c9e...",
  "state": "In review",
  "step_key": "kyc"
}
```

`evidence` is stored verbatim and never interpreted. It is the caller's
vocabulary, and this API does not get to define it.

## Reading a run

```http
GET /api/v1/orca/workspaces/{slug}/process-instances/{source}/{instance_id}/
```

Every step with its native state, `routing_state`, `unit`, `primary_executor`,
`assignment_due_at` and `completion_due_at`, plus the derived `status`.
Readable by any active member of the workspace.

---

## Contract tests it must pass against staging

Run these against a staging workspace before the orchestrator is trusted with a
real process. Each is a statement about the pair of systems, not about either
one alone.

1. **A step created twice is one step.** Deliver the same source event twice
   with the same derived key. Expect: one work item, one `ProcessInstanceItem`,
   one `AssignmentDecision`, the second answer carrying
   `Idempotent-Replay: true`.

2. **The same instance, twenty events, delivered twice.** Four instances × five
   steps, replayed in full. Expect identical counts of work items, instances,
   steps, routing links and decisions before and after the replay. (This is the
   in-repo test `test_process_replay.py::test_twenty_events_delivered_twice_produce_one_of_everything`;
   the staging run proves it end to end through HTTP.)

3. **A run that died halfway completes on replay.** Kill the orchestrator after
   step 2 of 5. Restart. Expect the instance to reach five steps, with steps
   1–2 untouched (same issue ids, same executors) and no duplicate decisions.

4. **A corrected body needs a new key.** Resend a spent key with a changed body.
   Expect `409 ORG_IDEMPOTENCY_PAYLOAD_MISMATCH` and **no** write. Then resend
   the corrected body under a fresh key and expect it to apply.

5. **Every step lands in an area.** After a full run,
   `python manage.py audit_organizational_routing --workspace <slug>` reports
   nothing: no item without an area, no assigned item without an executor, no
   executor who is not eligible.

6. **`manual` is refused.** A step whose `completion_mode` is `manual` answers
   `409 ORG_COMPLETION_MANUAL_ONLY` to `complete/`, the item does not move, and
   a `ProcessCompletionEvent` with `applied: false` exists afterwards.

7. **`automatic_with_review` does not finish the run.** After completing the
   last step in that mode, the instance's `status` is **not** `completed`. A
   person moving the item to a completed state in the interface then makes it
   `completed` on the next read.

8. **The webhook carries the area.** Subscribe a listener, create a step, and
   assert the `issue` payload's `orca.process.step_key` matches what was sent.

9. **The flag off is a clean refusal.** With `ORCA_PROCESS_PROJECTION_ENABLED=0`,
   a `process` block answers `400 ORG_PROCESS_PROJECTION_DISABLED` and no work
   item is created; `complete/` answers the same; every other route still works.

10. **Template version is frozen per instance.** Send step 1 under `v3` and
    step 2 under `v4`. Expect the read to report `v3`, and a log line about the
    mismatch.

---

## Suggested shape of the sidecar

Not prescriptive — the contract above is what matters — but this is the shape
the design assumed:

- **Templates in Git**, YAML: `name`, `version`, `steps[] {key, title, unit,
project, assignment, completion_mode, assignment_sla, completion_sla,
depends_on[]}`. Bump `version` on any change; never edit a released version.
- **An event consumer** (webhook from the source system, or a queue) that
  stores processed `event_id`s. Orca's idempotency is the second line of
  defence, not the first.
- **A client** — [`tools/orca-client/`](../tools/orca-client/README.md) is a
  working Python one, including key derivation.
- **A webhook listener** for Plane's `issue` events, to react to a step
  changing state (release the next step, mark the instance).
- **A runbook**: see [`docs/orca-processes-runbook.md`](./orca-processes-runbook.md)
  for the Plane-side half of stopping, restarting and repairing.

## Schema, not instances

Plane Compose (or any schema-as-code tool) is for **structure**: states,
labels, project layout, versioned in Git. Instances always go through this API.
The two are not interchangeable — a tool that reconciles declared state would,
pointed at instances, delete work items that a person legitimately closed.

This is decision F12 in [the RFC](./orca-work-management-rfc.md), and pendency
**A5** — reading Compose's official documentation to confirm its re-push
behaviour on the CE 1.4.x base — is still open. It needs somebody with access
to that documentation; this environment's network policy blocks it.
