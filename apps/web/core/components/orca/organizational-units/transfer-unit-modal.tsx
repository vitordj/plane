/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IUnitQueueRow } from "@plane/types";
import { Input, ModalCore, EModalWidth } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
  unitId: string;
  row: IUnitQueueRow | null;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Hand a work item to a different area. Only areas that cover
 * the item's project are offered: an area that is not linked to the project
 * has no members with access to it, so the transfer would be refused by the
 * server anyway — better to not offer it than to explain the refusal.
 *
 * The reason is optional but asked for, because a transfer is the one queue
 * action whose "why" nobody can reconstruct from the decision alone.
 */
export const TransferUnitModal = observer(function TransferUnitModal(props: Props) {
  const { isOpen, onClose, workspaceSlug, unitId, row } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isSaving, setIsSaving] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (isOpen) setReason("");
  }, [isOpen]);

  // Refetched on every open rather than cached: the list is small, and a
  // stale `project_ids` here would offer an area that no longer covers the
  // project — exactly the mistake the filter exists to prevent.
  useEffect(() => {
    if (isOpen) store.fetchUnits(workspaceSlug).catch(() => undefined);
  }, [isOpen, workspaceSlug, store]);

  const destinations = useMemo(() => {
    if (!row) return [];
    return (store.units ?? [])
      .filter((unit) => unit.id !== unitId && unit.is_active && unit.project_ids.includes(row.project_id))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [store.units, row, unitId]);

  const handlePick = async (destinationId: string) => {
    if (!row) return;
    setIsSaving(true);
    try {
      await store.transferUnit(workspaceSlug, unitId, row.project_id, row.issue_id, destinationId, reason);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.queue.toast.transferred`) });
      onClose();
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.queue.toast.not_transferred`) });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} width={EModalWidth.LG}>
      <div className="p-5">
        <h3 className="text-lg text-custom-text-100 font-medium">{t(`${OU}.queue.transfer_modal.title`)}</h3>
        {row && <p className="text-custom-text-300 text-body-xs-regular mt-1 truncate">{row.name}</p>}
        <p className="text-custom-text-400 text-body-2xs-regular mt-2">{t(`${OU}.queue.transfer_modal.help`)}</p>

        <Input
          type="text"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder={t(`${OU}.queue.transfer_modal.reason_placeholder`)}
          className="mt-3 w-full"
        />

        <div className="mt-3 space-y-1">
          {destinations.length === 0 && (
            <p className="text-custom-text-400 text-body-xs-regular py-4 text-center">
              {t(`${OU}.queue.transfer_modal.nobody`)}
            </p>
          )}
          {destinations.map((unit) => (
            <button
              key={unit.id}
              type="button"
              disabled={isSaving}
              onClick={() => handlePick(unit.id)}
              className="hover:bg-custom-background-80 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left"
            >
              <span className="text-body-xs-regular text-custom-text-100 flex-1 truncate">{unit.name}</span>
              {unit.description && (
                <span className="text-custom-text-400 text-body-2xs-regular max-w-[50%] truncate">
                  {unit.description}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="mt-5 flex justify-end">
          <Button variant="neutral-primary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
