# Runbook — processes and the orchestrator

For whoever is on call when a process misbehaves. Every procedure here is
Plane-side: the orchestrator is a separate service
([its contract is here](./orca-orchestrator-contract.md)), and the point of
this document is that **Plane keeps working while it is down**.

Read this before switching `ORCA_PROCESS_PROJECTION_ENABLED` on, not during
the incident it causes.

---

## The one paragraph that matters

Nothing in the process layer decides who does the work. It records which step
of which run a work item is, and it closes a step when an outside rule says so.
Take the orchestrator away and every work item it already created stays exactly
where it is, in the area that owns it, in that area's queue, with the person
who was assigned to it. **Work does not stop; only the creation of new steps
stops.** That is the whole design, and it is why the procedures below are short.

---

## Stopping the orchestrator

Stop the service. That is all — there is nothing to drain on Plane's side and
no half-written state to worry about: projecting a step happens inside the
same database transaction as creating the work item, so at any instant a step
either exists completely or does not exist at all.

**What keeps working:** every queue, every assignment, every alert, the SLA
sweep, the availability sweep, the whole interface, and every route of
`/api/v1/orca/` except the ones that create new steps of a process.

**What stops:** new steps stop being created. A run half-way through stays
half-way through: its finished steps stay finished, its open steps stay in
their areas' queues and can be worked and closed by hand.

**What to tell people:** the coordinator of an affected area sees nothing
unusual — the queue is the queue. Only somebody waiting for step 4 to appear
notices, and it appears when the orchestrator comes back.

## Restarting it

Start the service. It replays from its own event log, and every call it makes
is find-or-create behind an idempotency key derived from the event, so the
steps it already created are found rather than duplicated.

Then verify, in this order:

```bash
# 1. Nothing is stranded: no item without an area, no assigned item without
#    an executor, no executor who cannot hold the work.
python manage.py audit_organizational_routing --workspace <slug>

# 2. The runs it touched read as expected.
curl -sS -H "X-Api-Key: $KEY" \
  "$PLANE/api/v1/orca/workspaces/<slug>/process-instances/<source>/<instance_id>/" | jq '.status, (.steps|length)'
```

A clean audit and the expected number of steps is the whole check. If the
audit reports something, `--write` returns the affected items to their areas'
queues; read the report first.

## Reprocessing a run that stopped half-way

Replay the source events for that instance. The orchestrator's keys are
derived from the events, so:

- steps that already exist are **found**, and answer with
  `Idempotent-Replay: true` — no second work item, no second allocation, no
  second decision;
- steps that were never created are created;
- a step whose body changed since the first attempt answers
  `409 ORG_IDEMPOTENCY_PAYLOAD_MISMATCH` and writes nothing. That is not a
  transient error: fix the body and send it under a **new** key.

