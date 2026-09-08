/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { Search } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { EModalPosition, EModalWidth, Input, ModalCore } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  unitId: string;
  projectId: string;
  subtitle?: string;
  onTransferred: (destinationUnitId: string) => Promise<unknown>;
  onClose: () => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Moves a work item to another area that covers the same project.
 * Only covering areas are offered: an area that does not link the project
 * cannot own work in it (defect D1), and offering it would produce an error
 * the person cannot act on.
 */
export const TransferUnitModal = observer(function TransferUnitModal(props: Props) {
  const { isOpen, workspaceSlug, unitId, projectId, subtitle, onTransferred, onClose } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [search, setSearch] = useState("");
  const [transferringId, setTransferringId] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setSearch("");
    store.fetchUnits(workspaceSlug).catch(() => undefined);
  }, [isOpen, workspaceSlug, store]);

  const destinations = useMemo(() => {
    const query = search.trim().toLowerCase();
    return store.units.filter((unit) => {
      if (unit.id === unitId || !unit.is_active) return false;
      if (!(unit.project_ids ?? []).includes(projectId)) return false;
      if (!query) return true;
      return unit.name.toLowerCase().includes(query);
    });
  }, [store.units, unitId, projectId, search]);

  const handleTransfer = async (destinationId: string) => {
    setTransferringId(destinationId);
    try {
      await onTransferred(destinationId);
      const destination = store.getUnitById(destinationId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.transferred_title`),
        message: t(`${OU}.work.toast.transferred`, { name: destination?.name ?? "" }),
      });
      onClose();
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_transferred`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setTransferringId(null);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.LG}>
      <div className="flex flex-col gap-4 p-5">
        <div>
          <h3 className="text-lg text-custom-text-100 font-medium">{t(`${OU}.work.transfer_modal.title`)}</h3>
          {subtitle && <p className="text-sm text-custom-text-300 mt-1">{subtitle}</p>}
        </div>
        <div className="relative">
          <Search className="text-custom-text-400 pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t(`${OU}.work.transfer_modal.search`)}
            className="pl-9"
          />
        </div>
        {destinations.length === 0 ? (
          <p className="text-sm text-custom-text-300 py-6 text-center">{t(`${OU}.work.transfer_modal.no_units`)}</p>
        ) : (
          <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto">
            {destinations.map((unit) => (
              <li key={unit.id}>
                <Button
                  variant="secondary"
                  className="w-full justify-start"
                  onClick={() => handleTransfer(unit.id)}
                  loading={transferringId === unit.id}
                  disabled={transferringId !== null}
                >
                  {unit.name}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </ModalCore>
  );
});
