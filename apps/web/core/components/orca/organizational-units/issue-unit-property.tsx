/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { Sparkles } from "lucide-react";
// plane imports
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { CustomSearchSelect } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
  /** Refreshes the work item after assignment so the new assignee shows up. */
  onAssigned?: () => void;
};

/**
 * @description Which area owns this work item, and a one-click way to hand it
 * to that area's least-loaded member. The area is responsibility, not access —
 * the assignee is always a person, because that is what Plane assigns work to.
 */
export const IssueOrganizationalUnitProperty = observer(function IssueOrganizationalUnitProperty(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled, onAssigned } = props;
  const store = useOrganizationalUnit();

  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);
  const [isAssigning, setIsAssigning] = useState(false);

  useEffect(() => {
    store.fetchUnits(workspaceSlug);
  }, [workspaceSlug, store]);

  useEffect(() => {
    let cancelled = false;
    const loadResponsibleUnit = async () => {
      try {
        const response = await store.service.getIssueOrganizationalUnit(workspaceSlug, projectId, issueId);
        if (!cancelled) setSelectedUnitId(response.organizational_unit?.id ?? null);
      } catch {
        if (!cancelled) setSelectedUnitId(null);
      }
    };
    void loadResponsibleUnit();
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, projectId, issueId, store]);

  // Only areas that actually cover this project can own work in it.
  const options = useMemo(
    () =>
      store.units
        .filter((unit) => unit.is_active)
        .map((unit) => ({
          value: unit.id,
          query: unit.name,
          content: <span className="truncate">{unit.name}</span>,
        })),
    [store.units]
  );

  const handleChange = async (unitId: string) => {
    const previous = selectedUnitId;
    setSelectedUnitId(unitId);
    try {
      await store.service.setIssueOrganizationalUnit(workspaceSlug, projectId, issueId, unitId);
    } catch {
      setSelectedUnitId(previous);
      setToast({ type: TOAST_TYPE.ERROR, title: "Area unchanged", message: "Try again in a moment." });
    }
  };

  const handleAutoAssign = async () => {
    if (!selectedUnitId) return;
    setIsAssigning(true);
    try {
      const result = await store.assignIssueFromUnit(workspaceSlug, projectId, issueId, { unitId: selectedUnitId });
      if (result.assigned) {
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: "Assigned",
          message: "Given to the area member with the least open work.",
        });
        onAssigned?.();
      } else if (result.reason === "already_assigned") {
        setToast({
          type: TOAST_TYPE.INFO,
          title: "Already assigned",
          message: "This work item already has an assignee.",
        });
      } else {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: "Nobody available",
          message: "No member of this area has access to this project.",
        });
      }
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not assigned", message: "Try again in a moment." });
    } finally {
      setIsAssigning(false);
    }
  };

  if (options.length === 0) return null;

  const selectedUnit = selectedUnitId ? store.getUnitById(selectedUnitId) : undefined;

  return (
    <div className="flex w-full items-center gap-1">
      <CustomSearchSelect
        value={selectedUnitId}
        options={options}
        onChange={handleChange}
        disabled={disabled}
        label={selectedUnit?.name ?? "None"}
        maxHeight="md"
        className="group w-full grow"
        buttonClassName={`text-body-xs-regular justify-between ${selectedUnit ? "" : "text-placeholder"}`}
        noResultsMessage="No areas match"
      />
      {selectedUnitId && !disabled && (
        <Button
          variant="ghost"
          size="sm"
          onClick={handleAutoAssign}
          loading={isAssigning}
          prependIcon={<Sparkles />}
          title="Assign to the area member with the least open work"
        >
          Assign
        </Button>
      )}
    </div>
  );
});
