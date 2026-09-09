/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IQueueRow } from "@plane/types";
import { Avatar } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
// components
import { AssignMemberModal } from "./assign-member-modal";
import { DecisionTimeline } from "./decision-timeline";
import { QueueList } from "./queue-list";
import { TransferUnitModal } from "./transfer-unit-modal";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description A work item whose due date is in the past, in this viewer's
 * calendar. Compared as a calendar day, not a timestamp: a due date is a
 * day, and calling something overdue at 00:01 because of a timezone would
 * be a different product.
 */
function isTargetDateOverdue(targetDate: string | null): boolean {
  if (!targetDate) return false;
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return targetDate.slice(0, 10) < `${today.getFullYear()}-${month}-${day}`;
}

/**
 * @description What an area is working on: what is waiting on somebody, and
 * what somebody is already doing, plus the rows that need a coordinator's eye
 * before the inbox (due date, paused, nobody on it who can work).
 */
export const OrganizationalUnitWorkTab = observer(function OrganizationalUnitWorkTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [rowToAssign, setRowToAssign] = useState<IQueueRow | null>(null);
  const [rowToTransfer, setRowToTransfer] = useState<IQueueRow | null>(null);

  const queue = store.getQueueByUnitId(unitId);
  const canTransfer = queue.viewer.is_admin || queue.viewer.is_coordinator;
  const onTransfer = canTransfer ? setRowToTransfer : undefined;

  useEffect(() => {
    store.fetchQueue(workspaceSlug, unitId).catch(() => undefined);
  }, [workspaceSlug, unitId, store]);

  const attention = useMemo(() => {
    const rows = [...queue.waiting, ...queue.inProgress, ...queue.suspended];
    const overdueDate: IQueueRow[] = [];
    const suspended: IQueueRow[] = [];
    const unavailable: IQueueRow[] = [];
    const noDate: IQueueRow[] = [];
    const seen = new Set<string>();
    const take = (bucket: IQueueRow[], row: IQueueRow) => {
      if (seen.has(row.issue_id)) return;
      seen.add(row.issue_id);
      bucket.push(row);
    };
    for (const row of rows) {
      if (isTargetDateOverdue(row.target_date)) take(overdueDate, row);
    }
    for (const row of rows) {
      if (row.routing_state === "suspended") take(suspended, row);
    }
    for (const row of rows) {
      if (row.queue_reason === "executor_unavailable") take(unavailable, row);
    }
    for (const row of queue.inProgress) {
      if (!row.target_date) take(noDate, row);
    }
    return { overdueDate, suspended, unavailable, noDate };
  }, [queue.waiting, queue.inProgress, queue.suspended]);

  const attentionCount =
    attention.overdueDate.length + attention.suspended.length + attention.unavailable.length + attention.noDate.length;

  // Nobody on an item is still an answer to "who is carrying what", so an
  // unassigned group is kept rather than dropped — an item in progress with no
  // executor is exactly the thing a coordinator should see.
  const executorGroups = useMemo(() => {
    const groups = new Map<string, { name: string; avatarUrl: string; rows: IQueueRow[] }>();
    for (const row of queue.inProgress) {
      const key = row.primary_executor?.id ?? "unassigned";
      const existing = groups.get(key);
      if (existing) existing.rows.push(row);
      else
        groups.set(key, {
          name: row.primary_executor?.display_name ?? t(`${OU}.work.unassigned`),
          avatarUrl: row.primary_executor?.avatar_url ?? "",
          rows: [row],
        });
    }
    return [...groups.entries()].toSorted((a, b) => a[1].name.localeCompare(b[1].name));
  }, [queue.inProgress, t]);

  const renderAttentionBucket = (key: string, rows: IQueueRow[]) => {
    if (rows.length === 0) return null;
    return (
      <div key={key} className="flex flex-col gap-2">
        <h5 className="text-xs text-custom-text-300 font-medium">{t(`${OU}.work.${key}`)}</h5>
        <QueueList
          workspaceSlug={workspaceSlug}
          unitId={unitId}
          rows={rows}
          isLoading={false}
          emptyMessage={t(`${OU}.work.empty_attention`)}
          onAssign={setRowToAssign}
          onTransfer={onTransfer}
        />
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-6">
      {attentionCount > 0 && (
        <section className="flex flex-col gap-3">
          <h4 className="text-sm text-custom-text-200 flex items-center gap-2 font-medium">
            {t(`${OU}.work.attention`)}
            <span className="text-xs text-custom-text-400">{attentionCount}</span>
          </h4>
          {renderAttentionBucket("attention_overdue_date", attention.overdueDate)}
          {renderAttentionBucket("attention_suspended", attention.suspended)}
          {renderAttentionBucket("attention_unavailable", attention.unavailable)}
          {renderAttentionBucket("attention_no_date", attention.noDate)}
        </section>
      )}

      <section className="flex flex-col gap-3">
        <h4 className="text-sm text-custom-text-200 flex items-center gap-2 font-medium">
          {t(`${OU}.work.inbox`)}
          <span className="text-xs text-custom-text-400">{queue.waiting.length}</span>
        </h4>
        <QueueList
          workspaceSlug={workspaceSlug}
          unitId={unitId}
          rows={queue.waiting}
          isLoading={queue.loader}
          emptyMessage={t(`${OU}.work.empty_inbox`)}
          onAssign={setRowToAssign}
          onTransfer={onTransfer}
        />
      </section>

      <section className="flex flex-col gap-3">
        <h4 className="text-sm text-custom-text-200 flex items-center gap-2 font-medium">
          {t(`${OU}.work.in_progress`)}
          <span className="text-xs text-custom-text-400">{queue.inProgress.length}</span>
        </h4>
        {queue.loader || executorGroups.length === 0 ? (
          <QueueList
            workspaceSlug={workspaceSlug}
            unitId={unitId}
            rows={[]}
            isLoading={queue.loader}
            emptyMessage={t(`${OU}.work.empty_in_progress`)}
            onAssign={setRowToAssign}
            onTransfer={onTransfer}
          />
        ) : (
          <div className="flex flex-col gap-4">
            {executorGroups.map(([key, group]) => (
              <div key={key} className="flex flex-col gap-2">
                <div className="text-xs text-custom-text-300 flex items-center gap-2">
                  <Avatar name={group.name} src={group.avatarUrl} size="sm" />
                  <span className="truncate">{group.name}</span>
                  <span className="text-custom-text-400">
                    {t(`${OU}.work.executor_count`, { count: group.rows.length })}
                  </span>
                </div>
                <QueueList
                  workspaceSlug={workspaceSlug}
                  unitId={unitId}
                  rows={group.rows}
                  isLoading={false}
                  emptyMessage={t(`${OU}.work.empty_in_progress`)}
                  onAssign={setRowToAssign}
                  onTransfer={onTransfer}
                />
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h4 className="text-sm text-custom-text-200 font-medium">{t(`${OU}.work.decisions`)}</h4>
        <DecisionTimeline workspaceSlug={workspaceSlug} unitId={unitId} />
      </section>

      <AssignMemberModal
        isOpen={rowToAssign !== null}
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        subtitle={
          rowToAssign ? `${rowToAssign.project.identifier}-${rowToAssign.sequence_id} · ${rowToAssign.name}` : undefined
        }
        onAssign={(userId) => {
          if (!rowToAssign) return Promise.resolve();
          return store.assign(workspaceSlug, unitId, rowToAssign, userId);
        }}
        onClose={() => setRowToAssign(null)}
      />

      <TransferUnitModal
        isOpen={rowToTransfer !== null}
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        projectId={rowToTransfer?.project.id ?? ""}
        subtitle={
          rowToTransfer
            ? `${rowToTransfer.project.identifier}-${rowToTransfer.sequence_id} · ${rowToTransfer.name}`
            : undefined
        }
        onTransferred={(destinationId) => {
          if (!rowToTransfer) return Promise.resolve();
          return store.transfer(workspaceSlug, unitId, rowToTransfer, destinationId);
        }}
        onClose={() => setRowToTransfer(null)}
      />
    </div>
  );
});
