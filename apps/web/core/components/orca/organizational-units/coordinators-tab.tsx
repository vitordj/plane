/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IUnitCoordinator } from "@plane/types";
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
 * @description Who runs this area's queue. A coordinator is not a member: they
 * see and move the work without the ranking ever handing it to them, which is
 * why appointing one does not put them into the rotation.
 */
export const CoordinatorsTab = observer(function CoordinatorsTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const {
    workspace: { workspaceMemberIds, getWorkspaceMemberDetails },
  } = useMember();

  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [coordinators, setCoordinators] = useState<IUnitCoordinator[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const load = async () => {
    const rows = await store.service.getCoordinators(workspaceSlug, unitId);
    setCoordinators(rows ?? []);
  };

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    load()
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, unitId]);

  const options = useMemo(() => {
    const already = new Set(coordinators.map((coordinator) => coordinator.workspace_member));
    return (workspaceMemberIds ?? [])
      .map((userId) => getWorkspaceMemberDetails(userId))
      .filter((details) => details && !already.has(details.id))
      .map((details) => ({
        value: details!.id,
        query: details!.member.display_name,
        content: (
          <div className="flex items-center gap-2">
            <Avatar name={details!.member.display_name} src={details!.member.avatar_url} size="sm" />
            <span className="truncate">{details!.member.display_name}</span>
          </div>
        ),
      }));
  }, [coordinators, workspaceMemberIds, getWorkspaceMemberDetails]);

  const handleAdd = async () => {
    if (!selected) return;
    setIsSaving(true);
    try {
      await store.service.addCoordinator(workspaceSlug, unitId, selected);
      setSelected(null);
      await load();
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.coordinators.added`) });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.coordinators.not_added`) });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemove = async (coordinator: IUnitCoordinator) => {
    try {
      await store.service.removeCoordinator(workspaceSlug, unitId, coordinator.id);
      await load();
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.coordinators.removed`) });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.coordinators.not_removed`) });
    }
  };

  if (isLoading) {
    return (
      <Loader className="space-y-2">
        <Loader.Item height="36px" />
        <Loader.Item height="36px" />
      </Loader>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-custom-text-400 text-body-2xs-regular">{t(`${OU}.coordinators.help`)}</p>

      <div className="flex items-center gap-2">
        <CustomSearchSelect
          value={selected}
          options={options}
          onChange={(value: string) => setSelected(value)}
          label={
            selected
              ? (getWorkspaceMemberDetails(selected)?.member.display_name ?? t(`${OU}.coordinators.pick`))
              : t(`${OU}.coordinators.pick`)
          }
          className="flex-1"
          maxHeight="md"
        />
        <Button variant="primary" size="sm" loading={isSaving} disabled={!selected} onClick={handleAdd}>
          {t(`${OU}.coordinators.add`)}
        </Button>
      </div>

      {coordinators.length === 0 ? (
        <p className="text-custom-text-400 text-body-xs-regular py-4 text-center">
          {t(`${OU}.coordinators.empty`)}
        </p>
      ) : (
        <ul className="border-custom-border-200 divide-custom-border-200 divide-y rounded-md border">
          {coordinators.map((coordinator) => (
            <li key={coordinator.id} className="flex items-center gap-2 px-3 py-2">
              <Avatar name={coordinator.display_name} src={coordinator.avatar_url} size="sm" />
              <span className="text-body-xs-regular text-custom-text-100 flex-1 truncate">
                {coordinator.display_name}
              </span>
              <button
                type="button"
                aria-label={t(`${OU}.coordinators.remove_aria`, { name: coordinator.display_name })}
                className="text-custom-text-400 hover:text-custom-text-200 rounded p-1"
                onClick={() => handleRemove(coordinator)}
              >
                <X className="size-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
