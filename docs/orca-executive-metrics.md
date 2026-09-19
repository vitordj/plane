# Executive metrics (Orca)

Every number on `:workspaceSlug/settings/organizational-units/executive`
comes from `GET /api/orca/workspaces/{slug}/executive/`
(`plane.app.services.orca.executive_metrics`). This page is the SQL that
produces each one, so a director (or a test) can reproduce the figure
without opening the UI.

The Python never uses SQL `NOW()`. Bind `:now` (timestamptz) and
`:today` (`(:now)::date`) once per request so a frozen clock in tests, and
every indicator on the same payload, agree. `:period_start` is
`:now - interval '7 days'` / `'30 days'` / `'90 days'` according to
`period` (`7d` | `30d` | `90d`, default `30d`).

Live rows only: every sidecar table below is filtered with
`deleted_at IS NULL`. Archived projects (`projects.archived_at IS NOT NULL`)
are out of the area queries, matching `_links()`.

Workspace Admin only. Counts include items in projects the reader is not a
`ProjectMember` of; those items are never listed. `hidden_count` is how many
were counted but must not be shown.

Cache: Django cache, 5 minutes, key
`orca:executive:{workspace_id}:{period}:{unit_id|all}`. The cached body is
built with no reader; `hidden_count` and delayed steps are filled per
request.

Optional `:unit_id` restricts every query to that area.

---

## Shared fragments

```sql
-- Projects this reader can actually open (F18). Workspace Admin is not
-- unrestricted here: listing requires a live ProjectMember.
-- :reader_id is the requesting user's UUID.
SELECT pm.project_id
FROM project_members pm
WHERE pm.member_id = :reader_id
  AND pm.workspace_id = :workspace_id
  AND pm.is_active
  AND pm.deleted_at IS NULL;

-- An issue is open when it has no native state, or the state's group is
-- not completed / cancelled.
-- open_issue(i): i.state_id IS NULL OR s.group NOT IN ('completed', 'cancelled')
```

---

## Per area

Join `issue_organizational_units iou` to `issues i` and `states s`
(`i.state_id = s.id`). Filter `iou.workspace_id = :workspace_id` and,
when set, `iou.organizational_unit_id = :unit_id`. Skip archived
projects: `p.archived_at IS NULL`.

### `backlog`

Items the area still owns that are not in a native completed/cancelled
group.

```sql
SELECT COUNT(*)
FROM issue_organizational_units iou
JOIN issues i ON i.id = iou.issue_id AND i.deleted_at IS NULL
JOIN projects p ON p.id = iou.project_id
LEFT JOIN states s ON s.id = i.state_id AND s.deleted_at IS NULL
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND (i.state_id IS NULL OR s.group NOT IN ('completed', 'cancelled'));
```

### `queued`

Coordinator inbox, including allocator failures that still need a person.

```sql
SELECT COUNT(*)
FROM issue_organizational_units iou
JOIN projects p ON p.id = iou.project_id
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND iou.routing_state IN ('queued', 'allocation_failed');
```

### `assignment_overdue`

Queued (same states) whose assignment SLA has passed.

```sql
SELECT COUNT(*)
FROM issue_organizational_units iou
JOIN projects p ON p.id = iou.project_id
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND iou.routing_state IN ('queued', 'allocation_failed')
  AND iou.assignment_due_at < :now;
```

### `target_overdue`

Open items whose native `target_date` is before today.

```sql
SELECT COUNT(*)
FROM issue_organizational_units iou
JOIN issues i ON i.id = iou.issue_id AND i.deleted_at IS NULL
JOIN projects p ON p.id = iou.project_id
LEFT JOIN states s ON s.id = i.state_id AND s.deleted_at IS NULL
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND (i.state_id IS NULL OR s.group NOT IN ('completed', 'cancelled'))
  AND i.target_date < :today;
```

### `queue_age_p50` / `queue_age_p90`

Postgres continuous percentile of `:now - queued_at` over queued rows
that have a `queued_at`. Empty sample → `NULL`, not 0.

```sql
SELECT
  percentile_cont(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (:now - iou.queued_at))
  ) AS queue_age_p50,
  percentile_cont(0.9) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (:now - iou.queued_at))
  ) AS queue_age_p90
FROM issue_organizational_units iou
JOIN projects p ON p.id = iou.project_id
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND iou.routing_state IN ('queued', 'allocation_failed')
  AND iou.queued_at IS NOT NULL;
```

