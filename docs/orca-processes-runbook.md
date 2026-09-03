# Running processes: what to do when something goes wrong

For the person on call. The orchestrator is a sidecar service outside this
repository; what follows is what Plane's side does, and what you can safely do
to it.

## The one thing to know first

**Everything the orchestrator writes is idempotent under its idempotency key.**
Replaying a run is safe. That is why most of the answers below are "replay it"
rather than "repair it by hand".

## Turning it off

| To stop | Do | What keeps working |
| --- | --- | --- |
| The orchestrator | Stop the service | Everything. Work already created stays in its area's queue and people carry on with it. |
| Process projection | `ORCA_PROCESS_PROJECTION_ENABLED=0` | Everything except the `process` block and `complete/`, which answer `4929`. Existing runs stay in the database and are readable again when it is switched back on. |
| The whole automation API | `ORCA_PUBLIC_API_ENABLED=0` | The app, the queue, the coordinators. Every `/api/v1/orca/` route answers 404. |
| The organizational layer | `ORCA_ORG_UNITS_ENABLED=0` | Plane. Areas disappear from the interface and every `/api/orca/` route answers 404. Inherited project access is left exactly as it is — the switch stops the layer acting, it does not withdraw what it granted. |

None of these lose data. All four are reversible by putting the value back.

## Turning it back on

Start the orchestrator and let it replay. It will:

- reconnect to each running instance through `source` + `instance_id`;
- find the steps that already exist, because the idempotency keys match;
- create only the steps that are genuinely missing.

Then check one thing:

```bash
python manage.py audit_organizational_routing --workspace <slug>
```

Clean output means no work item is claiming an executor who cannot do it and
no queue state disagrees with the assignees. That is the check the gate asks
for after a restart.

## Reprocessing a half-finished run

Re-send every step of the template with the same idempotency keys. Steps that
exist come back as replays; the missing ones are created. Nothing is
duplicated, and a step somebody has since reassigned keeps its executor — the
replay returns the stored response rather than redoing the work.

If a step must genuinely be recreated (it was deleted, say), it needs a new
`external.id`: the old binding still points at the deleted work item, and
reusing the reference is refused rather than silently re-pointed.

## Fixing a run by hand

Legitimate, and better than fighting the orchestrator:

- **A step is in the wrong area.** Move it in the app (Work tab → the row →
  move to another area) or with the API's `transfer/`. The run does not care
  which area a step is in.
- **A step should not have been created.** Cancel it in the app. Cancelled
  counts as closed, so the run can still finish.
- **A step is stuck waiting for nobody.** The queue shows why
  (`allocation_failed` means the ranking found nobody — usually the area's
  membership or its project links are wrong, not that everybody is busy).
- **A run should be abandoned.** Cancel its remaining work items. The run reads
  as completed once none of its steps is still open; there is no separate
  "cancel the run" call, because the work items are the truth.

Do **not** edit `ProcessInstanceItem` or `ProcessCompletionEvent` rows
directly. The completion events are append-only for a reason: they are what
answers "who closed this, and on what evidence?" the first time somebody
disputes an automatic closure.

## When a step was closed automatically and should not have been

The evidence is in `ProcessCompletionEvent` for that work item: which system
asserted it, which version of the rule applied, and what it sent. Reopen the
work item in the app — the run reopens with it, because status is derived from
the work items rather than stored. Then fix the rule, and bump the template
version so the two runs are distinguishable afterwards.

## What to check after any incident

```bash
# Nothing claims an executor who cannot do it
python manage.py audit_organizational_routing --workspace <slug>

# Nobody is holding work while away or gone (report only)
python manage.py sweep_unavailable_executors --workspace <slug>
```

Both are read-only without `--write`. Running them daily is cheap and turns
"why has this been sitting there for a week" into something noticed rather
than discovered.
