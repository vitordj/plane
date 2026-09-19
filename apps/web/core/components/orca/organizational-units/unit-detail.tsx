/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { ArrowLeft, Pencil } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { IOrganizationalUnit } from "@plane/types";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { useUserPermissions } from "@/hooks/store/user";
// components
import { OrganizationalUnitFormModal } from "./unit-form-modal";
import { OrganizationalUnitMembersTab } from "./unit-members-tab";
import { OrganizationalUnitProjectsTab } from "./unit-projects-tab";
import { OrganizationalUnitWorkTab } from "./unit-work-tab";
import { OrganizationalUnitCoordinatorsTab } from "./coordinators-tab";
import { OrganizationalUnitPolicyForm } from "./policy-form";

type Props = {
  workspaceSlug: string;
  unit: IOrganizationalUnit;
  onBack: () => void;
};

type TTab = "members" | "projects" | "work" | "coordinators" | "policy";

const OU = "workspace_settings.settings.organizational_units";

export const OrganizationalUnitDetail = observer(function OrganizationalUnitDetail(props: Props) {
  const { workspaceSlug, unit, onBack } = props;
  const { t } = useTranslation();
  const store = useOrganizationalUnit();
  const { allowPermissions } = useUserPermissions();
  const [activeTab, setActiveTab] = useState<TTab>("members");
  const [isEditing, setIsEditing] = useState(false);

  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE, workspaceSlug);

  useEffect(() => {
    store.fetchCoordinators(workspaceSlug, unit.id).catch(() => undefined);
  }, [workspaceSlug, unit.id, store]);

  // The queue is counted from what the Work tab has actually fetched, so the
  // badge reads 0 until then rather than inventing a number the tab would
  // contradict a moment later.
  const waitingCount = store.getQueueByUnitId(unit.id).waiting.length;
  const coordinatorCount = store.getCoordinatorsByUnitId(unit.id).filter((coordinator) => coordinator.is_active).length;

  const tabs: { key: TTab; label: string; count?: number }[] = [
    { key: "members", label: t(`${OU}.detail.tab_people`), count: unit.member_count },
    { key: "projects", label: t("common.projects"), count: unit.project_count },
    { key: "work", label: t(`${OU}.work.tab`), count: waitingCount },
    ...(isAdmin
      ? [
          { key: "coordinators" as const, label: t(`${OU}.coordinators.tab`), count: coordinatorCount },
          { key: "policy" as const, label: t(`${OU}.policy.tab`) },
        ]
      : []),
  ];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <button
            type="button"
            aria-label={t(`${OU}.detail.back_aria`)}
            className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 mt-1 rounded p-1 outline-none focus-visible:ring-2"
            onClick={onBack}
          >
            <ArrowLeft className="size-4" />
          </button>
          <div className="min-w-0">
            <h3 className="text-xl text-custom-text-100 truncate font-medium">{unit.name}</h3>
            {unit.description && <p className="text-sm text-custom-text-300">{unit.description}</p>}
          </div>
        </div>
        <Button variant="secondary" size="sm" onClick={() => setIsEditing(true)} prependIcon={<Pencil />}>
          {t("common.edit")}
        </Button>
      </div>

      <div className="border-custom-border-200 flex gap-1 border-b" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.key}
            className={`text-sm focus-visible:ring-custom-primary-100 -mb-px border-b-2 px-3 py-2 transition-colors outline-none focus-visible:ring-2 ${
              activeTab === tab.key
                ? "border-custom-primary-100 text-custom-text-100"
                : "text-custom-text-300 hover:text-custom-text-200 border-transparent"
            }`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
            {tab.count != null && <span className="text-xs text-custom-text-400 ml-1.5">{tab.count}</span>}
          </button>
        ))}
      </div>

      {activeTab === "work" && <OrganizationalUnitWorkTab workspaceSlug={workspaceSlug} unitId={unit.id} />}
      {activeTab === "members" && <OrganizationalUnitMembersTab workspaceSlug={workspaceSlug} unitId={unit.id} />}
      {activeTab === "projects" && <OrganizationalUnitProjectsTab workspaceSlug={workspaceSlug} unitId={unit.id} />}
      {activeTab === "coordinators" && (
        <OrganizationalUnitCoordinatorsTab workspaceSlug={workspaceSlug} unitId={unit.id} />
      )}
      {activeTab === "policy" && <OrganizationalUnitPolicyForm workspaceSlug={workspaceSlug} unitId={unit.id} />}

      <OrganizationalUnitFormModal
        isOpen={isEditing}
        workspaceSlug={workspaceSlug}
        unit={unit}
        onClose={() => setIsEditing(false)}
      />
    </div>
  );
});