`percentile_cont` interpolates at 1-based index `1 + (n - 1) * p`.

### `throughput`

Items of the area whose native `issues.completed_at` falls in the window.

```sql
SELECT COUNT(*)
FROM issue_organizational_units iou
JOIN issues i ON i.id = iou.issue_id AND i.deleted_at IS NULL
JOIN projects p ON p.id = iou.project_id
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND i.completed_at >= :period_start
  AND i.completed_at <= :now;
```

The sparkline is the same set grouped by calendar day of `completed_at`.
Days in `[period_start::date, :today]` with no completions are `0` so the
CSS bars have a stable width.

```sql
SELECT (i.completed_at AT TIME ZONE 'UTC')::date AS day, COUNT(*)
FROM issue_organizational_units iou
JOIN issues i ON i.id = iou.issue_id AND i.deleted_at IS NULL
JOIN projects p ON p.id = iou.project_id
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND i.completed_at >= :period_start
  AND i.completed_at <= :now
GROUP BY 1;
```

### `cycle_time_p50` / `cycle_time_p90`

`completed_at - created_at` of the throughput set, in seconds.

```sql
SELECT
  percentile_cont(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (i.completed_at - i.created_at))
  ) AS cycle_time_p50,
  percentile_cont(0.9) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (i.completed_at - i.created_at))
  ) AS cycle_time_p90
FROM issue_organizational_units iou
JOIN issues i ON i.id = iou.issue_id AND i.deleted_at IS NULL
JOIN projects p ON p.id = iou.project_id
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND i.completed_at >= :period_start
  AND i.completed_at <= :now
  AND i.completed_at IS NOT NULL
  AND i.created_at IS NOT NULL;
```

### `concentration_top3`

Share of **open assigned** items held by the three primary executors with
the most of them. No open assigned item → `NULL`.

```sql
WITH loads AS (
  SELECT iou.primary_executor_id, COUNT(*) AS n
  FROM issue_organizational_units iou
  JOIN issues i ON i.id = iou.issue_id AND i.deleted_at IS NULL
  JOIN projects p ON p.id = iou.project_id
  LEFT JOIN states s ON s.id = i.state_id AND s.deleted_at IS NULL
  WHERE iou.workspace_id = :workspace_id
    AND iou.organizational_unit_id = :unit_id
    AND iou.deleted_at IS NULL
    AND p.archived_at IS NULL
    AND iou.routing_state = 'assigned'
    AND iou.primary_executor_id IS NOT NULL
    AND (i.state_id IS NULL OR s.group NOT IN ('completed', 'cancelled'))
  GROUP BY iou.primary_executor_id
),
ranked AS (
  SELECT n FROM loads ORDER BY n DESC LIMIT 3
)
SELECT SUM(n)::float / NULLIF((SELECT SUM(n) FROM loads), 0)
FROM ranked;
```

### `auto_assign_kept_ratio`

`least_loaded` decisions in the window that were **not** later replaced by
a human decision (`trigger` in `ui_coordinator`, `reassign`, `ui_claim`
and `decided_by_id` set), over the total `least_loaded` in the window.
Denominator 0 → `NULL`, never `0`.

```sql
SELECT
  COUNT(*) FILTER (
    WHERE NOT EXISTS (
      SELECT 1
      FROM orca_assignment_decisions later
      WHERE later.issue_id = d.issue_id
        AND later.created_at > d.created_at
        AND later.trigger IN ('ui_coordinator', 'reassign', 'ui_claim')
        AND later.decided_by_id IS NOT NULL
        AND later.deleted_at IS NULL
    )
  )::float / NULLIF(COUNT(*), 0)
FROM orca_assignment_decisions d
WHERE d.workspace_id = :workspace_id
  AND d.organizational_unit_id = :unit_id
  AND d.effective_mode = 'least_loaded'
  AND d.created_at >= :period_start
  AND d.created_at <= :now
  AND d.deleted_at IS NULL;
```

### `hidden_count` (per area, on the table)

Live links whose project the reader is not a member of. Independent of
which indicator the column shows; the drill-down uses a tighter count.

