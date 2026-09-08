/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { TAssignmentMode } from "@plane/types";
import { Checkbox, CustomSelect, Input, Loader } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const OU = "workspace_settings.settings.organizational_units";

const MODES: TAssignmentMode[] = ["manual", "self_claim", "least_loaded", "explicit"];

/**
 * @description How this area hands work out, and a per-project override of
 * that default. Admin-only on the write path — a coordinator operates the
 * queue, they do not rewrite who work lands on.
 */
export const OrganizationalUnitPolicyForm = observer(function OrganizationalUnitPolicyForm(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [projectId, setProjectId] = useState<string | "area">("area");
  const [defaultMode, setDefaultMode] = useState<TAssignmentMode>("manual");
  const [allowedModes, setAllowedModes] = useState<TAssignmentMode[]>(["manual"]);
  const [sla, setSla] = useState("");
  const [maxOpen, setMaxOpen] = useState("");

  const projects = store.getProjectsByUnitId(unitId);
  const scopeProjectId = projectId === "area" ? undefined : projectId;

  useEffect(() => {
    store.fetchProjects(workspaceSlug, unitId).catch(() => undefined);
  }, [workspaceSlug, unitId, store]);

  useEffect(() => {
    setIsLoading(true);
    store
      .fetchPolicy(workspaceSlug, unitId, scopeProjectId)
      .then((resolution) => {
        const mode = (resolution.effective_mode ?? "manual") as TAssignmentMode;
        setDefaultMode(mode);
        const allowed = (resolution.allowed_modes ?? [mode]).filter((entry): entry is TAssignmentMode =>
          MODES.includes(entry as TAssignmentMode)
        );
        setAllowedModes(allowed.length > 0 ? allowed : [mode]);
        setSla(resolution.assignment_sla_seconds != null ? String(resolution.assignment_sla_seconds) : "");
        setMaxOpen(resolution.max_open_items_per_member != null ? String(resolution.max_open_items_per_member) : "");
        return undefined;
      })
      .catch(() => undefined)
      .finally(() => setIsLoading(false));
  }, [workspaceSlug, unitId, scopeProjectId, store]);

  const toggleMode = (mode: TAssignmentMode) => {
    setAllowedModes((current) => {
      if (current.includes(mode)) {
        const next = current.filter((entry) => entry !== mode);
        return next.length > 0 ? next : current;
      }
      return [...current, mode];
    });
  };

  const handleSave = async () => {
    const modes = allowedModes.includes(defaultMode) ? allowedModes : [...allowedModes, defaultMode];
    setIsSaving(true);
    try {
      await store.updatePolicy(
        workspaceSlug,
        unitId,
        {
          default_mode: defaultMode,
          allowed_modes: modes,
          assignment_sla_seconds: sla === "" ? null : Number(sla),
          max_open_items_per_member: maxOpen === "" ? null : Number(maxOpen),
        },
        scopeProjectId
      );
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.policy.toast.saved_title`),
        message: t(`${OU}.policy.toast.saved`),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.policy.toast.not_saved`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const modeLabel = (mode: TAssignmentMode) => t(`${OU}.policy.mode.${mode}`);

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
      </Loader>
    );

  return (
    <div className="flex max-w-xl flex-col gap-5">
      <div>
        <h4 className="text-sm text-custom-text-200 font-medium">{t(`${OU}.policy.heading`)}</h4>
        <p className="text-xs text-custom-text-300 mt-1">{t(`${OU}.policy.description`)}</p>
      </div>

      <CustomSelect
        value={projectId}
        label={
          projectId === "area"
            ? t(`${OU}.policy.scope_area`)
            : t(`${OU}.policy.scope_project`, {
                name: projects.find((project) => project.project === projectId)?.project_name ?? "",
              })
        }
        onChange={(value: string) => setProjectId(value as string | "area")}
      >
        <CustomSelect.Option value="area">{t(`${OU}.policy.scope_area`)}</CustomSelect.Option>
        {projects.map((project) => (
          <CustomSelect.Option key={project.id} value={project.project}>
            {t(`${OU}.policy.scope_project`, { name: project.project_name })}
          </CustomSelect.Option>
        ))}
      </CustomSelect>

      <div className="flex flex-col gap-1">
        <span className="text-xs text-custom-text-300">{t(`${OU}.policy.default_mode`)}</span>
        <CustomSelect
          value={defaultMode}
          label={modeLabel(defaultMode)}
          onChange={(value: TAssignmentMode) => setDefaultMode(value)}
        >
          {MODES.map((mode) => (
            <CustomSelect.Option key={mode} value={mode}>
              {modeLabel(mode)}
            </CustomSelect.Option>
          ))}
        </CustomSelect>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-xs text-custom-text-300">{t(`${OU}.policy.allowed_modes`)}</legend>
        {MODES.map((mode) => (
          <label key={mode} className="text-sm text-custom-text-200 flex items-center gap-2">
            <Checkbox checked={allowedModes.includes(mode)} onChange={() => toggleMode(mode)} />
            {modeLabel(mode)}
          </label>
        ))}
      </fieldset>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-custom-text-300">{t(`${OU}.policy.sla_seconds`)}</span>
        <Input type="number" min={0} value={sla} onChange={(event) => setSla(event.target.value)} />
        <span className="text-xs text-custom-text-400">{t(`${OU}.policy.sla_hint`)}</span>
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-custom-text-300">{t(`${OU}.policy.max_open`)}</span>
        <Input type="number" min={0} value={maxOpen} onChange={(event) => setMaxOpen(event.target.value)} />
        <span className="text-xs text-custom-text-400">{t(`${OU}.policy.max_open_hint`)}</span>
      </label>

      <div>
        <Button variant="primary" size="sm" onClick={handleSave} loading={isSaving}>
          {t(`${OU}.policy.save`)}
        </Button>
      </div>
    </div>
  );
});
