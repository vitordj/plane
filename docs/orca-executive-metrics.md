# What every number on the executive page means

One definition per indicator, with the SQL that reproduces it. A page whose
numbers cannot be checked is a page people argue with instead of act on, and
the first argument is always about what the number means.

The implementation is `apps/api/plane/app/services/orca/executive_metrics.py`
and the endpoint is `GET /api/orca/workspaces/{slug}/executive/?period=30d`
(Workspace Admin; `period` is `7d`, `30d` or `90d`, and anything else is
refused rather than guessed at).

## Three conventions

- **Open means not closed natively.** `completed` and `cancelled` are both
  closed. A report that counted cancelled work as outstanding would make every
  tidy-up look like a problem.
- **Percentiles, not averages.** One work item that sat for three months moves
  an average enough to hide a queue that is otherwise fine, and moves a p90 by
  exactly as much as it should.
- **A ratio carries its sample.** 75% of four decisions is noise. The count is
  returned alongside, and the interface shows it in the tooltip.

Substitute `:unit` for the area's id, `:since` for the start of the window and
`:workspace` for the workspace id.

## Per area

### `backlog` — open

Work the area owns that is neither done nor cancelled.

```sql
SELECT count(*)
FROM issue_organizational_units link
JOIN issues i ON i.id = link.issue_id
LEFT JOIN states s ON s.id = i.state_id
WHERE link.organizational_unit_id = :unit
  AND link.deleted_at IS NULL
  AND (s.group IS NULL OR s.group NOT IN ('completed', 'cancelled'));
```

### `queued` — waiting

Of the open work, what nobody is executing. `allocation_failed` counts: it is
waiting, and it is the kind of waiting that needs somebody to fix something.

```sql
-- as above, AND link.routing_state IN ('queued', 'allocation_failed')
```

### `assignment_overdue` — late to be taken

Waiting past the time the area's own policy promised somebody would take it.
Null `assignment_due_at` means the area made no promise, and never counts.

```sql
-- as `queued`, AND link.assignment_due_at < now()
```

### `target_overdue` — past its date

The work item's own target date has passed and it is not closed. This is the
native date, deliberately: it is what the people doing the work see. The
area's promise is a different thing and lives in `orca_issue_service_levels`.

```sql
-- as `backlog`, AND i.target_date < current_date
```

### `queue_age` — waited (p50 / p90)

How long the currently waiting work has been waiting, in seconds. Empty queue
gives `null`, not zero: zero would read as "instant".

```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY value)
FROM (
  SELECT extract(epoch FROM (now() - queued_at)) AS value
  FROM issue_organizational_units
  WHERE organizational_unit_id = :unit
    AND routing_state IN ('queued', 'allocation_failed')
    AND queued_at IS NOT NULL
    AND deleted_at IS NULL
) samples;
```

### `throughput` — finished

Work items of this area that reached a completed state in the window. Uses
Plane's own `completed_at`, so it agrees with every other report in the
product. Cancelled work is not throughput.

```sql
SELECT count(DISTINCT i.id)
FROM issues i
JOIN issue_organizational_units link ON link.issue_id = i.id AND link.deleted_at IS NULL
JOIN states s ON s.id = i.state_id
WHERE link.organizational_unit_id = :unit
  AND i.completed_at >= :since
  AND s.group = 'completed';
```

### `cycle_time` — took (p50 / p90)

Creation to completion, in seconds, for the work finished in the window.

```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY value)
FROM (
  SELECT extract(epoch FROM (i.completed_at - i.created_at)) AS value
  FROM issues i
  JOIN issue_organizational_units link ON link.issue_id = i.id AND link.deleted_at IS NULL
  WHERE link.organizational_unit_id = :unit
    AND i.completed_at IS NOT NULL
    AND i.completed_at >= :since
    AND i.deleted_at IS NULL
) samples;
```

### `concentration_top3` — top 3 carry

What share of the area's open assigned work its three busiest people hold.

**High is not automatically wrong.** An area of three people is always 100%,
which is why the count of open items and the number of executors travel with
the ratio. What this finds is the area of twelve where three hold 90%.

```sql
WITH per_person AS (
  SELECT link.primary_executor_id, count(*) AS open_items
  FROM issue_organizational_units link
  JOIN issues i ON i.id = link.issue_id
  LEFT JOIN states s ON s.id = i.state_id
  WHERE link.organizational_unit_id = :unit
    AND link.routing_state = 'assigned'
    AND link.deleted_at IS NULL
    AND (s.group IS NULL OR s.group NOT IN ('completed', 'cancelled'))
  GROUP BY 1
)
SELECT (SELECT sum(open_items) FROM (SELECT open_items FROM per_person ORDER BY open_items DESC LIMIT 3) top)
       / nullif((SELECT sum(open_items) FROM per_person), 0)::float;
```

### `auto_assign_kept_ratio` — auto kept

Of the automatic assignments made in the window, how many nobody overrode.
Overridden means a later decision names it in `supersedes_id`.

A low share is the strongest evidence available that the ranking is wrong for
this area — people are correcting it by hand — and the reasonable response is
to look at the area's policy, not at the people overriding it.

```sql
WITH auto AS (
  SELECT id FROM orca_assignment_decisions
  WHERE organizational_unit_id = :unit
    AND effective_mode = 'least_loaded'
    AND outcome = 'assigned'
    AND created_at >= :since
)
SELECT (SELECT count(*) FROM auto)                                        AS decisions,
       (SELECT count(DISTINCT supersedes_id) FROM orca_assignment_decisions
         WHERE supersedes_id IN (SELECT id FROM auto))                    AS overridden;
-- ratio = (decisions - overridden) / decisions
```

## Per workspace, by process

`running` counts instances whose status is `running`; `completed` counts those
finished inside the window. `lead_time` is `completed_at - started_at` for
those, at p50 and p90.

`slowest_steps` is the five step keys with the most work waiting right now —
what to look at first when a process is slow, rather than which run happens to
be the oldest.

```sql
SELECT item.step_key, count(*) AS waiting
FROM issue_organizational_units link
JOIN orca_process_instance_items item ON item.issue_id = link.issue_id AND item.deleted_at IS NULL
JOIN issues i ON i.id = link.issue_id
LEFT JOIN states s ON s.id = i.state_id
WHERE link.workspace_id = :workspace
  AND link.routing_state IN ('queued', 'allocation_failed')
  AND link.deleted_at IS NULL
  AND (s.group IS NULL OR s.group NOT IN ('completed', 'cancelled'))
GROUP BY 1 ORDER BY waiting DESC LIMIT 5;
```

## Caching

Five minutes per (workspace, period, area), in Django's cache. Short on
purpose: the page is for deciding something this week, so five minutes stale
costs nothing and an hour would have somebody acting on yesterday.

## What is deliberately not here

- **Anything per person, ranked.** The page reports areas. `concentration_top3`
  is about an area's shape, and it does not name anybody — a league table of
  people is a different product with different consequences.
- **Estimates and story points.** Load is counted in open work items (RFC A7
  leaves weighting open). An estimate-weighted number would look more precise
  and be less true wherever estimates are not kept up.
- **A materialized snapshot.** Phase 5.2 says to add one only if this exceeds
  two seconds against real data. Measure before building it.
