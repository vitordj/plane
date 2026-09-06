/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { X } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
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
 * @description Who runs this area's queue. Appointing somebody here grants
 * them native access to every project the area covers — a coordinator who
 * cannot open the items in their own queue cannot do the job — which is why
 * this screen, like the members one, is workspace-admin only.
 */
export const OrganizationalUnitCoordinatorsTab = observer(function OrganizationalUnitCoordinatorsTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const {
    workspace: { workspaceMemberIds, getWorkspaceMemberDetails },
  } = useMember();

  const [isLoading, setIsLoading] = useState(true);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [isAdding, setIsAdding] = useState(false);

  useEffect(() => {
    setIsLoading(true);
    store
      .fetchCoordinators(workspaceSlug, unitId)
      .catch(() => undefined)
      .finally(() => setIsLoading(false));
  }, [workspaceSlug, unitId, store]);

  const coordinators = store.getCoordinatorsByUnitId(unitId);

  // Keyed by *user* id, not workspace-member id: the coordinator endpoint
  // takes the person, since a coordination is about who runs the area rather
  // than about a membership row.
  const options = useMemo(() => {
    const already = new Set(coordinators.filter((row) => row.is_active).map((row) => row.member_id));
    return (workspaceMemberIds ?? [])
      .filter((userId) => !already.has(userId))
      .map((userId) => getWorkspaceMemberDetails(userId))
      .filter((details) => !!details)
      .map((details) => ({
        value: details!.member.id,
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
    if (!selectedUserId) return;
    setIsAdding(true);
    try {
      await store.addCoordinator(workspaceSlug, unitId, selectedUserId);
      setSelectedUserId(null);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.coordinators.toast.added`) });
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
        title: t(`${OU}.coordinators.toast.removed`),
        message: displayName,
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

  const active = coordinators.filter((row) => row.is_active);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <CustomSearchSelect
          value={selectedUserId}
          options={options}
          onChange={(value: string) => setSelectedUserId(value)}
          label={
            selectedUserId
              ? (options.find((option) => option.value === selectedUserId)?.query ??
                t(`${OU}.coordinators.select_person`))
              : t(`${OU}.coordinators.select_person`)
          }
          maxHeight="md"
          noResultsMessage={t(`${OU}.coordinators.no_addable`)}
        />
        <Button variant="primary" size="sm" onClick={handleAdd} loading={isAdding} disabled={!selectedUserId}>
          {t(`${OU}.coordinators.add`)}
        </Button>
      </div>

      {active.length === 0 ? (
        <p className="text-sm text-custom-text-300 py-8 text-center">{t(`${OU}.coordinators.empty`)}</p>
      ) : (
        <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
          {active.map((coordinator) => (
            <div key={coordinator.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="flex min-w-0 items-center gap-2">
                <Avatar name={coordinator.display_name} src={coordinator.avatar_url} size="md" />
                <div className="min-w-0">
                  <p className="text-sm text-custom-text-100 truncate">{coordinator.display_name}</p>
                  <p className="text-xs text-custom-text-300 truncate">{coordinator.email}</p>
                </div>
              </div>
              <button
                type="button"
                aria-label={t(`${OU}.coordinators.remove_aria`, { name: coordinator.display_name })}
                className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 rounded p-1 outline-none focus-visible:ring-2"
                onClick={() => handleRemove(coordinator.id, coordinator.display_name)}
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
