# What each number on the executive page means

One page, ten indicators, and the only thing that makes them worth showing is
that each has **one** written definition and a query anybody can run to
reproduce it. A dashboard number nobody can check is a number that ends an
argument by authority rather than by evidence — and the first time it disagrees
with an area's own queue, the queue is right.

So this document is the contract for
`/api/orca/workspaces/{slug}/executive/` and for the page at
**Workspace settings → Executive view**. The SQL here is what the service
computes; if you run it and get a different answer, that is a bug in the
service, not a difference of interpretation.

- Access: **workspace Admin**. Not because the numbers are secret — a
  coordinator sees more detail than this about their own queue — but because a
  cross-area comparison is a management artifact (RFC F23). The capability that
  would widen it (`executive_viewer`) is open decision A4.
- Cached **five minutes** per `(workspace, period, area)`. The answer carries
  `generated_at`, and the page shows it, so nobody reads a stale number as news.
  `?refresh=1` reads through.
- Periods: `7d`, `30d`, `90d`. Anything else is refused rather than guessed at.

---

## The two definitions everything rests on

**Closed** means the work item's native state is in group `completed` **or**
`cancelled`. Cancelled counts as closed everywhere here: an item nobody will
ever do is not backlog.

**The area owns an item** when a row exists in `issue_organizational_units`
linking them. There is exactly one such row per item (RFC I1), which is why no
number below double-counts.

Throughout, `:since` is `now() - interval '30 days'` (or 7, or 90) and `:unit`
is the area's id.

---

## Per area

### `backlog` — open work the area owns

```sql
SELECT count(*)
FROM issue_organizational_units link
JOIN issues i ON i.id = link.issue_id
LEFT JOIN states s ON s.id = i.state_id
WHERE link.organizational_unit_id = :unit
  AND (s.group IS NULL OR s.group NOT IN ('completed', 'cancelled'));
```

Not "items in the queue" and not "items assigned": both are subsets, and a page
that called either of them the backlog would under-report the area.

### `queued` — waiting for a person

```sql
... AND link.routing_state IN ('queued', 'allocation_failed')
    AND s.group NOT IN ('completed', 'cancelled')
```

`allocation_failed` is counted here, not as an error: it is an item waiting for
a human precisely because the ranking could not place it. `allocation_failed`
is also reported on its own, because "waiting for a coordinator" and "the area
has nobody eligible" call for different actions.

### `assignment_overdue` — the area's own promise, breached

```sql
... AND link.routing_state IN ('queued', 'allocation_failed')
    AND link.assignment_due_at < now()
    AND s.group NOT IN ('completed', 'cancelled')
```

This is about **allocation**, not delivery: nobody is on the item and somebody
was supposed to be by now.

### `target_overdue` — the work itself late

```sql
... AND i.target_date < current_date
    AND s.group NOT IN ('completed', 'cancelled')
```

Different failure, different column. An item can be assigned on time and still
be late, and an item can be unassigned and not yet late.

### `assigned_open` — somebody is on it

```sql
... AND link.routing_state = 'assigned'
    AND s.group NOT IN ('completed', 'cancelled')
```

### `queue_age_p50` / `queue_age_p90` — how long the queue has waited

```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (now() - link.queued_at))),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (now() - link.queued_at)))
FROM issue_organizational_units link
JOIN issues i ON i.id = link.issue_id
LEFT JOIN states s ON s.id = i.state_id
WHERE link.organizational_unit_id = :unit
  AND link.routing_state IN ('queued', 'allocation_failed')
  AND link.queued_at IS NOT NULL
  AND (s.group IS NULL OR s.group NOT IN ('completed', 'cancelled'));
```

In **seconds**, over the items waiting **right now** — not over items that
waited at some point in the period. It answers "how long has the queue been
sitting?", which is the question a director asks looking at it.

`percentile_cont` interpolates: over `[600, 1200, 1800, 3600]` the median is
1500, not 1200. An implementation that sorted and took the middle element would
report a different number for the same data, which is why the definition names
the function.

**Empty population → `null`, never `0`.** An area holding nothing has no median
wait, and printing `0s` for it would read as instantaneous service.

### `throughput` — finished inside the period

```sql
SELECT count(*)
FROM issue_organizational_units link
JOIN issues i ON i.id = link.issue_id
WHERE link.organizational_unit_id = :unit
  AND i.completed_at >= :since AND i.completed_at <= now();
```

`issues.completed_at` is Plane's own column, maintained by the model when the
state changes group. That is deliberate: an item a person closed in the
interface counts exactly like one an API call closed, because both are the work
being done.

### `cycle_time_p50` / `cycle_time_p90` — creation to completion

```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - i.created_at))),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - i.created_at)))
FROM issue_organizational_units link
JOIN issues i ON i.id = link.issue_id
WHERE link.organizational_unit_id = :unit
  AND i.completed_at >= :since AND i.completed_at <= now();
```

Same population as `throughput`, which is why they are one query. Measured from
**creation**, not from assignment: the time an item spent waiting for a person
is part of how long the requester waited, and excluding it would flatter the
area for its own queue.

### `concentration_top3` — is this area actually three people?

