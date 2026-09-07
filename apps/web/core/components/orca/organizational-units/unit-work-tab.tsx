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
import { QueueList } from "./queue-list";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description What an area is working on: what is waiting on somebody, and
 * what somebody is already doing.
 *
 * The inbox keeps the order the backend sent — overdue allocations first — so
 * the row the area owes is the row a coordinator reads first. "In progress" is
 * grouped by executor instead, because the question it answers is "who is
 * carrying what", which a flat list by age does not.
 */
export const OrganizationalUnitWorkTab = observer(function OrganizationalUnitWorkTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [rowToAssign, setRowToAssign] = useState<IQueueRow | null>(null);

  const queue = store.getQueueByUnitId(unitId);

  useEffect(() => {
    store.fetchQueue(workspaceSlug, unitId).catch(() => undefined);
  }, [workspaceSlug, unitId, store]);

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

  return (
    <div className="flex flex-col gap-6">
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
                />
              </div>
            ))}
          </div>
        )}
      </section>

      <AssignMemberModal
        isOpen={rowToAssign !== null}
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        row={rowToAssign}
        onClose={() => setRowToAssign(null)}
      />
    </div>
  );
});
