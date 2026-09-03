/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Loader } from "@plane/ui";
// components
import { OrganizationalUnitWorkTab } from "./unit-work-tab";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The work the areas this person belongs to have to get done.
 * Separate from workspace settings on purpose: settings is where an admin
 * configures areas, this is where the people in them work.
 */
export const MyAreasRoot = observer(function MyAreasRoot(props: Props) {
  const { workspaceSlug } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    store
      .fetchMyUnits(workspaceSlug)
      .then((units) => {
        if (cancelled) return;
        setSelectedUnitId((current) => current ?? units?.[0]?.id ?? null);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, store]);

  const myUnits = store.myUnits ?? [];

  if (isLoading) {
    return (
      <Loader className="space-y-3">
        <Loader.Item height="32px" width="240px" />
        <Loader.Item height="120px" />
      </Loader>
    );
  }

  if (myUnits.length === 0) {
    return (
      <div className="py-16 text-center">
        <p className="text-custom-text-300 text-body-sm-medium">{t(`${OU}.my_areas.empty_title`)}</p>
        <p className="text-custom-text-400 text-body-xs-regular mt-1">{t(`${OU}.my_areas.empty_message`)}</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {myUnits.length > 1 && (
        <div className="border-custom-border-200 flex gap-1 border-b" role="tablist">
          {myUnits.map((unit) => (
            <button
              key={unit.id}
              type="button"
              role="tab"
              aria-selected={selectedUnitId === unit.id}
              className={`text-body-xs-medium -mb-px border-b-2 px-3 py-2 ${
                selectedUnitId === unit.id
                  ? "border-custom-primary-100 text-custom-text-100"
                  : "text-custom-text-300 border-transparent"
              }`}
              onClick={() => setSelectedUnitId(unit.id)}
            >
              {unit.name}
            </button>
          ))}
        </div>
      )}

      {selectedUnitId && <OrganizationalUnitWorkTab workspaceSlug={workspaceSlug} unitId={selectedUnitId} />}
    </div>
  );
});
