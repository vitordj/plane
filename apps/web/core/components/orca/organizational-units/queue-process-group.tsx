/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { ChevronDown } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IQueueRow } from "@plane/types";
// components
import { QueueItemRow } from "./queue-item-row";

type Props = {
  workspaceSlug: string;
  unitId: string;
  name: string;
  done: number;
  total: number;
  rows: IQueueRow[];
  onAssign: (row: IQueueRow) => void;
  onTransfer?: (row: IQueueRow) => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Every step of one process run that appears in this list,
 * collapsible, with the instance's `n/m` — not how many of those steps
 * happened to land on the page.
 *
 * Open by default: a coordinator opening the inbox to pick the next thing
 * should see the work, not a closed folder they have to remember to expand.
 */
export const QueueProcessGroup = observer(function QueueProcessGroup(props: Props) {
  const { workspaceSlug, unitId, name, done, total, rows, onAssign, onTransfer } = props;
  const { t } = useTranslation();

  return (
    <details className="group/process" open>
      <summary className="text-xs text-custom-text-200 hover:bg-custom-background-90 focus-visible:ring-custom-primary-100 flex cursor-pointer list-none items-center gap-2 px-4 py-2 outline-none focus-visible:ring-2 [&::-webkit-details-marker]:hidden">
        <ChevronDown className="size-3.5 shrink-0 -rotate-90 transition-transform group-open/process:rotate-0" />
        <span className="truncate font-medium">{name}</span>
        <span className="text-custom-text-400 tabular-nums">{t(`${OU}.work.process_progress`, { done, total })}</span>
      </summary>
      <div className="divide-custom-border-200 divide-y border-t">
        {rows.map((row) => (
          <QueueItemRow
            key={row.issue_id}
            workspaceSlug={workspaceSlug}
            unitId={unitId}
            row={row}
            onAssign={onAssign}
            onTransfer={onTransfer}
          />
        ))}
      </div>
    </details>
  );
});
