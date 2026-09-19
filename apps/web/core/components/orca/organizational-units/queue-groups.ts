/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { IQueueRow } from "@plane/types";

/**
 * One visual block in a queue list: either a lone work item or every step of
 * a process run that appears in this list, shown together.
 *
 * The backend order (overdue first, then oldest) is the order of first
 * appearance. Later steps of a run already opened are pulled into that
 * first group rather than opening a second one further down.
 */
export type QueueListBlock =
  | { kind: "item"; row: IQueueRow }
  | { kind: "process"; key: string; name: string; done: number; total: number; rows: IQueueRow[] };

/**
 * @description Group a page of queue rows by `ProcessInstanceReference`.
 * @param rows Rows in the order the API returned them.
 * @returns Blocks the list should render, in that same first-seen order.
 */
export function groupQueueRows(rows: IQueueRow[]): QueueListBlock[] {
  const byProcess = new Map<string, IQueueRow[]>();
  for (const row of rows) {
    if (!row.process) continue;
    const key = `${row.process.source}:${row.process.instance_id}`;
    const existing = byProcess.get(key);
    if (existing) existing.push(row);
    else byProcess.set(key, [row]);
  }

  const blocks: QueueListBlock[] = [];
  const opened = new Set<string>();
  for (const row of rows) {
    if (!row.process) {
      blocks.push({ kind: "item", row });
      continue;
    }
    const key = `${row.process.source}:${row.process.instance_id}`;
    if (opened.has(key)) continue;
    opened.add(key);
    const grouped = byProcess.get(key) ?? [row];
    blocks.push({
      kind: "process",
      key,
      name: row.process.template_name,
      done: row.process.done,
      total: row.process.total,
      rows: grouped,
    });
  }
  return blocks;
}
