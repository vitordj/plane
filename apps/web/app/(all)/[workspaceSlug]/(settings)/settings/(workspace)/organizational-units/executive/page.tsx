/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
import { useParams } from "react-router";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { PageHead } from "@/components/core/page-title";
import { ExecutiveTable } from "@/components/orca/organizational-units";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { OrganizationalUnitsExecutiveHeader } from "./header";

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The cross-area view (item 5.3). Workspace settings restrict the
 * whole section to admins, which is the access rule F23 asked for; the
 * endpoint enforces it again rather than trusting the route.
 */
const OrganizationalUnitsExecutivePage = observer(function OrganizationalUnitsExecutivePage() {
  const { workspaceSlug } = useParams();
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  useEffect(() => {
    if (!workspaceSlug) return;
    // Same reason as the areas page: with the layer switched off the endpoint
    // answers 404, and an empty table would read as "no areas" rather than
    // "this instance does not have areas".
    store.fetchConfig(workspaceSlug.toString());
  }, [workspaceSlug, store]);

  const title = t(`${OU}.executive.title`);

  if (!store.isEnabled)
    return (
      <SettingsContentWrapper header={<OrganizationalUnitsExecutiveHeader />}>
        <PageHead title={title} />
        <div className="flex max-w-6xl flex-col gap-2 p-6">
          <h3 className="text-xl text-custom-text-100 font-medium">{title}</h3>
          <p className="text-sm text-custom-text-300">{t(`${OU}.disabled`)}</p>
        </div>
      </SettingsContentWrapper>
    );

  return (
    <SettingsContentWrapper header={<OrganizationalUnitsExecutiveHeader />}>
      <PageHead title={title} />
      <div className="flex max-w-6xl flex-col gap-6 p-6">
        <div className="border-b border-subtle pb-6">
          <h3 className="text-xl text-custom-text-100 font-medium">{title}</h3>
          <p className="text-sm text-custom-text-300">{t(`${OU}.executive.description`)}</p>
        </div>
        {workspaceSlug && <ExecutiveTable workspaceSlug={workspaceSlug.toString()} />}
      </div>
    </SettingsContentWrapper>
  );
});

export default OrganizationalUnitsExecutivePage;
