/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Network, Trash2 } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IOrganizationalUnit } from "@plane/types";
import { AlertModalCore } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  units: IOrganizationalUnit[];
  onSelect: (unit: IOrganizationalUnit) => void;
};

const OU = "workspace_settings.settings.organizational_units";

export const OrganizationalUnitList = observer(function OrganizationalUnitList(props: Props) {
  const { workspaceSlug, units, onSelect } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [unitToDelete, setUnitToDelete] = useState<IOrganizationalUnit | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (!unitToDelete) return;
    setIsDeleting(true);
    try {
      await store.deleteUnit(workspaceSlug, unitToDelete.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.list.toast.deleted_title`),
        message: t(`${OU}.list.toast.deleted_message`, { name: unitToDelete.name }),
      });
      setUnitToDelete(null);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.list.toast.not_deleted`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  if (units.length === 0)
    return (
      <div className="border-custom-border-200 flex flex-col items-center gap-2 rounded border border-dashed py-12">
        <Network className="text-custom-text-400 size-6" />
        <p className="text-sm text-custom-text-300">{t(`${OU}.list.empty_title`)}</p>
        <p className="text-xs text-custom-text-400 max-w-sm text-center">{t(`${OU}.list.empty_description`)}</p>
      </div>
    );

  return (
    <>
      <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
        {units.map((unit) => (
          <div key={unit.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <button
              type="button"
              className="focus-visible:ring-custom-primary-100 flex min-w-0 flex-1 flex-col items-start rounded text-left outline-none focus-visible:ring-2"
              onClick={() => onSelect(unit)}
            >
              <span className="flex min-w-0 items-center gap-2">
                <span className="text-sm text-custom-text-100 truncate font-medium">{unit.name}</span>
                {/* Bound areas are worth flagging: their membership is owned
                    upstream, so editing it here is undone on the next sync. */}
                {unit.external_id && (
                  <span className="text-custom-text-400 shrink-0 rounded bg-layer-1 px-1.5 py-0.5 text-[10px] tracking-wide uppercase">
                    {t(`${OU}.list.synced_badge`)}
                  </span>
                )}
              </span>
              <span className="text-xs text-custom-text-300">
                {t(`${OU}.list.counts`, { members: unit.member_count, projects: unit.project_count })}
              </span>
            </button>
            <button
              type="button"
              aria-label={t(`${OU}.list.delete_aria`, { name: unit.name })}
              className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 flex-shrink-0 rounded p-1 outline-none focus-visible:ring-2"
              onClick={() => setUnitToDelete(unit)}
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        ))}
      </div>

      {unitToDelete && (
        <AlertModalCore
          isOpen
          handleClose={() => setUnitToDelete(null)}
          handleSubmit={handleDelete}
          isSubmitting={isDeleting}
          title={t(`${OU}.list.delete_title`, { name: unitToDelete.name })}
          content={unitToDelete.external_id ? t(`${OU}.list.delete_content_synced`) : t(`${OU}.list.delete_content`)}
          variant="danger"
          primaryButtonText={{ loading: t(`${OU}.list.delete_loading`), default: t(`${OU}.list.delete_confirm`) }}
        />
      )}
    </>
  );
});
