/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
import { useNavigate, useParams } from "react-router";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { PageHead } from "@/components/core/page-title";
import { ExecutiveDashboard } from "@/components/orca/organizational-units";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { OrganizationalUnitsWorkspaceSettingsHeader } from "../header";

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The areas of a workspace side by side. Lives under workspace
 * settings because Plane already restricts that section to admins — the
 * aggregate is a management view (RFC F18), and putting it anywhere else would
 * mean reimplementing that restriction.
 */
const ExecutiveViewPage = observer(function ExecutiveViewPage() {
  const { workspaceSlug } = useParams();
  const navigate = useNavigate();
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  useEffect(() => {
    if (!workspaceSlug) return;
    store.fetchConfig(workspaceSlug.toString());
  }, [workspaceSlug, store]);

  const title = t(`${OU}.executive.title`);

  if (!store.isEnabled) {
    return (
      <SettingsContentWrapper header={<OrganizationalUnitsWorkspaceSettingsHeader />}>
        <PageHead title={title} />
        <div className="flex max-w-4xl flex-col gap-2 p-6">
          <h3 className="text-xl text-custom-text-100 font-medium">{title}</h3>
          <p className="text-sm text-custom-text-300">{t(`${OU}.disabled`)}</p>
        </div>
      </SettingsContentWrapper>
    );
  }

  return (
    <SettingsContentWrapper header={<OrganizationalUnitsWorkspaceSettingsHeader />}>
      <PageHead title={title} />
      <div className="flex flex-col gap-6 p-6">
        {workspaceSlug && (
          <ExecutiveDashboard
            workspaceSlug={workspaceSlug.toString()}
            // Drill-down is the area's own queue: the same rows, the same
            // permissions, no second implementation of either.
            onOpenUnit={() => navigate(`/${workspaceSlug}/settings/organizational-units`)}
          />
        )}
      </div>
    </SettingsContentWrapper>
  );
});

export default ExecutiveViewPage;
