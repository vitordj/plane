/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
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

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Which projects an area grants access to, and at which role.
 * Linking a project immediately gives every member of the area that role on it;
 * unlinking withdraws only the access this area granted.
 */
export const OrganizationalUnitProjectsTab = observer(function OrganizationalUnitProjectsTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const { workspaceProjectIds, getProjectById } = useProject();

  const [isLoading, setIsLoading] = useState(true);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState<number>(EUserWorkspaceRoles.MEMBER);
  const [isLinking, setIsLinking] = useState(false);

  const linkedProjects = store.getProjectsByUnitId(unitId);

  /** Project roles an area can grant, using Plane's own role values. */
  const inheritedRoles: { value: number; label: string; hint: string }[] = [
    {
      value: EUserWorkspaceRoles.ADMIN,
      label: t("role_details.admin.title"),
      hint: t(`${OU}.projects.role_admin_hint`),
    },
    {
      value: EUserWorkspaceRoles.MEMBER,
      label: t("role_details.member.title"),
      hint: t(`${OU}.projects.role_member_hint`),
    },
    {
      value: EUserWorkspaceRoles.GUEST,
      label: t("role_details.guest.title"),
      hint: t(`${OU}.projects.role_guest_hint`),
    },
  ];
  const roleLabel = (role: number) =>
    inheritedRoles.find((option) => option.value === role)?.label ?? t("role_details.member.title");

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
        title: t(`${OU}.projects.toast.linked_title`),
        message: t(`${OU}.projects.toast.linked`),
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.projects.toast.not_linked`), message: t(`${OU}.try_again`) });
    } finally {
      setIsLinking(false);
    }
  };

  const handleRoleChange = async (linkId: string, role: number) => {
    try {
      await store.updateLinkedProjectRole(workspaceSlug, unitId, linkId, role);
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.toast.role_unchanged`), message: t(`${OU}.try_again`) });
    }
  };

  const handleUnlink = async (linkId: string, projectName: string) => {
    try {
      await store.unlinkProject(workspaceSlug, unitId, linkId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.projects.toast.unlinked_title`),
        message: t(`${OU}.projects.toast.unlinked`, { name: projectName }),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.projects.toast.not_unlinked`),
        message: t(`${OU}.try_again`),
      });
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
            selectedProjectId
              ? (getProjectById(selectedProjectId)?.name ?? t(`${OU}.projects.select_project`))
              : t(`${OU}.projects.select_project`)
          }
          maxHeight="md"
          noResultsMessage={t(`${OU}.projects.no_linkable`)}
        />
        <CustomSelect
          value={selectedRole}
          label={roleLabel(selectedRole)}
          onChange={(value: number) => setSelectedRole(value)}
        >
          {inheritedRoles.map((role) => (
            <CustomSelect.Option key={role.value} value={role.value}>
              <div className="flex flex-col">
                <span>{role.label}</span>
                <span className="text-xs text-custom-text-300">{role.hint}</span>
              </div>
            </CustomSelect.Option>
          ))}
        </CustomSelect>
        <Button variant="primary" size="sm" onClick={handleLink} loading={isLinking} disabled={!selectedProjectId}>
          {t(`${OU}.projects.link`)}
        </Button>
      </div>

      {linkedProjects.length === 0 ? (
        <p className="text-sm text-custom-text-300 py-8 text-center">{t(`${OU}.projects.empty`)}</p>
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
                  label={roleLabel(link.default_role)}
                  onChange={(value: number) => handleRoleChange(link.id, value)}
                  buttonClassName="text-xs"
                >
                  {inheritedRoles.map((role) => (
                    <CustomSelect.Option key={role.value} value={role.value}>
                      {role.label}
                    </CustomSelect.Option>
                  ))}
                </CustomSelect>
                <button
                  type="button"
                  aria-label={t(`${OU}.projects.unlink_aria`, { name: link.project_name })}
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

      <p className="text-xs text-custom-text-300">{t(`${OU}.projects.role_note`)}</p>
    </div>
  );
});
