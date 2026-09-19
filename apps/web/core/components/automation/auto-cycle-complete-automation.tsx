/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { CheckCircle2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import type { IProject } from "@plane/types";
import { ToggleSwitch } from "@plane/ui";
// component
import { SettingsControlItem } from "@/components/settings/control-item";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";

type Props = {
  handleChange: (formData: Partial<IProject>) => Promise<void>;
};

export const AutoCycleCompleteAutomation = observer(function AutoCycleCompleteAutomation(props: Props) {
  // translation
  const { t } = useTranslation();
  const { handleChange } = props;
  // router
  const { workspaceSlug } = useParams();
  // store hooks
  const { allowPermissions } = useUserPermissions();
  const { currentProjectDetails } = useProject();

  const isAdmin = allowPermissions(
    [EUserPermissions.ADMIN],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug?.toString(),
    currentProjectDetails?.id
  );

  const autoCompleteStatus = currentProjectDetails?.cycle_auto_complete ?? false;

  const handleToggle = async () => {
    await handleChange({ cycle_auto_complete: !autoCompleteStatus });
  };

  return (
    <div className="flex flex-col gap-4 border-b border-subtle py-2">
      <div className="flex items-center gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-sm bg-layer-2">
          <CheckCircle2 className="size-4 shrink-0 text-success-primary" />
        </div>
        <SettingsControlItem
          title={t("project_settings.automations.auto-cycle-complete.title")}
          description={t("project_settings.automations.auto-cycle-complete.description")}
          control={<ToggleSwitch value={autoCompleteStatus} onChange={handleToggle} size="sm" disabled={!isAdmin} />}
        />
      </div>
    </div>
  );
});
