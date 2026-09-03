/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Link, useParams } from "react-router";
import { BarChart3, Plus } from "lucide-react";
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

const OU = "workspace_settings.settings.organizational_units";

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
    // Ask whether the layer is switched on before listing: with
    // ORCA_ORG_UNITS_ENABLED=0 the list endpoint answers 404, and the page
    // should say the feature is off rather than render an empty area list as
    // if the workspace simply had none.
    store.fetchConfig(workspaceSlug.toString());
    store.fetchUnits(workspaceSlug.toString()).finally(() => setIsLoading(false));
  }, [workspaceSlug, store]);

  const title = t(`${OU}.title`);
  const heading = t(`${OU}.heading`);
  const description = t(`${OU}.description`);

  const selectedUnit = selectedUnitId ? store.getUnitById(selectedUnitId) : undefined;

  // Reachable by typing the URL even though the nav entry is hidden.
  if (!store.isEnabled) {
    return (
      <SettingsContentWrapper header={<OrganizationalUnitsWorkspaceSettingsHeader />}>
        <PageHead title={title} />
        <div className="flex max-w-4xl flex-col gap-2 p-6">
          <h3 className="text-xl text-custom-text-100 font-medium">{heading}</h3>
          <p className="text-sm text-custom-text-300">{t(`${OU}.disabled`)}</p>
        </div>
      </SettingsContentWrapper>
    );
  }

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
              <div className="flex shrink-0 items-center gap-2">
                {/* The aggregate view lives one click away rather than as a
                    tab: it answers a different question from this page, and
                    mixing the two makes both harder to find. */}
                <Link
                  to={`/${workspaceSlug}/settings/organizational-units/executive`}
                  className="text-body-xs-medium text-custom-text-300 hover:text-custom-text-100 flex items-center gap-1"
                >
                  <BarChart3 className="size-3.5" />
                  {t(`${OU}.executive.title`)}
                </Link>
                <Button variant="primary" size="sm" onClick={() => setIsCreating(true)} prependIcon={<Plus />}>
                  {t(`${OU}.add`)}
                </Button>
              </div>
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
