/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { ChevronDown, Sparkles } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel, resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import type { IIssueRouting } from "@plane/types";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { CustomMenu, CustomSearchSelect } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { useUser, useUserPermissions } from "@/hooks/store/user";
// components
import { AssignMemberModal } from "./assign-member-modal";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  disabled?: boolean;
  /** Refreshes the work item after assignment so the new assignee shows up. */
  onAssigned?: () => void;
};

const KEY = "issue.organizational_unit";
const OU = "workspace_settings.settings.organizational_units";
const TRY_AGAIN = "workspace_settings.settings.organizational_units.try_again";

/**
 * @description Which area owns this work item, and — once one does — what is
 * happening to it: waiting, assigned, or stuck, and the actions this viewer
 * may take.
 *
 * The three actions are not driven by a `permissions` object the way a queue
 * row is: this property has no such flags to read (`GET
 * organizational-unit/` returns the item's own routing, not a queue page),
 * so eligibility is read from what this screen already has — the routing's
 * executor and this viewer's workspace role — rather than by adding a call
 * to the queue endpoint just to ask. Whatever this menu offers, the API
 * decides for real: an ineligible click still ends in a toast, not a write.
 */
export const IssueOrganizationalUnitProperty = observer(function IssueOrganizationalUnitProperty(props: Props) {
  const { workspaceSlug, projectId, issueId, disabled, onAssigned } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const { data: currentUser } = useUser();
  const { allowPermissions } = useUserPermissions();

  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);
  const [routing, setRouting] = useState<IIssueRouting | null>(null);
  const [isAssigning, setIsAssigning] = useState(false);
  const [pendingAction, setPendingAction] = useState<"claim" | "return" | null>(null);
  const [isPickingPerson, setIsPickingPerson] = useState(false);

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

  // Fetched once an area is on the item, alongside its routing above — not a
  // call to the queue, and not one per render: the "am I a coordinator of
  // this area" question the menu needs has no other source.
  useEffect(() => {
    if (!selectedUnitId) return;
    store.fetchCoordinators(workspaceSlug, selectedUnitId).catch(() => undefined);
  }, [workspaceSlug, selectedUnitId, store]);

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

  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);
  const isExecutor = Boolean(currentUser?.id && routing?.primary_executor === currentUser.id);
  const isCoordinator = Boolean(
    selectedUnitId &&
    currentUser?.id &&
    store
      .getCoordinatorsByUnitId(selectedUnitId)
      .some((coordinator) => coordinator.is_active && coordinator.member.id === currentUser.id)
  );

  const canClaim = routing != null && routing.routing_state !== "assigned";
  const canChoosePerson = isWorkspaceAdmin || isCoordinator;
  const canReturn = routing?.routing_state === "assigned" && (isExecutor || isWorkspaceAdmin || isCoordinator);

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

  const handleClaim = async () => {
    setPendingAction("claim");
    try {
      const nextRouting = await store.claimIssueRouting(workspaceSlug, projectId, issueId);
      setRouting(nextRouting);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.claimed_title`),
        message: t(`${OU}.work.toast.claimed`),
      });
      onAssigned?.();
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_claimed`),
        message: t(resolveOrcaErrorKey(error) ?? TRY_AGAIN),
      });
    } finally {
      setPendingAction(null);
    }
  };

  const handleReturn = async () => {
    setPendingAction("return");
    try {
      const nextRouting = await store.returnIssueRouting(workspaceSlug, projectId, issueId, {
        expectedDecisionId: routing?.current_assignment_decision?.id ?? null,
      });
      setRouting(nextRouting);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.returned_title`),
        message: t(`${OU}.work.toast.returned`),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_returned`),
        message: t(resolveOrcaErrorKey(error) ?? TRY_AGAIN),
      });
    } finally {
      setPendingAction(null);
    }
  };

  if (options.length === 0) return null;

  const selectedUnit = selectedUnitId ? store.getUnitById(selectedUnitId) : undefined;
  const hasMenuActions = !disabled && selectedUnitId && (canClaim || canChoosePerson || canReturn);

  return (
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
      {hasMenuActions && (
        <CustomMenu
          customButton={
            <span className="text-custom-text-300 hover:bg-custom-background-80 text-xs flex shrink-0 items-center gap-1 rounded px-2 py-1">
              {t(`${KEY}.assign`)}
              <ChevronDown className="size-3" />
            </span>
          }
          placement="bottom-end"
          closeOnSelect
          ariaLabel={t(`${KEY}.assign`)}
        >
          <CustomMenu.MenuItem onClick={handleAutoAssign} disabled={isAssigning}>
            <span className="flex items-center gap-2">
              <Sparkles className="size-3.5" />
              {t(`${KEY}.assign`)}
            </span>
          </CustomMenu.MenuItem>
          {canClaim && (
            <CustomMenu.MenuItem onClick={handleClaim} disabled={pendingAction !== null}>
              {t(`${OU}.work.claim`)}
            </CustomMenu.MenuItem>
          )}
          {canChoosePerson && (
            <CustomMenu.MenuItem onClick={() => setIsPickingPerson(true)}>{t(`${OU}.work.assign`)}</CustomMenu.MenuItem>
          )}
          {canReturn && (
            <CustomMenu.MenuItem onClick={handleReturn} disabled={pendingAction !== null}>
              {t(`${OU}.work.return_to_queue`)}
            </CustomMenu.MenuItem>
          )}
        </CustomMenu>
      )}

      {selectedUnitId && (
        <AssignMemberModal
          isOpen={isPickingPerson}
          workspaceSlug={workspaceSlug}
          unitId={selectedUnitId}
          onAssign={async (userId) => {
            const nextRouting = await store.reassignIssueRouting(workspaceSlug, projectId, issueId, userId, {
              expectedDecisionId: routing?.current_assignment_decision?.id ?? null,
            });
            setRouting(nextRouting);
            onAssigned?.();
          }}
          onClose={() => setIsPickingPerson(false)}
        />
      )}
    </div>
  );
});