This is exercised in the test suite —
`plane/tests/unit/orca/test_process_replay.py` delivers twenty events twice and
asserts every count is identical, and replays a run that died after step 2 —
but the staging rehearsal in
[the contract](./orca-orchestrator-contract.md#contract-tests-it-must-pass-against-staging)
is what proves it for a given orchestrator.

## Repairing one instance by hand

Everything below is done in Plane's own interface, by a person, and the read
endpoint reflects it immediately. There is no "process admin" screen and there
should not be one: a run is its work items.

| Symptom                                             | What to do                                                                                                                                                                                 |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| A step is in the wrong area                         | Transfer it (queue screen, "Transfer to area…", or `POST .../transfer/`). The step key and the instance are untouched.                                                                     |
| A step is on the wrong person                       | Reassign it from the area's queue. Ordinary Phase 2 flow; the process layer is not involved.                                                                                               |
| A step was closed by mistake                        | Reopen the work item by changing its state. `ProcessCompletionEvent` is append-only, so the claim stays in the log — the item is what moves.                                               |
| A step should never have existed                    | Close or cancel the work item. `status` derives from the steps' states, so a cancelled step no longer blocks the run from reading `completed`.                                             |
| A step is missing                                   | Let the orchestrator create it (replay), or create it by hand and accept that it carries no step key. Prefer the replay.                                                                   |
| The run is finished but `status` is not `completed` | At least one step is not in a completed or cancelled state. Read the instance; the step that is open is named there.                                                                       |
| `aguardando-validacao` piled up                     | That is `automatic_with_review` with no review state configured for the area. Configure `review_state` on the area↔project policy; existing items keep the label until a person clears it. |

**Never** edit `ProcessInstanceItem` or `ProcessCompletionEvent` rows directly
in the database. The first is what makes a redelivery idempotent, the second is
the audit trail, and a hand-edited row is a lie the next replay believes.

## Switching the projection off

```bash
ORCA_PROCESS_PROJECTION_ENABLED=0   # then restart the API workers
```

What **keeps working** — that is, everything except two things:

- every area, membership, coordinator, queue, policy and alert;
- every assignment, reassignment, transfer, claim and return, in the interface
  and in `/api/v1/orca/`;
- the SLA sweep and the availability sweep;
- reading a run: `GET /process-instances/{source}/{instance_id}/` still answers,
  because the rows are still there. Turning the flag off hides no history.
- work items created as steps: they stay in their areas, keep their executors,
  keep their deadlines. A step is an ordinary work item with a note attached.

What **stops**:

- a `process` block on creation is refused with
  `400 ORG_PROCESS_PROJECTION_DISABLED`. The work item is **not** created — the
  whole call is refused, so an orchestrator does not end up with items it
  believes are steps and Plane does not;
- `POST .../complete/` is refused with the same code;
- `responsibility.completion_due_at` is refused (`400 VALIDATION_ERROR`, with
  the field named) rather than accepted and dropped, because a deadline nobody
  records is worse than a deadline rejected;
- the queue stops grouping by run, and the step badge disappears from the rows.
  The items are all still there, ungrouped.

Switching it back on picks up exactly where it left off. Nothing is
back-filled, and nothing needs to be: the instances that existed still exist.

## Switching it on for the first time

1. `ORCA_ORG_UNITS_ENABLED=1` and `ORCA_PUBLIC_API_ENABLED=1` must already be
   on. The process layer sits on top of both.
2. For each area↔project pair that will run steps, decide `completed_state` and
   `review_state` on the policy. Without them, `automatic` falls back to the
   project's first completed state by sequence, and `automatic_with_review`
   falls back to the `aguardando-validacao` label. Both fallbacks work; both
   are worse than a deliberate choice.
3. `ORCA_PROCESS_PROJECTION_ENABLED=1`, restart the API workers.
4. Add the orchestrator's host to `WEBHOOK_ALLOWED_HOSTS` if it listens to
   Plane's webhooks.
5. Run one instance end to end in staging before pointing anything real at it.

## The kill switches, in order of blast radius

| Flag                              | Off means                                                                                    |
| --------------------------------- | -------------------------------------------------------------------------------------------- |
| `ORCA_PROCESS_PROJECTION_ENABLED` | No new steps, no automatic completion. Everything else, including existing steps, unchanged. |
| `ORCA_PUBLIC_API_ENABLED`         | `/api/v1/orca/` answers 404. The interface and the queues are untouched.                     |
| `ORCA_AVAILABILITY_ENABLED`       | Absences and per-person limits stop affecting the ranking; the sweep stops writing.          |
| `ORCA_ORG_UNITS_ENABLED`          | The whole organizational layer. Plane is upstream Plane again.                               |

Each one's "off" position reproduces the behaviour that existed before the
phase that introduced it. A switch whose off position changes answers is a
switch nobody dares touch during an incident, which makes it not a switch.

## When somebody asks "why did this happen?"

| Question                               | Where the answer is                                                                            |
| -------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Why does this person have this step?   | `AssignmentDecision` — the queue's decision timeline, with the candidate snapshot that ranked. |
| Who said this step was finished?       | `ProcessCompletionEvent` — source, event id, rule version, and the evidence, verbatim.         |
| Which template version ran this?       | The instance's `template.version`, frozen at the first step.                                   |
| When was it due, and was that changed? | `IssueServiceLevel` — the current deadlines and the `original_*` it started with.              |
| Did our call actually do anything?     | `AutomationOperation` — the receipt for the idempotency key, and whether it was a replay.      |

None of these are deleted by any procedure in this document.
