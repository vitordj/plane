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
import { AvailabilityForm, OrganizationalUnitWorkTab } from "@/components/orca/organizational-units";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The queue of the areas this person belongs to, without going
 * through workspace settings — which are admin-only and are about configuring
 * areas rather than working in them.
 *
 * One area at a time, chosen by a tab: the sections inside are already a lot
 * to read, and somebody who belongs to four areas is answering "what does
 * *this* area need from me" rather than merging four queues into one list.
 */
const MyAreasPage = observer(function MyAreasPage() {
  const { workspaceSlug } = useParams();
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceSlug) return;
    setIsLoading(true);
    const load = async () => {
      try {
        const units = await store.fetchMyUnits(workspaceSlug.toString());
        // Whichever area was already chosen wins, so a refresh does not throw
        // the reader back to the first tab.
        setSelectedUnitId((current) => current ?? units[0]?.organizational_unit.id ?? null);
      } catch {
        // The empty state below is the honest answer to a failed read.
      } finally {
        setIsLoading(false);
      }
    };
    void load();
  }, [workspaceSlug, store]);

  const title = t(`${OU}.my_areas.title`);
  const myUnits = store.myUnits ?? [];

  if (!workspaceSlug) return null;

  return (
    <>
      <PageHead title={title} />
      <div className="flex h-full w-full flex-col gap-5 overflow-y-auto p-6">
        <div>
          <h3 className="text-xl text-custom-text-100 font-medium">{title}</h3>
          <p className="text-sm text-custom-text-300">{t(`${OU}.my_areas.description`)}</p>
        </div>

        {/* Your own absences live here rather than in profile Preferences: an
            absence belongs to a workspace membership, and that page is not
            workspace-scoped. This is also the page where knowing you are
            marked away actually matters. */}
        <AvailabilityForm workspaceSlug={workspaceSlug.toString()} />

        {isLoading ? (
          <Loader className="flex flex-col gap-2">
            <Loader.Item height="48px" />
            <Loader.Item height="48px" />
          </Loader>
        ) : myUnits.length === 0 ? (
          <p className="text-sm text-custom-text-300 py-10 text-center">{t(`${OU}.my_areas.empty`)}</p>
        ) : (
          <>
            <div className="border-custom-border-200 flex flex-wrap gap-1 border-b" role="tablist">
              {myUnits.map((entry) => (
                <button
                  key={entry.organizational_unit.id}
                  type="button"
                  role="tab"
                  aria-selected={selectedUnitId === entry.organizational_unit.id}
                  className={`text-sm focus-visible:ring-custom-primary-100 -mb-px border-b-2 px-3 py-2 transition-colors outline-none focus-visible:ring-2 ${
                    selectedUnitId === entry.organizational_unit.id
                      ? "border-custom-primary-100 text-custom-text-100"
                      : "text-custom-text-300 hover:text-custom-text-200 border-transparent"
                  }`}
                  onClick={() => setSelectedUnitId(entry.organizational_unit.id)}
                >
                  {entry.organizational_unit.name}
                </button>
              ))}
            </div>

            {selectedUnitId && (
              <OrganizationalUnitWorkTab workspaceSlug={workspaceSlug.toString()} unitId={selectedUnitId} />
            )}
          </>
        )}
      </div>
    </>
  );
});

export default MyAreasPage;
