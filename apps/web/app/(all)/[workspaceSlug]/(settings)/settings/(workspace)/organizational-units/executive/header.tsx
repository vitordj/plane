/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "react-router";
// plane imports
import { WORKSPACE_SETTINGS } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Breadcrumbs } from "@plane/ui";
// components
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { SettingsPageHeader } from "@/components/settings/page-header";
import { WORKSPACE_SETTINGS_ICONS } from "@/components/settings/workspace/sidebar/item-icon";

const OU = "workspace_settings.settings.organizational_units";

export const OrganizationalUnitsExecutiveHeader = observer(function OrganizationalUnitsExecutiveHeader() {
  const { workspaceSlug } = useParams();
  const { t } = useTranslation();
  const settingsDetails = WORKSPACE_SETTINGS["organizational-units"];
  const Icon = WORKSPACE_SETTINGS_ICONS["organizational-units"];
  const areasHref = workspaceSlug ? `/${workspaceSlug}/settings/organizational-units` : undefined;

  return (
    <SettingsPageHeader
      leftItem={
        <div className="flex items-center gap-2">
          <Breadcrumbs>
            <Breadcrumbs.Item
              component={
                <BreadcrumbLink
                  href={areasHref}
                  label={t(settingsDetails.i18n_label)}
                  icon={<Icon className="size-4 text-tertiary" />}
                />
              }
            />
            <Breadcrumbs.Item component={<BreadcrumbLink label={t(`${OU}.executive.title`)} isLast />} />
          </Breadcrumbs>
        </div>
      }
    />
  );
});
