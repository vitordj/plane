/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import type { IQueueCapabilities, IQueueItem } from "@plane/types";
// components
import { QueueItemRow } from "./queue-item-row";

type Props = {
  workspaceSlug: string;
  unitId: string;
  title: string;
  emptyLabel: string;
  items: IQueueItem[];
  capabilities: IQueueCapabilities;
  onAssign: (item: IQueueItem) => void;
  onTransfer: (item: IQueueItem) => void;
};

/**
 * @description One titled section of an area's board — the inbox, the work in
 * progress, the items that need attention. The sections differ only in which
 * rows they hold and what they are called, so they share this component and
 * the ordering the server chose: overdue first, then longest waiting.
 */
export const QueueList = observer(function QueueList(props: Props) {
  const { workspaceSlug, unitId, title, emptyLabel, items, capabilities, onAssign, onTransfer } = props;

  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm text-custom-text-200 flex items-center gap-2 font-medium">
        {title}
        <span className="text-xs text-custom-text-400">{items.length}</span>
      </h4>
      {items.length === 0 ? (
        <p className="text-sm text-custom-text-300 border-custom-border-200 rounded border border-dashed px-4 py-6 text-center">
          {emptyLabel}
        </p>
      ) : (
        <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
          {items.map((item) => (
            <QueueItemRow
              key={item.id}
              workspaceSlug={workspaceSlug}
              unitId={unitId}
              item={item}
              capabilities={capabilities}
              onAssign={onAssign}
              onTransfer={onTransfer}
            />
          ))}
        </div>
      )}
    </section>
  );
});
