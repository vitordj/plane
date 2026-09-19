# Process runs — operator runbook (item 4.7)

How to stop, resume and repair a process projection without duplicating work.
The orchestrator itself lives in another repository (item 4.4). This file
is what an operator of **this** Plane instance needs, and what that
orchestrator may assume.

Flags:

| Flag                              | Default | What it gates                                           |
| --------------------------------- | ------- | ------------------------------------------------------- |
| `ORCA_ORG_UNITS_ENABLED`          | on      | The whole organizational layer                          |
| `ORCA_PUBLIC_API_ENABLED`         | off     | `/api/v1/orca/`                                         |
| `ORCA_PROCESS_PROJECTION_ENABLED` | off     | The `process` block, `complete/`, and the instance read |

Turning the process flag off does **not** delete rows. Work items, bindings,
areas and SLAs keep working. What stops answering is the `process` block on
create, `POST …/complete/`, and `GET …/process-instances/…`. Existing
`ProcessInstanceReference` rows stay; the queue still groups by them.

## Webhooks the orchestrator subscribes to

Orca creates go through the same `model_activity` → `webhook_activity` path
as a person creating a work item. Subscribe to native `issue` webhooks.

The payload already has `workspace_slug`. Work items created through
`/api/v1/orca/` also have native `data.external_source` and `data.external_id`.
Item 4.5 attaches a sidecar, without changing the native serializer:

```json
{
  "event": "issue",
  "action": "create",
  "workspace_slug": "acme",
  "data": {
    "id": "…",
    "external_source": "espo-onboarding",
    "external_id": "cliente-1:kyc",
    "orca": {
      "unit_slug": "compliance",
      "routing_state": "queued",
      "primary_executor": null
    }
  }
}
```

`orca` is `null` when the work item has no area (a Compose-pushed seed issue,
or anything created outside the automation API).

The orchestrator's host must be listed in `WEBHOOK_ALLOWED_HOSTS` (or its
IP in `WEBHOOK_ALLOWED_IPS`) or the worker will refuse the URL as SSRF. The
fork already reads both variables; they are empty by default.

## Stop the orchestrator

1. Stop the sidecar. In-flight HTTP calls either finish or the client retries
   with the **same** `Idempotency-Key`.
2. Leave Plane running. Work items already created stay in their areas.
   Coordinators keep assigning, returning and transferring as usual.
3. Optional: set `ORCA_PROCESS_PROJECTION_ENABLED=0` if you also want
   `complete/` to 404 while you inspect a stuck run. Do not do this to
   "pause" a run — it only hides the projection, it does not freeze native
   state.

## Relight the orchestrator

1. Turn `ORCA_PROCESS_PROJECTION_ENABLED=1` if you turned it off.
2. Start the sidecar.
3. Re-deliver the same events (same `event_id`, same
   `Idempotency-Key = "{source}:{instance}:{step}:{event_id}"`).
4. Counts must not grow. The contract test
   `TestReprocessingARun.test_twenty_events_replayed_leave_the_counts_alone`
   is the assertion: twenty creates, replayed, leave 20 issues, 20 steps, 5
   runs.

A key that already succeeded answers `201` with `Idempotent-Replay: true`
and does not attach a second `ProcessInstanceItem`. A key that already
failed with 4xx **replays that 4xx** — fixing the payload requires a new
key (vary `event_id` or add an attempt suffix). That is RFC §6.7, not a
quirk of processes.

## Resume a run that died halfway

A run of four steps where step 3 failed:

1. `GET /api/v1/orca/workspaces/{slug}/process-instances/{source}/{id}/`
   tells you which steps exist and which native states they are in.
2. Re-create missing steps with the original create keys. Existing steps
   replay.
3. Re-close automatic steps with `complete/`. If the original complete key
   recorded a 4xx, use a **new** key after the cause is fixed (missing
   completed state, flag off, etc.).
4. The last closed step sets `ProcessInstanceReference.completed_at`.

The test
`TestReprocessingARun.test_a_failure_on_step_three_is_finished_by_a_replay`
is that sequence.

## Repair one instance by hand

- **Wrong area.** Transfer through the UI or
  `POST …/organizational-unit/transfer/`. The process row stays; the area
  on `IssueOrganizationalUnit` is what the queue reads.
- **Duplicate step.** Soft-delete is not exposed. Do not write SQL against
  `orca_process_instance_items` unless you are restoring a backup. If a
  second work item was created because the create key was new, close or
  cancel the extra native item and leave the projection pointing at the
  one whose `external_id` is the real step.
- **Instance stuck `running` after every step is done.** The native states
  were moved in the UI. `GET` the instance — `status` is derived from
  those states, and `refresh_instance_status` runs on `complete/`. Moving
  the last item to a completed group in the UI is enough; the next read
  reports `completed`.

## What still works with the process flag off

Everything except the three routes named at the top: the public create
without a `process` block, assignment, the coordinator inbox, webhooks,
SLA rows already written. Grouping in the inbox still shows runs that
already exist.