```sql
WITH held AS (
  SELECT link.primary_executor_id, count(*) AS n
  FROM issue_organizational_units link
  JOIN issues i ON i.id = link.issue_id
  LEFT JOIN states s ON s.id = i.state_id
  WHERE link.organizational_unit_id = :unit
    AND link.routing_state = 'assigned'
    AND link.primary_executor_id IS NOT NULL
    AND (s.group IS NULL OR s.group NOT IN ('completed', 'cancelled'))
  GROUP BY 1
)
SELECT (SELECT sum(n) FROM (SELECT n FROM held ORDER BY n DESC LIMIT 3) top)::float
     / (SELECT sum(n) FROM held);
```

A share between 0 and 1. `1.0` in an area of three people is not a problem;
`0.9` in an area of twelve is the number the page exists to surface. `null` when
the area holds nothing assigned.

### `auto_assign_kept_ratio` — did the ranking's choice stand?

```sql
WITH ranked AS (
  SELECT d.id
  FROM orca_assignment_decisions d
  WHERE d.organizational_unit_id = :unit
    AND d.effective_mode = 'least_loaded'
    AND d.outcome = 'assigned'
    AND d.created_at >= :since AND d.created_at <= now()
),
overturned AS (
  SELECT DISTINCT d.supersedes_id
  FROM orca_assignment_decisions d
  WHERE d.supersedes_id IN (SELECT id FROM ranked)
    AND d.decided_by_id IS NOT NULL
)
SELECT (SELECT count(*) FROM ranked WHERE id NOT IN (SELECT supersedes_id FROM overturned))::float
     / (SELECT count(*) FROM ranked);
```

"Overturned" means a decision that supersedes it **and was made by a person**
(`decided_by` set). Work the availability sweep returned because somebody went
on holiday is not the ranking being wrong; counting it would make this number
worse every time the absence feature did its job.

`null` when the area made no ranked allocation in the period — a ratio of
nothing is not `1.0`.

---

## Per workspace: processes

`running` counts `orca_process_instance_references` with `completed_at IS NULL`;
`completed` counts those whose `completed_at` falls inside the period.

```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at)))
FROM orca_process_instance_references
WHERE workspace_id = :workspace
  AND completed_at >= :since AND completed_at <= now()
  AND started_at IS NOT NULL;
```

`late_steps` groups open steps past a deadline by **`step_key`**, not by
instance: "the KYC step is always late" is actionable, and "instance 4471 is
late" is not.

```sql
SELECT item.step_key, inst.template_name, count(DISTINCT item.id) AS late_count
FROM orca_process_instance_items item
JOIN orca_process_instance_references inst ON inst.id = item.process_instance_id
JOIN issues i ON i.id = item.issue_id
LEFT JOIN states s ON s.id = i.state_id
LEFT JOIN orca_issue_service_levels sl ON sl.issue_id = i.id
LEFT JOIN issue_organizational_units link ON link.issue_id = i.id
WHERE item.workspace_id = :workspace
  AND (s.group IS NULL OR s.group NOT IN ('completed', 'cancelled'))
  AND (sl.completion_due_at < now() OR link.assignment_due_at < now())
GROUP BY 1, 2
ORDER BY late_count DESC, step_key
LIMIT 5;
```

---

## The drill-down, and why two numbers on one screen may differ

Clicking a count opens the rows behind it — the same predicates as the
aggregate, which is exactly why they are written side by side in
`services/orca/executive_metrics.py`: if the two drift apart, the page is lying
about one of them.

The counts are the **workspace's**. The rows are the **reader's**: only work
items in projects they are an active member of. Plane's own project membership
is the authority on who reads titles of real work, and a dashboard does not get
to widen it (RFC F18). The difference is shown, never swallowed —
_"7 items are in projects you do not belong to. They are counted, not shown."_

Percentiles have no drill-down: a percentile of a population is not a list, and
offering to open one would mean inventing it.

---

## What the page does not do

**No stored series.** Every number is computed from rows the earlier phases
already write; nothing is recorded specially for this page. That is why the
throughput cell shows a bar comparing the areas on screen and not a sparkline
over time: a real sparkline needs a daily history, that history is item 5.2 of
the execution plan, and drawing a trend from a single number would be inventing
one.

**No caching beyond five minutes, and no nightly job.** Item 5.2 says to
materialize only if the live query exceeds two seconds against real data in
staging. Until somebody measures it there, adding a snapshot table would be
optimizing a number nobody has seen.

**No per-person ranking.** `concentration_top3` says how concentrated an area's
work is; it does not name who. The queue names people to the coordinator who
has to act, which is a different audience with a different need.

---

## Reproducing a number by hand

```bash
cd apps/api
python manage.py shell -c "
from plane.db.models import Workspace
from plane.app.services.orca.executive_metrics import executive_metrics
import json
w = Workspace.objects.get(slug='acme')
print(json.dumps(executive_metrics(w, period='30d', use_cache=False), indent=2, default=str))
"
```

`use_cache=False` is the point: a disputed number should be recomputed, not
re-read.

The fixed-dataset tests in
`apps/api/plane/tests/unit/orca/test_executive_metrics.py` build three areas,
two runs and about forty items, and assert every number above as a literal with
its arithmetic in a comment. If you change a definition here, that file is what
tells you what else you changed.
