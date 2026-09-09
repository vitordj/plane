/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import type { IQueueRow } from "@plane/types";
import { Loader } from "@plane/ui";
// components
import { QueueItemRow } from "./queue-item-row";

type Props = {
  workspaceSlug: string;
  unitId: string;
  rows: IQueueRow[];
  isLoading: boolean;
  /** Shown in place of the list when there is nothing in it. */
  emptyMessage: string;
  onAssign: (row: IQueueRow) => void;
  onTransfer?: (row: IQueueRow) => void;
};

/**
 * @description A list of queued work items, in the order the API returned
 * them. Deliberately does not sort: the backend puts overdue allocations
 * first, and a second ordering here would mean the row a coordinator owes sits
 * in a different place depending on which screen they opened.
 */
export const QueueList = observer(function QueueList(props: Props) {
  const { workspaceSlug, unitId, rows, isLoading, emptyMessage, onAssign, onTransfer } = props;

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="56px" />
        <Loader.Item height="56px" />
        <Loader.Item height="56px" />
      </Loader>
    );

  if (rows.length === 0) return <p className="text-sm text-custom-text-300 py-8 text-center">{emptyMessage}</p>;

  return (
    <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
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
  );
});
