/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { ChevronDown } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import type { IIssueRouting } from "@plane/types";
import { Menu } from "@plane/propel/menu";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { CustomSearchSelect } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { useUser } from "@/hooks/store/user/user-user";

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
 * @description Which area owns this work item, where the item stands inside
 * that area, and the four things a person can do about it from here: let the
 * area allocate it, take it, hand it back, put it on hold.
 *
 * The area is responsibility, not access — the assignee is always a person,
 * because that is what Plane assigns work to. What this panel adds over the
 * area's own board is proximity: somebody reading the item should not have to
 * find the area's queue to say "I'll take this".
 */
export const IssueOrganizationalUnitProperty = observer(function IssueOrganizationalUnitProperty(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled, onAssigned } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const { data: currentUser } = useUser();

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
        const { unit, routing: state } = await store.fetchIssueUnit(workspaceSlug, projectId, issueId);
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

  // Only areas that actually cover this project can own work in it: an area
  // that does not link this project grants nobody access to it, so the API
  // refuses it (defect D1) and offering it here would produce an error the
  // person cannot act on.
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
    const previousRouting = routing;
    setSelectedUnitId(unitId);
    try {
      const { routing: state } = await store.setIssueUnit(workspaceSlug, projectId, issueId, unitId);
      setRouting(state);
    } catch {
      setSelectedUnitId(previous);
      setRouting(previousRouting);
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${KEY}.toast.area_unchanged`), message: t(TRY_AGAIN) });
    }
  };

  const handleAutoAssign = async () => {
    if (!selectedUnitId) return;
    setIsAssigning(true);
    try {
      const result = await store.assignIssueFromUnit(workspaceSlug, projectId, issueId, { unitId: selectedUnitId });
      setRouting(result.routing);
      if (result.assigned) {
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${KEY}.toast.assigned_title`),
          message: t(`${KEY}.toast.assigned`),
        });
        onAssigned?.();
      } else if (result.reason === "queued") {
        // The area allocates by hand or waits for someone to claim it, so
        // nobody was picked and nothing went wrong.
        setToast({
          type: TOAST_TYPE.INFO,
          title: t(`${KEY}.toast.queued_title`),
          message: t(`${KEY}.toast.queued`),
        });
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

  /**
   * @description Run one of the queue actions and show what it did. Kept in
   * one place so the four buttons cannot drift in how they report a refusal:
   * the API answers with a coded error, which the catalogue turns into a
   * sentence in the reader's language.
   */
  const runAction = async (action: () => Promise<IIssueRouting>, failureKey: string) => {
    setIsAssigning(true);
    try {
      const state = await action();
      setRouting(state);
      onAssigned?.();
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${KEY}.toast.${failureKey}`),
        message: t(resolveOrcaErrorKey(error) ?? TRY_AGAIN),
      });
    } finally {
      setIsAssigning(false);
    }
  };

  if (options.length === 0) return null;

  const selectedUnit = selectedUnitId ? store.getUnitById(selectedUnitId) : undefined;
  const state = routing?.routing_state;
  const isWaiting = state === "queued" || state === "allocation_failed";
  const isMine = !!routing?.primary_executor && routing.primary_executor === currentUser?.id;

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
        {/* One menu rather than four buttons: which of the actions makes sense
            depends on where the item stands, and a row of buttons that mostly
            refuse is worse than a menu that only offers what applies. */}
        {selectedUnitId && !disabled && (
          <Menu
            customButton={
              <div className="hover:bg-custom-background-80 flex shrink-0 items-center gap-1 rounded-md px-2 py-1">
                <span className="text-body-xs-regular">{t(`${KEY}.actions`)}</span>
                <ChevronDown className="size-3" />
              </div>
            }
            optionsClassName="min-w-[200px]"
            disabled={isAssigning}
          >
            {isWaiting && <Menu.MenuItem onClick={handleAutoAssign}>{t(`${KEY}.assign`)}</Menu.MenuItem>}
            {isWaiting && (
              <Menu.MenuItem
                onClick={() =>
                  void runAction(() => store.claim(workspaceSlug, projectId, issueId, selectedUnitId), "not_claimed")
                }
              >
                {t(`${KEY}.claim`)}
              </Menu.MenuItem>
            )}
            {state === "assigned" && (
              <Menu.MenuItem
                onClick={() =>
                  void runAction(
                    () => store.returnToQueue(workspaceSlug, projectId, issueId, { unitId: selectedUnitId }),
                    "not_returned"
                  )
                }
              >
                {isMine ? t(`${KEY}.return_mine`) : t(`${KEY}.return`)}
              </Menu.MenuItem>
            )}
            {state !== "suspended" && (
              <Menu.MenuItem
                onClick={() =>
                  void runAction(
                    () => store.suspendIssue(workspaceSlug, projectId, issueId, { unitId: selectedUnitId }),
                    "not_suspended"
                  )
                }
              >
                {t(`${KEY}.suspend`)}
              </Menu.MenuItem>
            )}
          </Menu>
        )}
      </div>

      {/* Where the item stands under the area. Without it the panel says the
          area owns the item and stops, which is exactly the state that makes
          somebody ask "so why is nobody on it?". */}
      {selectedUnitId && state && (
        <p className="text-custom-text-300 truncate text-body-xs-regular">
          {t(`workspace_settings.settings.organizational_units.work.state.${state}`)}
          {routing?.queue_reason
            ? ` · ${t(`workspace_settings.settings.organizational_units.work.reason.${routing.queue_reason}`)}`
            : ""}
          {/* Who is on it is Plane's own assignee field, right above this one;
              repeating the name here would be two sources for one fact. */}
          {isMine ? ` · ${t(`${KEY}.yours`)}` : ""}
        </p>
      )}
    </div>
  );
});
