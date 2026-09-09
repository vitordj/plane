/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "react-router";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Loader } from "@plane/ui";
// components
import { PageHead } from "@/components/core/page-title";
import { OrganizationalUnitWorkTab } from "@/components/orca/organizational-units";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

const OU = "workspace_settings.settings.organizational_units";

const MyAreasPage = observer(function MyAreasPage() {
  const { workspaceSlug } = useParams();
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(true);
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceSlug) return;
    setIsLoading(true);
    store
      .fetchMyUnits(workspaceSlug)
      .then((units) => {
        setSelectedUnitId((current) => current ?? units[0]?.organizational_unit.id ?? null);
        return undefined;
      })
      .finally(() => setIsLoading(false));
  }, [workspaceSlug, store]);

  const units = store.myUnits ?? [];
  const selected = units.find((entry) => entry.organizational_unit.id === selectedUnitId);

  return (
    <>
      <PageHead title={t(`${OU}.my_areas.title`)} />
      <div className="flex h-full flex-col gap-5 p-6">
        <div>
          <h3 className="text-xl text-custom-text-100 font-medium">{t(`${OU}.my_areas.heading`)}</h3>
          <p className="text-sm text-custom-text-300">{t(`${OU}.my_areas.description`)}</p>
        </div>

        {isLoading ? (
          <Loader className="flex flex-col gap-2">
            <Loader.Item height="40px" width="240px" />
            <Loader.Item height="160px" />
          </Loader>
        ) : units.length === 0 ? (
          <p className="text-sm text-custom-text-300 py-8 text-center">{t(`${OU}.my_areas.empty`)}</p>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col gap-5">
            <div className="flex flex-wrap gap-2" role="tablist" aria-label={t(`${OU}.my_areas.select`)}>
              {units.map((entry) => (
                <button
                  key={entry.organizational_unit.id}
                  type="button"
                  role="tab"
                  aria-selected={selectedUnitId === entry.organizational_unit.id}
                  className={`text-sm focus-visible:ring-custom-primary-100 rounded border px-3 py-1.5 outline-none focus-visible:ring-2 ${
                    selectedUnitId === entry.organizational_unit.id
                      ? "border-custom-primary-100 text-custom-text-100 bg-custom-primary-100/10"
                      : "border-custom-border-200 text-custom-text-300 hover:text-custom-text-200"
                  }`}
                  onClick={() => setSelectedUnitId(entry.organizational_unit.id)}
                >
                  {entry.organizational_unit.name}
                </button>
              ))}
            </div>
            {workspaceSlug && selected && (
              <OrganizationalUnitWorkTab workspaceSlug={workspaceSlug} unitId={selected.organizational_unit.id} />
            )}
          </div>
        )}
      </div>
    </>
  );
});

export default MyAreasPage;
