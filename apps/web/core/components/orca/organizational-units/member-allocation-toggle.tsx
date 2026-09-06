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
import type { IMembershipAllocation } from "@plane/types";
import { ToggleSwitch } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
  membershipId: string;
  /** Coordinators and admins also see the area's ceiling for this person. */
  canSetLimit?: boolean;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Whether this area's ranking may pick this person, shown next to
 * them in the area's member list.
 *
 * Deliberately separate from availability: being away is about the person
 * everywhere, and this is about one area. Somebody can be perfectly available
 * and still not be taking new work from one of their three areas.
 *
 * Turning it off takes nothing away — work already held stays held. It only
 * stops the area putting more on them.
 */
export const MemberAllocationToggle = observer(function MemberAllocationToggle(props: Props) {
  const { workspaceSlug, unitId, membershipId, canSetLimit } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [allocation, setAllocation] = useState<IMembershipAllocation | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await store.fetchMembershipAllocation(workspaceSlug, unitId, membershipId);
        if (!cancelled) setAllocation(response);
      } catch {
        // Availability is off on this instance, or the reader may not ask.
        if (!cancelled) setAllocation(null);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, unitId, membershipId, store]);

  if (allocation === null) return null;

  const handleToggle = async () => {
    setIsSaving(true);
    const next = !allocation.accepts_new_work;
    try {
      const response = await store.writeMembershipAllocation(workspaceSlug, unitId, membershipId, {
        accepts_new_work: next,
      });
      setAllocation(response);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.availability.toast.not_saved`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-custom-text-300">{t(`${OU}.availability.accepts_new_work`)}</span>
      <ToggleSwitch value={allocation.accepts_new_work} onChange={handleToggle} disabled={isSaving} size="sm" />
      {canSetLimit && allocation.max_open_items !== null && (
        <span className="text-xs text-custom-text-400">
          {t(`${OU}.availability.limit_of`, { count: allocation.max_open_items })}
        </span>
      )}
    </div>
  );
});