```sql
SELECT COUNT(*)
FROM issue_organizational_units iou
JOIN projects p ON p.id = iou.project_id
WHERE iou.workspace_id = :workspace_id
  AND iou.organizational_unit_id = :unit_id
  AND iou.deleted_at IS NULL
  AND p.archived_at IS NULL
  AND iou.project_id NOT IN (
    SELECT pm.project_id
    FROM project_members pm
    WHERE pm.member_id = :reader_id
      AND pm.workspace_id = :workspace_id
      AND pm.is_active
      AND pm.deleted_at IS NULL
  );
```

---

## Per process

Universe: `orca_process_instances` that are `running`, or `completed` with
`completed_at` in the window. Optional `:unit_id` keeps instances that have
at least one item linked to that area.

```sql
SELECT *
FROM orca_process_instances pi
WHERE pi.workspace_id = :workspace_id
  AND pi.deleted_at IS NULL
  AND (
    pi.status = 'running'
    OR (
      pi.status = 'completed'
      AND pi.completed_at >= :period_start
      AND pi.completed_at <= :now
    )
  );
```

### `running` / `completed`

```sql
SELECT
  COUNT(*) FILTER (WHERE status = 'running') AS running,
  COUNT(*) FILTER (WHERE status = 'completed') AS completed
FROM /* universe above */;
```

### `lead_time_p50` / `lead_time_p90`

`completed_at - started_at` of completed instances in the universe.

```sql
SELECT
  percentile_cont(0.5) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (pi.completed_at - pi.started_at))
  ) AS lead_time_p50,
  percentile_cont(0.9) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (pi.completed_at - pi.started_at))
  ) AS lead_time_p90
FROM orca_process_instances pi
WHERE /* universe, status = 'completed' */
  AND pi.started_at IS NOT NULL
  AND pi.completed_at IS NOT NULL;
```

### Delayed steps

Open process items whose promised date has passed. Prefer
`orca_issue_service_levels.completion_due_at` when that timestamp is
already late; otherwise `assignment_due_at`. Ranked by how late.
**Items in projects the reader cannot open are omitted** — they would be
a list of work the person cannot follow (F18).

```sql
SELECT
  item.process_instance_id,
  pi.template_name,
  item.step_key,
  item.issue_id,
  CASE
    WHEN sla.completion_due_at < :now THEN sla.completion_due_at
    ELSE sla.assignment_due_at
  END AS due_at,
  CASE
    WHEN sla.completion_due_at < :now THEN 'completion'
    ELSE 'assignment'
  END AS kind,
  EXTRACT(EPOCH FROM (
    :now - CASE
      WHEN sla.completion_due_at < :now THEN sla.completion_due_at
      ELSE sla.assignment_due_at
    END
  )) AS late_seconds
FROM orca_process_instance_items item
JOIN orca_process_instances pi
  ON pi.id = item.process_instance_id AND pi.deleted_at IS NULL
JOIN issues i ON i.id = item.issue_id AND i.deleted_at IS NULL
LEFT JOIN states s ON s.id = i.state_id AND s.deleted_at IS NULL
LEFT JOIN orca_issue_service_levels sla
  ON sla.issue_id = i.id AND sla.deleted_at IS NULL
WHERE item.deleted_at IS NULL
  AND pi.id IN (/* universe */)
  AND (i.state_id IS NULL OR s.group NOT IN ('completed', 'cancelled'))
  AND (
    sla.completion_due_at < :now
    OR sla.assignment_due_at < :now
  )
  AND item.issue_id IN (
    SELECT i2.id
    FROM issues i2
    JOIN project_members pm
      ON pm.project_id = i2.project_id
     AND pm.member_id = :reader_id
     AND pm.workspace_id = :workspace_id
     AND pm.is_active
     AND pm.deleted_at IS NULL
  )
ORDER BY late_seconds DESC
LIMIT 5;
```

---

## Drill-down

`GET /api/orca/workspaces/{slug}/executive/drill-down/?unit=&metric=&period=`

`metric` is one of `backlog`, `queued`, `assignment_overdue`,
`target_overdue`, `throughput`. The filter is the same `WHERE` as the
matching count above. `results` keeps only rows whose `project_id` is in
the reader's `project_members`. `hidden_count` is how many matching rows
were dropped. Action flags on every row are all `false`.

---

## What this file is not

Item 5.2 (a nightly `OrcaExecutiveSnapshot`) is intentionally absent.
It exists only if 5.1 exceeds 2 seconds in staging with real data. There
is no staging measurement in this tree, so there is no snapshot table.
