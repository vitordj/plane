/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Link } from "react-router";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { PriorityIcon, StateGroupIcon } from "@plane/propel/icons";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IQueueRow } from "@plane/types";
import { Avatar, Tooltip } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
  row: IQueueRow;
  /**
   * Opens the "assign to…" picker. Owned by the tab rather than the row so one
   * modal serves the whole list instead of one per line.
   */
  onAssign: (row: IQueueRow) => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Turns `age_seconds` into something a person reads. Coarse on
 * purpose: a coordinator deciding what to pick up next needs "three days", not
 * three days, four hours and eleven minutes.
 * @param seconds How long the item has been where it is.
 * @returns A catalogue key and the count it interpolates.
 */
function ageLabel(seconds: number): { key: string; count: number } {
  if (seconds < 60) return { key: "just_now", count: 0 };
  if (seconds < 3600) return { key: "minutes", count: Math.floor(seconds / 60) };
  if (seconds < 86400) return { key: "hours", count: Math.floor(seconds / 3600) };
  return { key: "days", count: Math.floor(seconds / 86400) };
}

/**
 * @description One work item in an area's queue: what it is, why it is sitting
 * here, how long it has been, and the actions this viewer may take on it.
 *
 * The three actions follow the `permissions` flags the API returns. They are a
 * courtesy, not a control — the API refuses what the person may not do
 * whatever the row shows (RFC §1.2) — so the row can afford to trust them.
 */
export const QueueItemRow = observer(function QueueItemRow(props: Props) {
  const { workspaceSlug, unitId, row, onAssign } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [pendingAction, setPendingAction] = useState<"claim" | "return" | null>(null);

  const age = ageLabel(row.age_seconds);
  const workItemLink = `/${workspaceSlug}/projects/${row.project.id}/issues/${row.issue_id}`;

  const handleClaim = async () => {
    setPendingAction("claim");
    try {
      await store.claim(workspaceSlug, unitId, row);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.claimed_title`),
        message: t(`${OU}.work.toast.claimed`),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_claimed`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setPendingAction(null);
    }
  };

  const handleReturn = async () => {
    setPendingAction("return");
    try {
      await store.returnToQueue(workspaceSlug, unitId, row);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.returned_title`),
        message: t(`${OU}.work.toast.returned`),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_returned`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setPendingAction(null);
    }
  };

  return (
    <div className="flex items-start justify-between gap-3 px-4 py-3">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-2">
          <PriorityIcon priority={row.priority} className="size-3.5 shrink-0" />
          {row.state && (
            <Tooltip tooltipContent={row.state.name}>
              <span className="flex shrink-0 items-center">
                <StateGroupIcon stateGroup={row.state.group} color={row.state.color} />
              </span>
            </Tooltip>
          )}
          <Link
            to={workItemLink}
            className="text-xs text-custom-text-400 hover:text-custom-text-200 focus-visible:ring-custom-primary-100 shrink-0 rounded font-medium outline-none focus-visible:ring-2"
          >
            {row.project.identifier}-{row.sequence_id}
          </Link>
          <Link
            to={workItemLink}
            className="text-sm text-custom-text-100 hover:text-custom-primary-100 focus-visible:ring-custom-primary-100 truncate rounded outline-none focus-visible:ring-2"
          >
            {row.name}
          </Link>
        </div>

        <div className="text-xs text-custom-text-300 flex flex-wrap items-center gap-x-3 gap-y-1">
          {row.queue_reason && <span>{t(`${OU}.work.reason.${row.queue_reason}`)}</span>}
          <span>{t(`${OU}.work.age.${age.key}`, { count: age.count })}</span>
          {/* The one thing the area owes somebody: an allocation past its own
              deadline. Called out rather than left to be read off a date. */}
          {row.assignment_overdue && (
            <span className="text-custom-text-100 bg-custom-primary-100/10 rounded px-1.5 py-0.5 font-medium">
              {t(`${OU}.work.overdue`)}
            </span>
          )}
          {row.primary_executor ? (
            <span className="flex min-w-0 items-center gap-1.5">
              <Avatar name={row.primary_executor.display_name} src={row.primary_executor.avatar_url} size="sm" />
              <span className="truncate">{row.primary_executor.display_name}</span>
            </span>
          ) : (
            <span className="text-custom-text-400">{t(`${OU}.work.unassigned`)}</span>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {row.permissions.can_claim && (
          <Button
            variant="secondary"
            size="sm"
            onClick={handleClaim}
            loading={pendingAction === "claim"}
            disabled={pendingAction !== null}
          >
            {t(`${OU}.work.claim`)}
          </Button>
        )}
        {row.permissions.can_assign && (
          <Button variant="secondary" size="sm" onClick={() => onAssign(row)} disabled={pendingAction !== null}>
            {t(`${OU}.work.assign`)}
          </Button>
        )}
        {row.permissions.can_return && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleReturn}
            loading={pendingAction === "return"}
            disabled={pendingAction !== null}
          >
            {t(`${OU}.work.return_to_queue`)}
          </Button>
        )}
      </div>
    </div>
  );
});
