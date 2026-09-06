/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Link } from "react-router";
import { AlarmClock, CircleUser, MoreHorizontal } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Menu } from "@plane/propel/menu";
import { Tooltip } from "@plane/propel/tooltip";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IQueueCapabilities, IQueueItem } from "@plane/types";
import { Avatar } from "@plane/ui";
import { calculateTimeAgoShort, generateWorkItemLink } from "@plane/utils";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { useUser } from "@/hooks/store/user/user-user";
// components
import { QueueItemSuggestion } from "./queue-item-suggestion";

type Props = {
  workspaceSlug: string;
  unitId: string;
  item: IQueueItem;
  capabilities: IQueueCapabilities;
  /** Opens the "assign to…" modal for this item. */
  onAssign: (item: IQueueItem) => void;
  /** Opens the "move to another area" modal for this item. */
  onTransfer: (item: IQueueItem) => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description One item of an area's queue: what it is, how long it has been
 * waiting, who is on it, and the actions the reader is allowed to take.
 *
 * The actions come from the server's `capabilities` rather than from a role
 * check written here, so the rules live in one place — and the one exception
 * is returning your own item, which the row can decide because it knows who
 * the executor is and who is reading.
 */
export const QueueItemRow = observer(function QueueItemRow(props: Props) {
  const { workspaceSlug, unitId, item, capabilities, onAssign, onTransfer } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const { data: currentUser } = useUser();

  const [isBusy, setIsBusy] = useState(false);

  const isMine = !!item.primary_executor && item.primary_executor === currentUser?.id;
  const isWaiting = item.routing_state === "queued" || item.routing_state === "allocation_failed";
  const canClaim = capabilities.can_claim && isWaiting;
  const canReturn = (capabilities.can_return || isMine) && item.routing_state !== "queued";
  const canSuspend = capabilities.can_assign && item.routing_state !== "suspended";

  const run = async (action: () => Promise<unknown>, successKey: string, failureKey: string) => {
    setIsBusy(true);
    try {
      await action();
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.work.toast.${successKey}`) });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.${failureKey}`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsBusy(false);
    }
  };

  const workItemLink = generateWorkItemLink({
    workspaceSlug,
    projectId: item.project,
    issueId: item.issue_id,
    projectIdentifier: item.project_identifier,
    sequenceId: item.sequence_id,
  });

  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-xs text-custom-text-400 shrink-0 font-medium">
            {item.project_identifier}-{item.sequence_id}
          </span>
          <Link to={workItemLink} className="text-sm text-custom-text-100 truncate hover:underline">
            {item.name}
          </Link>
        </div>
        <div className="text-xs text-custom-text-300 flex flex-wrap items-center gap-2">
          <span>{t(`${OU}.work.state.${item.routing_state}`)}</span>
          {item.queue_reason && <span>· {t(`${OU}.work.reason.${item.queue_reason}`)}</span>}
          {item.process && (
            <span>
              ·{" "}
              {t(`${OU}.work.process_step`, {
                step: item.process.step_key,
                done: item.process.done,
                total: item.process.total,
              })}
            </span>
          )}
          {item.age_seconds !== null && item.queued_at && (
            <span>· {t(`${OU}.work.waiting_for`, { duration: calculateTimeAgoShort(item.queued_at) })}</span>
          )}
          {/* Only for an item that came back because its executor became
              unavailable: there the machine knows the shape of the answer and
              the coordinator is doing recovery work they did not plan. */}
          {item.queue_reason === "executor_unavailable" && (
            <QueueItemSuggestion
              workspaceSlug={workspaceSlug}
              unitId={unitId}
              item={item}
              canAssign={capabilities.can_assign}
            />
          )}
          {item.assignment_overdue && (
            <Tooltip tooltipContent={t(`${OU}.work.overdue_tooltip`)}>
              <span className="text-custom-text-error flex items-center gap-1">
                <AlarmClock className="size-3" />
                {t(`${OU}.work.overdue`)}
              </span>
            </Tooltip>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {item.primary_executor_detail ? (
          <Tooltip tooltipContent={item.primary_executor_detail.display_name}>
            <span>
              <Avatar
                name={item.primary_executor_detail.display_name}
                src={item.primary_executor_detail.avatar_url}
                size="md"
              />
            </span>
          </Tooltip>
        ) : (
          <Tooltip tooltipContent={t(`${OU}.work.nobody_on_it`)}>
            <CircleUser className="text-custom-text-400 size-5" />
          </Tooltip>
        )}

        <Menu
          ellipsis
          disabled={isBusy}
          customButton={
            <div className="hover:bg-custom-background-80 flex items-center justify-center rounded-md p-1">
              <MoreHorizontal className="text-custom-text-300 size-4" />
            </div>
          }
          optionsClassName="min-w-[180px]"
        >
          {canClaim && (
            <Menu.MenuItem
              onClick={() =>
                run(() => store.claim(workspaceSlug, item.project, item.issue_id, unitId), "claimed", "not_claimed")
              }
            >
              {t(`${OU}.work.claim`)}
            </Menu.MenuItem>
          )}
          {capabilities.can_assign && (
            <Menu.MenuItem onClick={() => onAssign(item)}>{t(`${OU}.work.assign`)}</Menu.MenuItem>
          )}
          {canReturn && (
            <Menu.MenuItem
              onClick={() =>
                run(
                  () => store.returnToQueue(workspaceSlug, item.project, item.issue_id, { unitId }),
                  "returned",
                  "not_returned"
                )
              }
            >
              {t(`${OU}.work.return`)}
            </Menu.MenuItem>
          )}
          {canSuspend && (
            <Menu.MenuItem
              onClick={() =>
                run(
                  () => store.suspendIssue(workspaceSlug, item.project, item.issue_id, { unitId }),
                  "suspended",
                  "not_suspended"
                )
              }
            >
              {t(`${OU}.work.suspend`)}
            </Menu.MenuItem>
          )}
          {capabilities.can_assign && (
            <Menu.MenuItem onClick={() => onTransfer(item)}>{t(`${OU}.work.transfer`)}</Menu.MenuItem>
          )}
        </Menu>
      </div>
    </div>
  );
});
