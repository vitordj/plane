/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { ChevronDown, ChevronRight, Workflow } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IUnitQueueRow } from "@plane/types";
// components
import { QueueList } from "./queue-list";

type Props = {
  workspaceSlug: string;
  unitId: string;
  rows: IUnitQueueRow[];
  emptyLabel: string;
  busyIssueId?: string | null;
  onClaim: (row: IUnitQueueRow) => void;
  onAssign: (row: IUnitQueueRow) => void;
  onReturn: (row: IUnitQueueRow) => void;
  onTransfer: (row: IUnitQueueRow) => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The same queue, with the steps of one process run kept
 * together. Four steps of one onboarding shown as four unrelated rows is how a
 * coordinator ends up assigning step three before step one — the grouping is
 * what makes the shape of the work visible.
 *
 * Collapsed by default only when the run is large enough for that to help:
 * hiding two rows behind a heading is worse than showing them.
 */
export const QueueProcessGroup = observer(function QueueProcessGroup(props: Props) {
  const { workspaceSlug, unitId, rows, emptyLabel, busyIssueId, onClaim, onAssign, onReturn, onTransfer } = props;
  const { t } = useTranslation();

  const loose = rows.filter((row) => !row.process);
  const groups = new Map<string, IUnitQueueRow[]>();
  for (const row of rows) {
    if (!row.process) continue;
    const key = `${row.process.source}:${row.process.instance_id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(row);
  }

  const listProps = { workspaceSlug, unitId, busyIssueId, onClaim, onAssign, onReturn, onTransfer };

  return (
    <div className="space-y-3">
      {[...groups.entries()].map(([key, groupRows]) => (
        <ProcessGroup
          key={key}
          label={groupRows[0].process!.template_name || groupRows[0].process!.instance_id}
          instanceId={groupRows[0].process!.instance_id}
          progress={groupRows[0].process!.progress}
          rows={groupRows}
          listProps={listProps}
          emptyLabel={emptyLabel}
          t={t}
        />
      ))}

      {(loose.length > 0 || groups.size === 0) && (
        <QueueList {...listProps} rows={loose} emptyLabel={emptyLabel} />
      )}
    </div>
  );
});

type GroupProps = {
  label: string;
  instanceId: string;
  progress: { done: number; total: number };
  rows: IUnitQueueRow[];
  listProps: Omit<Props, "rows" | "emptyLabel">;
  emptyLabel: string;
  t: (key: string, values?: Record<string, unknown>) => string;
};

const ProcessGroup = observer(function ProcessGroup(props: GroupProps) {
  const { label, instanceId, progress, rows, listProps, emptyLabel, t } = props;
  const [isOpen, setIsOpen] = useState(rows.length <= 3);

  return (
    <div className="border-custom-border-200 rounded-md border">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        className="hover:bg-custom-background-90 flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <Workflow className="text-custom-text-300 size-3.5" />
        <span className="text-body-xs-medium text-custom-text-100 truncate">{label}</span>
        <span className="text-custom-text-400 text-body-2xs-regular truncate">{instanceId}</span>
        <span className="text-custom-text-300 text-body-2xs-regular ml-auto shrink-0">
          {t(`${OU}.queue.process_progress`, { done: progress.done, total: progress.total })}
        </span>
      </button>
      {isOpen && (
        <div className="border-custom-border-200 border-t">
          <QueueList {...listProps} rows={rows} emptyLabel={emptyLabel} />
        </div>
      )}
    </div>
  );
});
