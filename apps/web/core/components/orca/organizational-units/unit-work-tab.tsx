/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IUnitQueueRow } from "@plane/types";
// components
import { AssignMemberModal } from "./assign-member-modal";
import { QueueList } from "./queue-list";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description What an area has to do, in two sections that answer two
 * different questions: what is waiting for somebody (the inbox), and what is
 * already being done and by whom. Keeping them apart is the point — a single
 * list sorted by date hides the fact that half of it has no owner.
 */
export const OrganizationalUnitWorkTab = observer(function OrganizationalUnitWorkTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);
  const [busyIssueId, setBusyIssueId] = useState<string | null>(null);
  const [rowToAssign, setRowToAssign] = useState<IUnitQueueRow | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    // No routing_state filter: the tab shows both halves, so it asks for all
    // of them and splits below rather than making two round trips.
    store
      .fetchQueue(workspaceSlug, unitId, { routing_state: "" })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, unitId, store]);

  const rows = store.getQueueByUnitId(unitId);
  const waiting = useMemo(
    () => rows.filter((row) => row.routing_state === "queued" || row.routing_state === "allocation_failed"),
    [rows]
  );
  const inProgress = useMemo(() => rows.filter((row) => row.routing_state === "assigned"), [rows]);

  const byExecutor = useMemo(() => {
    const groups = new Map<string, { name: string; rows: IUnitQueueRow[] }>();
    for (const row of inProgress) {
      const key = row.primary_executor?.id ?? "unassigned";
      const name = row.primary_executor?.display_name ?? t(`${OU}.queue.unknown_executor`);
      if (!groups.has(key)) groups.set(key, { name, rows: [] });
      groups.get(key)!.rows.push(row);
    }
    return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [inProgress, t]);

  const run = async (row: IUnitQueueRow, action: () => Promise<void>, successKey: string, errorKey: string) => {
    setBusyIssueId(row.issue_id);
    try {
      await action();
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(successKey) });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(errorKey) });
    } finally {
      setBusyIssueId(null);
    }
  };

  const handleClaim = (row: IUnitQueueRow) =>
    run(
      row,
      () => store.claim(workspaceSlug, unitId, row.project_id, row.issue_id),
      `${OU}.queue.toast.claimed`,
      `${OU}.queue.toast.not_claimed`
    );

  const handleReturn = (row: IUnitQueueRow) =>
    run(
      row,
      () => store.returnToQueue(workspaceSlug, unitId, row.project_id, row.issue_id),
      `${OU}.queue.toast.returned`,
      `${OU}.queue.toast.not_returned`
    );

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h4 className="text-body-xs-medium text-custom-text-200">
          {t(`${OU}.queue.inbox`)}
          <span className="text-custom-text-400 ml-1.5">{waiting.length}</span>
        </h4>
        <QueueList
          workspaceSlug={workspaceSlug}
          rows={waiting}
          isLoading={isLoading}
          emptyLabel={`${OU}.queue.inbox_empty`}
          busyIssueId={busyIssueId}
          onClaim={handleClaim}
          onAssign={setRowToAssign}
          onReturn={handleReturn}
        />
      </section>

      <section className="space-y-3">
        <h4 className="text-body-xs-medium text-custom-text-200">
          {t(`${OU}.queue.in_progress`)}
          <span className="text-custom-text-400 ml-1.5">{inProgress.length}</span>
        </h4>
        {!isLoading && inProgress.length === 0 && (
          <p className="text-custom-text-400 text-body-xs-regular px-3 py-6 text-center">
            {t(`${OU}.queue.in_progress_empty`)}
          </p>
        )}
        {byExecutor.map((group) => (
          <div key={group.name} className="space-y-1">
            <p className="text-custom-text-300 text-body-2xs-medium">{group.name}</p>
            <QueueList
              workspaceSlug={workspaceSlug}
              rows={group.rows}
              emptyLabel={`${OU}.queue.in_progress_empty`}
              busyIssueId={busyIssueId}
              onClaim={handleClaim}
              onAssign={setRowToAssign}
              onReturn={handleReturn}
            />
          </div>
        ))}
      </section>

      <AssignMemberModal
        isOpen={rowToAssign !== null}
        onClose={() => setRowToAssign(null)}
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        row={rowToAssign}
      />
    </div>
  );
});
