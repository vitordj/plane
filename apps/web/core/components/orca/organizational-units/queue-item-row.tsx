/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { AlertTriangle, ArrowRightLeft, Clock, UserPlus } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { IUnitQueueRow } from "@plane/types";
import { Avatar, Tooltip } from "@plane/ui";

type Props = {
  workspaceSlug: string;
  row: IUnitQueueRow;
  onClaim: (row: IUnitQueueRow) => void;
  onAssign: (row: IUnitQueueRow) => void;
  onReturn: (row: IUnitQueueRow) => void;
  onTransfer: (row: IUnitQueueRow) => void;
  busy?: boolean;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description One work item in an area's queue. Shows why it is waiting and
 * for how long, because "queued" alone tells a coordinator nothing about
 * whether to worry: waiting ten minutes for a claim is normal, waiting two
 * days past its assignment date is the thing they came here to find.
 */
export const QueueItemRow = observer(function QueueItemRow(props: Props) {
  const { workspaceSlug, row, onClaim, onAssign, onReturn, onTransfer, busy } = props;
  const { t } = useTranslation();

  const age = row.age_seconds ?? 0;
  const ageLabel =
    age >= 86400
      ? t(`${OU}.queue.age_days`, { count: Math.floor(age / 86400) })
      : age >= 3600
        ? t(`${OU}.queue.age_hours`, { count: Math.floor(age / 3600) })
        : t(`${OU}.queue.age_minutes`, { count: Math.max(1, Math.floor(age / 60)) });

  return (
    <div className="border-custom-border-200 flex items-center gap-3 border-b px-3 py-2 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <a
            href={`/${workspaceSlug}/projects/${row.project_id}/issues/${row.issue_id}`}
            className="text-custom-text-300 hover:text-custom-text-100 text-body-2xs-medium shrink-0"
          >
            {row.identifier}
          </a>
          <span className="text-body-xs-regular text-custom-text-100 truncate">{row.name}</span>
        </div>
        <div className="text-custom-text-400 text-body-2xs-regular mt-0.5 flex items-center gap-2">
          <span>{t(`${OU}.queue.reason.${row.queue_reason || "new_item"}`)}</span>
          <span className="flex items-center gap-1">
            <Clock className="size-3" />
            {ageLabel}
          </span>
          {row.assignment_overdue && (
            <Tooltip tooltipContent={t(`${OU}.queue.overdue_tooltip`)}>
              <span className="text-danger-primary flex items-center gap-1">
                <AlertTriangle className="size-3" />
                {t(`${OU}.queue.overdue`)}
              </span>
            </Tooltip>
          )}
        </div>
      </div>

      {row.primary_executor && (
        <Avatar name={row.primary_executor.display_name} src={row.primary_executor.avatar_url} size="sm" />
      )}

      <div className="flex shrink-0 items-center gap-1">
        {row.can_claim && (
          <Button variant="secondary" size="sm" loading={busy} onClick={() => onClaim(row)}>
            {t(`${OU}.queue.claim`)}
          </Button>
        )}
        {row.can_assign && (
          <Button
            variant="secondary"
            size="sm"
            loading={busy}
            prependIcon={<UserPlus />}
            onClick={() => onAssign(row)}
          >
            {t(`${OU}.queue.assign_to`)}
          </Button>
        )}
        {row.can_transfer && (
          <Tooltip tooltipContent={t(`${OU}.queue.transfer`)}>
            <Button
              variant="secondary"
              size="sm"
              loading={busy}
              prependIcon={<ArrowRightLeft />}
              onClick={() => onTransfer(row)}
              aria-label={t(`${OU}.queue.transfer`)}
            />
          </Tooltip>
        )}
        {row.can_return && (
          <Button variant="link-neutral" size="sm" loading={busy} onClick={() => onReturn(row)}>
            {t(`${OU}.queue.return`)}
          </Button>
        )}
      </div>
    </div>
  );
});
