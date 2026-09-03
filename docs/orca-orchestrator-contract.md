# What the orchestrator may assume

The process templates live outside Plane, in their own repository, run by a
sidecar service (RFC F19, FORK.md §1.B). This document is the boundary between
the two: what Plane's automation API promises, what it deliberately does not,
and the tests the orchestrator has to pass against staging before it is
allowed near production data.

The endpoints themselves are in [`orca-public-api.md`](./orca-public-api.md).
This is about what may be relied on.

## What Plane owns, and what the orchestrator owns

| Plane | The orchestrator |
| --- | --- |
| Work items, their states, who is doing them | Which steps exist, in what order, under which conditions |
| Which area is responsible, and its policy | Which area a step is *asked* for |
| The queue, and every decision about it | When to create the next step |
| That a step belongs to a run of a process | The template, and its version |

The line matters in one direction in particular: **the orchestrator never
decides who does the work.** It asks for a mode; the area's policy decides, and
refuses a mode the area does not allow. An orchestrator that starts naming
executors has moved the routing rules out of the product and into a YAML file
nobody in the area can see.

## Six things it may rely on

1. **Every write is idempotent under a key.** Send `Idempotency-Key` on every
   mutating call. Use `f"{source}:{instance}:{step}:{event_id}"` — it is stable
   under a restart and unique per attempt at a distinct thing. A replay returns
   the first result with `operation.replay = true` and creates nothing.
2. **An external reference maps to exactly one work item, forever.**
   `external.source` + `external.id` is a permanent binding. Reusing a pair
   across projects is refused (`4925`), not silently re-pointed.
3. **A `process` block reconnects rather than restarting.** Posting a step for
   an `instance_id` that already exists joins that run. This is what makes a
   restart mid-run safe: replay every step, and the ones that landed are
   recognised.
4. **`template_version` is recorded on the run.** A template that changed
   half-way through a run is a real event, and the record is what makes it
   explicable afterwards.
5. **Errors are stable codes, not prose.** Branch on `error_code`; the English
   `error` text may be reworded.
6. **The native webhooks fire for work items this API creates.** Including an
   `orca` block naming the area, the routing state and the executor, so a
   listener does not have to call back for it.

## Five things it may not

1. **That a step it created is still where it put it.** People reassign work,
   return it to the queue and move it to another area. Read the current state;
   never assume the response from creation is still true.
2. **That `least_loaded` is available.** The area's `allowed_modes` decides,
   and a mode outside it is refused rather than downgraded. Handle `4917`.
3. **That it may close any step.** `completion_mode` is set per step, and
   `manual` steps refuse `complete/` (`4921`). That is the point of the
   setting.
4. **That `target_date` is the promise.** It is a projection anybody can edit.
   The promise is the service level, and its `original_*` values are what a
   report about lateness should read.
5. **That the API is on.** `ORCA_PUBLIC_API_ENABLED=0` makes every route 404,
   and `ORCA_PROCESS_PROJECTION_ENABLED=0` refuses the `process` block with
   `4929`. Both are ways an operator stops the world during an incident; an
   orchestrator that treats 404 as a bug will fight them.

## Contract tests it must pass against staging

Run these before pointing it at anything that matters. Each is a scenario, not
an assertion about internals:

1. **Replay.** Create a four-step run. Replay every call with the same
   idempotency keys. Result: four work items, four `ProcessInstanceItem` rows,
   and every response carries `operation.replay = true`.
2. **Restart mid-run.** Create steps one and two, kill the orchestrator, start
   it, let it replay the whole template. Result: four steps, one run, no
   duplicates.
3. **Failure at step three of four.** Inject a failure after step two. Replay.
   Result: the run completes and nothing is created twice.
4. **Refused mode.** Ask for `least_loaded` in an area whose `allowed_modes` is
   `["manual"]`. Result: `4917`, and **no work item is created** — the whole
   composed call is one transaction.
5. **A person moves the work.** Create a step, have somebody reassign it in the
   app, then replay the creation. Result: the replay returns the original
   response and **does not** undo the person's decision.
6. **Closing a manual step.** Result: `4921`, and the step is untouched.
7. **Closing the last step.** Result: the run reads as `completed`, with
   `completed_at` set.
8. **Someone closes a step in the app.** Result: reading the run reflects it —
   progress and status are derived from the work items, not from a counter.

## Webhooks

The orchestrator's host must be in the fork's `WEBHOOK_ALLOWED_HOSTS`, or the
webhook is never sent. That list exists so a workspace admin cannot use
webhooks to make the server fetch arbitrary internal addresses; adding a host
to it is a deployment decision, not a workspace setting.

React to `issue` events for state changes — that is how "step two is done,
create step three" is driven without polling. The `orca` block on the payload
carries the area and the routing state, so the common decisions need no extra
call.

## Where the templates live

Not here. A suggested shape, kept in the orchestrator's own repository:

```yaml
name: onboarding-cliente
version: "3"
steps:
  - key: compliance.kyc
    title: Validate the customer's documents
    unit: compliance
    project: onboarding
    assignment: { mode: default }
    completion_mode: automatic_with_review
    assignment_sla: 2h
    completion_sla: 2d
    depends_on: []
```

`version` is a string and is sent verbatim as `template_version`. Bump it
whenever the steps change, including a change of `unit` — a run that
half-happened under two versions is otherwise unreadable.
