/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { X } from "lucide-react";
// plane imports
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { EUserWorkspaceRoles } from "@plane/types";
import { CustomSearchSelect, CustomSelect, Loader } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { useProject } from "@/hooks/store/use-project";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

/** Project roles an area can grant, using Plane's own role values. */
const INHERITED_ROLES: { value: number; label: string; hint: string }[] = [
  { value: EUserWorkspaceRoles.ADMIN, label: "Admin", hint: "Can change project settings and members" },
  { value: EUserWorkspaceRoles.MEMBER, label: "Member", hint: "Can create and edit work items" },
  { value: EUserWorkspaceRoles.GUEST, label: "Guest", hint: "Limited access" },
];

/**
 * @description Which projects an area grants access to, and at which role.
 * Linking a project immediately gives every member of the area that role on it;
 * unlinking withdraws only the access this area granted.
 */
export const OrganizationalUnitProjectsTab = observer(function OrganizationalUnitProjectsTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { workspaceProjectIds, getProjectById } = useProject();

  const [isLoading, setIsLoading] = useState(true);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState<number>(EUserWorkspaceRoles.MEMBER);
  const [isLinking, setIsLinking] = useState(false);

  const linkedProjects = store.getProjectsByUnitId(unitId);

  useEffect(() => {
    setIsLoading(true);
    store.fetchProjects(workspaceSlug, unitId).finally(() => setIsLoading(false));
  }, [workspaceSlug, unitId, store]);

  const linkableOptions = useMemo(() => {
    const alreadyLinked = new Set(linkedProjects.map((link) => link.project));
    return (workspaceProjectIds ?? [])
      .filter((projectId) => !alreadyLinked.has(projectId))
      .map((projectId) => {
        const project = getProjectById(projectId);
        return {
          value: projectId,
          query: project?.name ?? "",
          content: (
            <div className="flex items-center gap-2">
              <span className="text-xs text-custom-text-300">{project?.identifier}</span>
              <span className="truncate">{project?.name}</span>
            </div>
          ),
        };
      });
  }, [linkedProjects, workspaceProjectIds, getProjectById]);

  const handleLink = async () => {
    if (!selectedProjectId) return;
    setIsLinking(true);
    try {
      await store.linkProject(workspaceSlug, unitId, selectedProjectId, selectedRole);
      setSelectedProjectId(null);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Project linked",
        message: "Everyone in this area now has access to it.",
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not linked", message: "Try again in a moment." });
    } finally {
      setIsLinking(false);
    }
  };

  const handleRoleChange = async (linkId: string, role: number) => {
    try {
      await store.updateLinkedProjectRole(workspaceSlug, unitId, linkId, role);
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Role unchanged", message: "Try again in a moment." });
    }
  };

  const handleUnlink = async (linkId: string, projectName: string) => {
    try {
      await store.unlinkProject(workspaceSlug, unitId, linkId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Project unlinked",
        message: `Access to ${projectName} granted outside this area is kept.`,
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not unlinked", message: "Try again in a moment." });
    }
  };

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
      </Loader>
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <CustomSearchSelect
          value={selectedProjectId}
          options={linkableOptions}
          onChange={(value: string) => setSelectedProjectId(value)}
          label={
            selectedProjectId ? (getProjectById(selectedProjectId)?.name ?? "Select a project") : "Select a project"
          }
          maxHeight="md"
          noResultsMessage="Every project is already linked to this area"
        />
        <CustomSelect
          value={selectedRole}
          label={INHERITED_ROLES.find((role) => role.value === selectedRole)?.label ?? "Member"}
          onChange={(value: number) => setSelectedRole(value)}
        >
          {INHERITED_ROLES.map((role) => (
            <CustomSelect.Option key={role.value} value={role.value}>
              <div className="flex flex-col">
                <span>{role.label}</span>
                <span className="text-xs text-custom-text-300">{role.hint}</span>
              </div>
            </CustomSelect.Option>
          ))}
        </CustomSelect>
        <Button variant="primary" size="sm" onClick={handleLink} loading={isLinking} disabled={!selectedProjectId}>
          Link project
        </Button>
      </div>

      {linkedProjects.length === 0 ? (
        <p className="text-sm text-custom-text-300 py-8 text-center">
          No projects linked yet. Link one to give this area&apos;s members access to it.
        </p>
      ) : (
        <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
          {linkedProjects.map((link) => (
            <div key={link.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="flex min-w-0 items-center gap-2">
                <span className="bg-custom-background-80 text-xs text-custom-text-300 flex-shrink-0 rounded px-1.5 py-0.5">
                  {link.project_identifier}
                </span>
                <span className="text-sm text-custom-text-100 truncate">{link.project_name}</span>
              </div>
              <div className="flex flex-shrink-0 items-center gap-2">
                <CustomSelect
                  value={link.default_role}
                  label={INHERITED_ROLES.find((role) => role.value === link.default_role)?.label ?? "Member"}
                  onChange={(value: number) => handleRoleChange(link.id, value)}
                  buttonClassName="text-xs"
                >
                  {INHERITED_ROLES.map((role) => (
                    <CustomSelect.Option key={role.value} value={role.value}>
                      {role.label}
                    </CustomSelect.Option>
                  ))}
                </CustomSelect>
                <button
                  type="button"
                  aria-label={`Unlink ${link.project_name} from this area`}
                  className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 rounded p-1 outline-none focus-visible:ring-2"
                  onClick={() => handleUnlink(link.id, link.project_name)}
                >
                  <X className="size-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-custom-text-300">
        A person&apos;s role is the highest their areas grant, capped by their workspace role. Roles set by hand on a
        project are never lowered here.
      </p>
    </div>
  );
});
