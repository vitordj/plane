/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "react-router";
import { Plus } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Loader } from "@plane/ui";
// components
import { PageHead } from "@/components/core/page-title";
import {
  DirectorySyncPanel,
  OrganizationalUnitDetail,
  OrganizationalUnitFormModal,
  OrganizationalUnitList,
} from "@/components/orca/organizational-units";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { OrganizationalUnitsWorkspaceSettingsHeader } from "./header";

/** Falls back to English when a locale has not translated a key yet. */
const translate = (t: (key: string) => string, key: string, fallback: string) => {
  const value = t(key);
  return !value || value === key ? fallback : value;
};

const OrganizationalUnitsPage = observer(function OrganizationalUnitsPage() {
  const { workspaceSlug } = useParams();
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceSlug) return;
    setIsLoading(true);
    store.fetchUnits(workspaceSlug.toString()).finally(() => setIsLoading(false));
  }, [workspaceSlug, store]);

  const title = translate(t, "workspace_settings.settings.organizational_units.title", "Areas");
  const heading = translate(t, "workspace_settings.settings.organizational_units.heading", "Areas");
  const description = translate(
    t,
    "workspace_settings.settings.organizational_units.description",
    "Group people into areas and give each area access to its projects in one step."
  );

  const selectedUnit = selectedUnitId ? store.getUnitById(selectedUnitId) : undefined;

  return (
    <SettingsContentWrapper header={<OrganizationalUnitsWorkspaceSettingsHeader />}>
      <PageHead title={title} />
      <div className="flex max-w-4xl flex-col gap-6 p-6">
        {selectedUnit && workspaceSlug ? (
          <OrganizationalUnitDetail
            workspaceSlug={workspaceSlug.toString()}
            unit={selectedUnit}
            onBack={() => setSelectedUnitId(null)}
          />
        ) : (
          <>
            <div className="flex items-start justify-between gap-4 border-b border-subtle pb-6">
              <div>
                <h3 className="text-xl text-custom-text-100 font-medium">{heading}</h3>
                <p className="text-sm text-custom-text-300">{description}</p>
              </div>
              <Button variant="primary" size="sm" onClick={() => setIsCreating(true)} prependIcon={<Plus />}>
                {translate(t, "workspace_settings.settings.organizational_units.add", "New area")}
              </Button>
            </div>

            {isLoading ? (
              <Loader className="flex flex-col gap-2">
                <Loader.Item height="56px" />
                <Loader.Item height="56px" />
                <Loader.Item height="56px" />
              </Loader>
            ) : (
              workspaceSlug && (
                <OrganizationalUnitList
                  workspaceSlug={workspaceSlug.toString()}
                  units={store.units}
                  onSelect={(unit) => setSelectedUnitId(unit.id)}
                />
              )
            )}

            {/* Directory provisioning sits below the list on purpose: areas
                work perfectly well without it, and the section is only
                actionable for workspace admins. */}
            {workspaceSlug && <DirectorySyncPanel workspaceSlug={workspaceSlug.toString()} />}
          </>
        )}
      </div>

      {workspaceSlug && (
        <OrganizationalUnitFormModal
          isOpen={isCreating}
          workspaceSlug={workspaceSlug.toString()}
          onClose={() => setIsCreating(false)}
        />
      )}
    </SettingsContentWrapper>
  );
});

export default OrganizationalUnitsPage;
