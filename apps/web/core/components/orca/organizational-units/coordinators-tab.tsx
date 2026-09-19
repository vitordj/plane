/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { X } from "lucide-react";
// plane imports
import { EUserPermissions, resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { Avatar, CustomSearchSelect, Loader } from "@plane/ui";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Who operates this area's queue. Distinct from membership: a
 * coordinator need not belong to the area (RFC §5.2). Writes are
 * workspace-admin only; the API refuses anything else.
 */
export const OrganizationalUnitCoordinatorsTab = observer(function OrganizationalUnitCoordinatorsTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const {
    workspace: { workspaceMemberIds, getWorkspaceMemberDetails },
  } = useMember();

  const [isLoading, setIsLoading] = useState(true);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [isAdding, setIsAdding] = useState(false);

  const coordinators = store.getCoordinatorsByUnitId(unitId);

  useEffect(() => {
    setIsLoading(true);
    store.fetchCoordinators(workspaceSlug, unitId).finally(() => setIsLoading(false));
  }, [workspaceSlug, unitId, store]);

  const addableOptions = useMemo(() => {
    const already = new Set(coordinators.filter((row) => row.is_active).map((row) => row.workspace_member));
    return (workspaceMemberIds ?? [])
      .map((userId) => getWorkspaceMemberDetails(userId))
      .filter((details) => details && details.role !== EUserPermissions.GUEST && !already.has(details.id))
      .map((details) => ({
        value: details!.id,
        query: `${details!.member.display_name} ${details!.member.email ?? ""}`,
        content: (
          <div className="flex items-center gap-2">
            <Avatar name={details!.member.display_name} src={details!.member.avatar_url} size="sm" />
            <span className="truncate">{details!.member.display_name}</span>
          </div>
        ),
      }));
  }, [coordinators, workspaceMemberIds, getWorkspaceMemberDetails]);

  const handleAdd = async () => {
    if (!selectedMemberId) return;
    setIsAdding(true);
    try {
      await store.addCoordinator(workspaceSlug, unitId, selectedMemberId);
      setSelectedMemberId(null);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.coordinators.toast.added_title`),
        message: t(`${OU}.coordinators.toast.added`),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.coordinators.toast.not_added`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemove = async (coordinatorId: string, displayName: string) => {
    try {
      await store.removeCoordinator(workspaceSlug, unitId, coordinatorId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.coordinators.toast.removed_title`),
        message: t(`${OU}.coordinators.toast.removed`, { name: displayName }),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.coordinators.toast.not_removed`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    }
  };

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
      </Loader>
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <CustomSearchSelect
          value={selectedMemberId}
          options={addableOptions}
          onChange={(value: string) => setSelectedMemberId(value)}
          label={
            selectedMemberId
              ? (addableOptions.find((option) => option.value === selectedMemberId)?.query ??
                t(`${OU}.coordinators.select_person`))
              : t(`${OU}.coordinators.select_person`)
          }
          maxHeight="md"
          noResultsMessage={t(`${OU}.coordinators.no_addable`)}
        />
        <Button variant="primary" size="sm" onClick={handleAdd} loading={isAdding} disabled={!selectedMemberId}>
          {t(`${OU}.coordinators.add`)}
        </Button>
      </div>

      {coordinators.length === 0 ? (
        <p className="text-sm text-custom-text-300 py-8 text-center">{t(`${OU}.coordinators.empty`)}</p>
      ) : (
        <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
          {coordinators.map((coordinator) => (
            <div key={coordinator.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="flex min-w-0 items-center gap-2">
                <Avatar name={coordinator.member.display_name} src={coordinator.member.avatar_url} size="md" />
                <div className="min-w-0">
                  <p className="text-sm text-custom-text-100 truncate">{coordinator.member.display_name}</p>
                  <p className="text-xs text-custom-text-300 truncate">{coordinator.member.email}</p>
                </div>
              </div>
              <button
                type="button"
                aria-label={t(`${OU}.coordinators.remove_aria`, { name: coordinator.member.display_name })}
                className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 rounded p-1 outline-none focus-visible:ring-2"
                onClick={() => handleRemove(coordinator.id, coordinator.member.display_name)}
              >
                <X className="size-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-custom-text-300">{t(`${OU}.coordinators.note`)}</p>
    </div>
  );
});
