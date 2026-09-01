/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Network, Trash2 } from "lucide-react";
// plane imports
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

export const OrganizationalUnitList = observer(function OrganizationalUnitList(props: Props) {
  const { workspaceSlug, units, onSelect } = props;
  const store = useOrganizationalUnit();

  const [unitToDelete, setUnitToDelete] = useState<IOrganizationalUnit | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (!unitToDelete) return;
    setIsDeleting(true);
    try {
      await store.deleteUnit(workspaceSlug, unitToDelete.id);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Area deleted",
        message: `Project access ${unitToDelete.name} granted was withdrawn. Access granted elsewhere is kept.`,
      });
      setUnitToDelete(null);
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not deleted", message: "Try again in a moment." });
    } finally {
      setIsDeleting(false);
    }
  };

  if (units.length === 0)
    return (
      <div className="border-custom-border-200 flex flex-col items-center gap-2 rounded border border-dashed py-12">
        <Network className="text-custom-text-400 size-6" />
        <p className="text-sm text-custom-text-300">No areas yet.</p>
        <p className="text-xs text-custom-text-400 max-w-sm text-center">
          An area groups people who work together — Compliance, Sales, Back office — and gives them access to its
          projects in one step.
        </p>
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
              <span className="text-sm text-custom-text-100 truncate font-medium">{unit.name}</span>
              <span className="text-xs text-custom-text-300">
                {unit.member_count} {unit.member_count === 1 ? "person" : "people"} · {unit.project_count}{" "}
                {unit.project_count === 1 ? "project" : "projects"}
              </span>
            </button>
            <button
              type="button"
              aria-label={`Delete ${unit.name}`}
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
          title={`Delete ${unitToDelete.name}?`}
          content={`Everyone in this area loses the project access it granted them. Access granted by another area, or set by hand on a project, is kept.`}
          variant="danger"
          primaryButtonText={{ loading: "Deleting...", default: "Delete area" }}
        />
      )}
    </>
  );
});
