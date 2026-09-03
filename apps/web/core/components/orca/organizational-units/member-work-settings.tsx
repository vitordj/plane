/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { ToggleSwitch, Input } from "@plane/ui";
// components
import { AvailabilityForm } from "./availability-form";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
  membershipId: string;
  workspaceMemberId: string;
  /** True when the reader may change these — a coordinator or an admin. */
  canEdit?: boolean;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description What this area may hand one person, and when they are away.
 * Two settings and a list, on purpose: the alternative was a "capacity" screen
 * that nobody would keep up to date.
 *
 * Neither setting hides anybody. They keep automatic allocation from adding to
 * what somebody carries; a coordinator can still hand them a work item by
 * name, because sometimes that is exactly right.
 */
export const MemberWorkSettings = observer(function MemberWorkSettings(props: Props) {
  const { workspaceSlug, unitId, membershipId, workspaceMemberId, canEdit = true } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [acceptsNewWork, setAcceptsNewWork] = useState(true);
  const [maxOpenItems, setMaxOpenItems] = useState<string>("");
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    store.service
      .getAllocationSettings(workspaceSlug, unitId, membershipId)
      .then((settings) => {
        if (cancelled) return;
        setAcceptsNewWork(settings.accepts_new_work);
        setMaxOpenItems(settings.max_open_items === null ? "" : String(settings.max_open_items));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, unitId, membershipId, store]);

  const save = async (payload: { accepts_new_work?: boolean; max_open_items?: number | null }) => {
    setIsSaving(true);
    try {
      await store.setAllocationSettings(workspaceSlug, unitId, membershipId, payload);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.allocation.saved`) });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.allocation.not_saved`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleToggle = async () => {
    const next = !acceptsNewWork;
    setAcceptsNewWork(next);
    await save({ accepts_new_work: next });
  };

  const handleLimitBlur = async () => {
    const trimmed = maxOpenItems.trim();
    if (trimmed === "") return save({ max_open_items: null });
    const parsed = Number(trimmed);
    if (!Number.isInteger(parsed) || parsed < 1) {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.allocation.limit_invalid`) });
      return;
    }
    await save({ max_open_items: parsed });
  };

  return (
    <div className="bg-custom-background-90/40 space-y-4 rounded px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-body-xs-medium text-custom-text-200">{t(`${OU}.allocation.accepts_new_work`)}</p>
          <p className="text-custom-text-400 text-body-2xs-regular">
            {t(`${OU}.allocation.accepts_new_work_help`)}
          </p>
        </div>
        <ToggleSwitch value={acceptsNewWork} onChange={handleToggle} disabled={!canEdit || isSaving} />
      </div>

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-body-xs-medium text-custom-text-200">{t(`${OU}.allocation.max_open_items`)}</p>
          <p className="text-custom-text-400 text-body-2xs-regular">{t(`${OU}.allocation.max_open_items_help`)}</p>
        </div>
        <Input
          type="number"
          min={1}
          value={maxOpenItems}
          onChange={(event) => setMaxOpenItems(event.target.value)}
          onBlur={handleLimitBlur}
          disabled={!canEdit || isSaving}
          placeholder={t(`${OU}.allocation.no_limit`)}
          className="w-28"
        />
      </div>

      <AvailabilityForm
        workspaceSlug={workspaceSlug}
        workspaceMemberId={workspaceMemberId}
        canEdit={canEdit}
      />
    </div>
  );
});
