/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IUnitQueueRow } from "@plane/types";
import { Loader } from "@plane/ui";
// components
import { QueueItemRow } from "./queue-item-row";

type Props = {
  workspaceSlug: string;
  unitId: string;
  rows: IUnitQueueRow[];
  isLoading?: boolean;
  emptyLabel: string;
  busyIssueId?: string | null;
  onClaim: (row: IUnitQueueRow) => void;
  onAssign: (row: IUnitQueueRow) => void;
  onReturn: (row: IUnitQueueRow) => void;
  onTransfer: (row: IUnitQueueRow) => void;
};

/**
 * @description A section of an area's queue. Empty is a real state worth
 * saying out loud — an empty inbox is the point of the screen, not a failure
 * to load.
 */
export const QueueList = observer(function QueueList(props: Props) {
  const { workspaceSlug, unitId, rows, isLoading, emptyLabel, busyIssueId, onClaim, onAssign, onReturn, onTransfer } =
    props;
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <Loader className="space-y-2">
        <Loader.Item height="44px" />
        <Loader.Item height="44px" />
        <Loader.Item height="44px" />
      </Loader>
    );
  }

  if (rows.length === 0) {
    return <p className="text-custom-text-400 text-body-xs-regular px-3 py-6 text-center">{t(emptyLabel)}</p>;
  }

  return (
    <div className="border-custom-border-200 rounded-md border">
      {rows.map((row) => (
        <QueueItemRow
          key={row.id}
          workspaceSlug={workspaceSlug}
          unitId={unitId}
          row={row}
          busy={busyIssueId === row.issue_id}
          onClaim={onClaim}
          onAssign={onAssign}
          onReturn={onReturn}
          onTransfer={onTransfer}
        />
      ))}
    </div>
  );
});
