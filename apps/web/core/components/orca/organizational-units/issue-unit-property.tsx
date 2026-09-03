/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { Sparkles } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { CustomSearchSelect } from "@plane/ui";
// hooks
import type { IIssueRouting } from "@plane/types";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
  /** Refreshes the work item after assignment so the new assignee shows up. */
  onAssigned?: () => void;
};

const KEY = "issue.organizational_unit";
const TRY_AGAIN = "workspace_settings.settings.organizational_units.try_again";

/**
 * @description Which area owns this work item, and a one-click way to hand it
 * to that area's least-loaded member. The area is responsibility, not access —
 * the assignee is always a person, because that is what Plane assigns work to.
 */
export const IssueOrganizationalUnitProperty = observer(function IssueOrganizationalUnitProperty(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled, onAssigned } = props;
  const store = useOrganizationalUnit();
  const {
    workspace: { getWorkspaceMemberDetails },
  } = useMember();
  const { t } = useTranslation();

  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);
  const [routing, setRouting] = useState<IIssueRouting | null>(null);
  const [isAssigning, setIsAssigning] = useState(false);

  useEffect(() => {
    store.fetchUnits(workspaceSlug);
  }, [workspaceSlug, store]);

  useEffect(() => {
    let cancelled = false;
    const loadResponsibleUnit = async () => {
      try {
        const unit = await store.fetchIssueUnit(workspaceSlug, projectId, issueId);
        const state = await store.fetchIssueRouting(workspaceSlug, projectId, issueId);
        if (!cancelled) {
          setSelectedUnitId(unit?.id ?? null);
          setRouting(state);
        }
      } catch {
        if (!cancelled) {
          setSelectedUnitId(null);
          setRouting(null);
        }
      }
    };
    void loadResponsibleUnit();
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, projectId, issueId, store]);

  // Only areas that actually cover this project can own work in it: the API
  // refuses the rest, so offering them would only produce a failed save.
  const options = useMemo(
    () =>
      store.units
        .filter((unit) => unit.is_active && (unit.project_ids ?? []).includes(projectId))
        .map((unit) => ({
          value: unit.id,
          query: unit.name,
          content: <span className="truncate">{unit.name}</span>,
        })),
    [store.units, projectId]
  );

  const handleChange = async (unitId: string) => {
    const previous = selectedUnitId;
    setSelectedUnitId(unitId);
    try {
      await store.setIssueUnit(workspaceSlug, projectId, issueId, unitId);
    } catch {
      setSelectedUnitId(previous);
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${KEY}.toast.area_unchanged`), message: t(TRY_AGAIN) });
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
          title: t(`${KEY}.toast.assigned_title`),
          message: t(`${KEY}.toast.assigned`),
        });
        onAssigned?.();
        setRouting(await store.fetchIssueRouting(workspaceSlug, projectId, issueId));
      } else if (result.reason === "already_assigned") {
        setToast({
          type: TOAST_TYPE.INFO,
          title: t(`${KEY}.toast.already_assigned_title`),
          message: t(`${KEY}.toast.already_assigned`),
        });
      } else {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: t(`${KEY}.toast.nobody_title`),
          message: t(`${KEY}.toast.nobody`),
        });
      }
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${KEY}.toast.not_assigned`), message: t(TRY_AGAIN) });
    } finally {
      setIsAssigning(false);
    }
  };

  if (options.length === 0) return null;

  const selectedUnit = selectedUnitId ? store.getUnitById(selectedUnitId) : undefined;

  // Where the work stands in the area's queue. "This area owns it" and
  // "somebody is doing it" are different facts, and only the second one tells
  // you whether to expect movement.
  const executorName = routing?.primary_executor
    ? getWorkspaceMemberDetails(routing.primary_executor)?.member.display_name
    : undefined;
  const routingLabel = !routing
    ? null
    : routing.routing_state !== "assigned"
      ? t(`${KEY}.routing.${routing.routing_state}`)
      : executorName
        ? t(`${KEY}.routing.assigned`, { name: executorName })
        : t(`${KEY}.routing.assigned_unknown`);

  return (
    <div className="flex w-full min-w-0 flex-col gap-1">
    <div className="flex w-full min-w-0 items-center gap-1">
      <CustomSearchSelect
        value={selectedUnitId}
        options={options}
        onChange={handleChange}
        disabled={disabled}
        label={selectedUnit?.name ?? t("common.none")}
        maxHeight="md"
        className="group min-w-0 flex-1"
        buttonClassName={`text-body-xs-regular justify-between ${selectedUnit ? "" : "text-placeholder"}`}
        noResultsMessage={t(`${KEY}.no_match`)}
      />
      {selectedUnitId && !disabled && (
        <Button
          className="shrink-0"
          variant="ghost"
          size="sm"
          onClick={handleAutoAssign}
          loading={isAssigning}
          prependIcon={<Sparkles />}
          title={t(`${KEY}.assign_tooltip`)}
        >
          {t(`${KEY}.assign`)}
        </Button>
      )}
    </div>
    {routingLabel && <span className="text-body-2xs-regular text-custom-text-300">{routingLabel}</span>}
    </div>
  );
});
