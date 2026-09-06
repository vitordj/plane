/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IQueueItem } from "@plane/types";
import { CustomSearchSelect, EModalPosition, EModalWidth, Input, ModalCore } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  /** The area the item belongs to today, whose queue is refreshed afterwards. */
  unitId: string;
  item: IQueueItem | null;
  onClose: () => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Hand an item to another area. Only areas that cover the item's
 * project are offered: an area that does not link the project grants nobody
 * access to it, so the API refuses the transfer (I2) and listing it here would
 * produce an error the coordinator cannot act on.
 */
export const TransferUnitModal = observer(function TransferUnitModal(props: Props) {
  const { isOpen, workspaceSlug, unitId, item, onClose } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [targetUnitId, setTargetUnitId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const options = useMemo(
    () =>
      store.units
        .filter(
          (unit) => unit.is_active && unit.id !== unitId && (unit.project_ids ?? []).includes(item?.project ?? "")
        )
        .map((unit) => ({ value: unit.id, query: unit.name, content: <span className="truncate">{unit.name}</span> })),
    [store.units, unitId, item]
  );

  const handleTransfer = async () => {
    if (!item || !targetUnitId) return;
    setIsSubmitting(true);
    try {
      await store.transferIssue(workspaceSlug, item.project, item.issue_id, targetUnitId, {
        unitId,
        reason: reason.trim(),
      });
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.work.toast.transferred`) });
      setTargetUnitId(null);
      setReason("");
      onClose();
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_transferred`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const selected = targetUnitId ? store.getUnitById(targetUnitId) : undefined;

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <div>
          <h3 className="text-lg text-custom-text-100 font-medium">{t(`${OU}.work.transfer_modal.title`)}</h3>
          {item && (
            <p className="text-sm text-custom-text-300 truncate">
              {item.project_identifier}-{item.sequence_id} · {item.name}
            </p>
          )}
        </div>

        {options.length === 0 ? (
          <p className="text-sm text-custom-text-300">{t(`${OU}.work.transfer_modal.no_target`)}</p>
        ) : (
          <>
            <CustomSearchSelect
              value={targetUnitId}
              options={options}
              onChange={(value: string) => setTargetUnitId(value)}
              label={selected?.name ?? t(`${OU}.work.transfer_modal.select_area`)}
              maxHeight="md"
              noResultsMessage={t(`${OU}.work.transfer_modal.no_target`)}
            />
            <div className="flex flex-col gap-1">
              <label htmlFor="orca-transfer-reason" className="text-sm text-custom-text-200">
                {t(`${OU}.work.reason_label`)}
              </label>
              <Input
                id="orca-transfer-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={t(`${OU}.work.reason_placeholder`)}
              />
            </div>
          </>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" size="sm" onClick={handleTransfer} loading={isSubmitting} disabled={!targetUnitId}>
            {t(`${OU}.work.transfer`)}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
