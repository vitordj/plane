/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { ArrowLeft, Pencil } from "lucide-react";
// plane imports
import { Button } from "@plane/propel/button";
import type { IOrganizationalUnit } from "@plane/types";
// components
import { OrganizationalUnitFormModal } from "./unit-form-modal";
import { OrganizationalUnitMembersTab } from "./unit-members-tab";
import { OrganizationalUnitProjectsTab } from "./unit-projects-tab";

type Props = {
  workspaceSlug: string;
  unit: IOrganizationalUnit;
  onBack: () => void;
};

type TTab = "members" | "projects";

export const OrganizationalUnitDetail = observer(function OrganizationalUnitDetail(props: Props) {
  const { workspaceSlug, unit, onBack } = props;
  const [activeTab, setActiveTab] = useState<TTab>("members");
  const [isEditing, setIsEditing] = useState(false);

  const tabs: { key: TTab; label: string; count: number }[] = [
    { key: "members", label: "People", count: unit.member_count },
    { key: "projects", label: "Projects", count: unit.project_count },
  ];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <button
            type="button"
            aria-label="Back to all areas"
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
          Edit
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
            <span className="text-xs text-custom-text-400 ml-1.5">{tab.count}</span>
          </button>
        ))}
      </div>

      {activeTab === "members" ? (
        <OrganizationalUnitMembersTab workspaceSlug={workspaceSlug} unitId={unit.id} />
      ) : (
        <OrganizationalUnitProjectsTab workspaceSlug={workspaceSlug} unitId={unit.id} />
      )}

      <OrganizationalUnitFormModal
        isOpen={isEditing}
        workspaceSlug={workspaceSlug}
        unit={unit}
        onClose={() => setIsEditing(false)}
      />
    </div>
  );
});
