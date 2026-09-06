/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { RefreshCw } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { IQueueItem } from "@plane/types";
import { Loader } from "@plane/ui";
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
 * @description The area's board: what is waiting, what is being worked on, and
 * what needs a person to look at it.
 *
 * The three sections are one read, not three: the queue endpoint answers with
 * everything the area owns (`routing_state=all`), and the split happens here,
 * so a coordinator never sees an item in two sections because two requests
 * caught it mid-move. "Attention" is a view over the same rows rather than a
 * fourth state — an overdue item is still queued, a suspended one still
 * belongs to the area.
 */
export const OrganizationalUnitWorkTab = observer(function OrganizationalUnitWorkTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);
  const [assigningItem, setAssigningItem] = useState<IQueueItem | null>(null);
  const [transferringItem, setTransferringItem] = useState<IQueueItem | null>(null);

  const load = useMemo(
    () => () => {
      setIsLoading(true);
      return store
        .fetchQueue(workspaceSlug, unitId, { routingState: "all" })
        .catch(() => undefined)
        .finally(() => setIsLoading(false));
    },
    [workspaceSlug, unitId, store]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const items = store.getQueueByUnitId(unitId);
  const capabilities = store.getCapabilitiesByUnitId(unitId);

  const inbox = items.filter((item) => item.routing_state === "queued" || item.routing_state === "allocation_failed");
  const inProgress = items.filter((item) => item.routing_state === "assigned");
  const attention = items.filter(
    (item) =>
      item.assignment_overdue ||
      item.routing_state === "suspended" ||
      item.routing_state === "allocation_failed" ||
      (!!item.target_date && new Date(item.target_date) < new Date() && item.state_group !== "completed")
  );

  // The inbox split in two: items that belong to a process instance, grouped
  // by it, and everything else. Grouping the whole inbox would put a single
  // loose item under a heading it does not have.
  const inboxLoose = inbox.filter((item) => !item.process);
  const inboxByProcess = useMemo(() => {
    const groups = new Map<string, { key: string; name: string; done: number; total: number; items: IQueueItem[] }>();
    for (const item of inbox) {
      if (!item.process) continue;
      const key = item.process.instance_id;
      const group = groups.get(key) ?? {
        key,
        name: `${item.process.template_name || item.process.source} · ${item.process.external_instance_id}`,
        done: item.process.done,
        total: item.process.total,
        items: [],
      };
      group.items.push(item);
      groups.set(key, group);
    }
    return [...groups.values()].toSorted((a, b) => a.name.localeCompare(b.name));
  }, [inbox]);

  // Grouped by executor, so "who is carrying what" is readable without
  // counting rows. The order inside each group is still the server's.
  const byExecutor = useMemo(() => {
    const groups = new Map<string, { name: string; items: IQueueItem[] }>();
    for (const item of inProgress) {
      const key = item.primary_executor ?? "unassigned";
      const name = item.primary_executor_detail?.display_name ?? t(`${OU}.work.nobody_on_it`);
      const group = groups.get(key) ?? { name, items: [] };
      group.items.push(item);
      groups.set(key, group);
    }
    return [...groups.values()].toSorted((a, b) => a.name.localeCompare(b.name));
  }, [inProgress, t]);

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="48px" />
        <Loader.Item height="48px" />
        <Loader.Item height="48px" />
      </Loader>
    );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-end">
        <Button variant="secondary" size="sm" prependIcon={<RefreshCw />} onClick={() => void load()}>
          {t(`${OU}.work.refresh`)}
        </Button>
      </div>

      {/* Grouped by process instance when the items are steps of one: four
          work items that are four steps of the same onboarding read as one
          thing, and a flat list of them does not. Items that belong to no
          process stay in the ungrouped list above them. */}
      <QueueList
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        title={t(`${OU}.work.inbox_title`)}
        emptyLabel={t(`${OU}.work.inbox_empty`)}
        items={inboxLoose}
        capabilities={capabilities}
        onAssign={setAssigningItem}
        onTransfer={setTransferringItem}
      />

      {inboxByProcess.map((group) => (
        <QueueList
          key={group.key}
          workspaceSlug={workspaceSlug}
          unitId={unitId}
          title={t(`${OU}.work.process_group`, {
            name: group.name,
            done: group.done,
            total: group.total,
          })}
          emptyLabel={t(`${OU}.work.inbox_empty`)}
          items={group.items}
          capabilities={capabilities}
          onAssign={setAssigningItem}
          onTransfer={setTransferringItem}
        />
      ))}

      <section className="flex flex-col gap-4">
        <h4 className="text-sm text-custom-text-200 flex items-center gap-2 font-medium">
          {t(`${OU}.work.in_progress_title`)}
          <span className="text-xs text-custom-text-400">{inProgress.length}</span>
        </h4>
        {byExecutor.length === 0 ? (
          <p className="text-sm text-custom-text-300 border-custom-border-200 rounded border border-dashed px-4 py-6 text-center">
            {t(`${OU}.work.in_progress_empty`)}
          </p>
        ) : (
          byExecutor.map((group) => (
            <QueueList
              key={group.name}
              workspaceSlug={workspaceSlug}
              unitId={unitId}
              title={group.name}
              emptyLabel={t(`${OU}.work.in_progress_empty`)}
              items={group.items}
              capabilities={capabilities}
              onAssign={setAssigningItem}
              onTransfer={setTransferringItem}
            />
          ))
        )}
      </section>

      <QueueList
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        title={t(`${OU}.work.attention_title`)}
        emptyLabel={t(`${OU}.work.attention_empty`)}
        items={attention}
        capabilities={capabilities}
        onAssign={setAssigningItem}
        onTransfer={setTransferringItem}
      />

      {/* The log is a coordinator's read; for everybody else the endpoint
          answers 403, and the section renders its own empty state. */}
      {capabilities.can_assign && (
        <section className="flex flex-col gap-2">
          <h4 className="text-sm text-custom-text-200 font-medium">{t(`${OU}.work.decisions_title`)}</h4>
          <DecisionTimeline workspaceSlug={workspaceSlug} unitId={unitId} />
        </section>
      )}

      <AssignMemberModal
        isOpen={!!assigningItem}
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        item={assigningItem}
        onClose={() => setAssigningItem(null)}
      />
      <TransferUnitModal
        isOpen={!!transferringItem}
        workspaceSlug={workspaceSlug}
        unitId={unitId}
        item={transferringItem}
        onClose={() => setTransferringItem(null)}
      />
    </div>
  );
});
