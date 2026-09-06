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
import { CustomSelect, Input } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
  /** Present when editing the policy that overrides the area's for one project. */
  projectId?: string;
  projectName?: string;
};

const OU = "workspace_settings.settings.organizational_units";

/** The three modes a policy can hand work out by; `explicit` bypasses policy. */
const MODES: TAssignmentMode[] = ["manual", "self_claim", "least_loaded"];

/**
 * @description How an area hands work out: the default mode, which modes it
 * permits at all, how long an item may wait for a person, and how much open
 * work one person may hold.
 *
 * `allowed_modes` is a fence, not a preference. A mode outside it is refused
 * rather than quietly downgraded (I7), because a caller that asked for
 * automatic allocation and silently got a queue would believe somebody is on
 * the item. That is also why the default must be inside the fence, and why the
 * form refuses to save a combination the server would have to reject.
 */
export const AssignmentPolicyForm = observer(function AssignmentPolicyForm(props: Props) {
  const { workspaceSlug, unitId, projectId, projectName } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [defaultMode, setDefaultMode] = useState<TAssignmentMode>("manual");
  const [allowedModes, setAllowedModes] = useState<TAssignmentMode[]>(["manual"]);
  const [slaHours, setSlaHours] = useState("");
  const [maxOpen, setMaxOpen] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    const load = async () => {
      try {
        const resolution = await store.fetchPolicy(workspaceSlug, unitId, projectId);
        if (cancelled) return;
        // `explicit` is not a policy mode — it bypasses resolution entirely
        // (RFC §6.3) — so the form shows the closest thing a policy can say.
        setDefaultMode(resolution.effective_mode === "explicit" ? "manual" : resolution.effective_mode);
        setAllowedModes(resolution.allowed_modes.filter((mode) => mode !== "explicit"));
        setSlaHours(
          resolution.assignment_sla_seconds ? String(Math.round(resolution.assignment_sla_seconds / 3600)) : ""
        );
        setMaxOpen(resolution.max_open_items_per_member ? String(resolution.max_open_items_per_member) : "");
      } catch {
        // Leave the defaults standing rather than emptying a form somebody
        // may be about to save.
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, unitId, projectId, store]);

  const toggleAllowed = (mode: TAssignmentMode) => {
    setAllowedModes((current) =>
      current.includes(mode) ? current.filter((value) => value !== mode) : [...current, mode]
    );
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await store.writePolicy(
        workspaceSlug,
        unitId,
        {
          default_mode: defaultMode,
          allowed_modes: allowedModes,
          assignment_sla_seconds: slaHours ? Number(slaHours) * 3600 : null,
          max_open_items_per_member: maxOpen ? Number(maxOpen) : null,
        },
        projectId
      );
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.toast.saved`) });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.toast.not_saved`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const isSavable = allowedModes.length > 0 && allowedModes.includes(defaultMode);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h4 className="text-sm text-custom-text-200 font-medium">
          {projectName ? t(`${OU}.policy.project_title`, { project: projectName }) : t(`${OU}.policy.title`)}
        </h4>
        <p className="text-xs text-custom-text-300">{t(`${OU}.policy.description`)}</p>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-sm text-custom-text-200">{t(`${OU}.policy.default_mode`)}</label>
        <CustomSelect
          value={defaultMode}
          label={t(`${OU}.work.decision.mode.${defaultMode}`)}
          onChange={(value: TAssignmentMode) => setDefaultMode(value)}
          disabled={isLoading}
        >
          {MODES.map((mode) => (
            <CustomSelect.Option key={mode} value={mode}>
              {t(`${OU}.work.decision.mode.${mode}`)}
            </CustomSelect.Option>
          ))}
        </CustomSelect>
      </div>

      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm text-custom-text-200">{t(`${OU}.policy.allowed_modes`)}</legend>
        <div className="flex flex-wrap gap-3">
          {MODES.map((mode) => (
            <label key={mode} className="text-sm text-custom-text-300 flex items-center gap-2">
              <input
                type="checkbox"
                checked={allowedModes.includes(mode)}
                onChange={() => toggleAllowed(mode)}
                disabled={isLoading}
              />
              {t(`${OU}.work.decision.mode.${mode}`)}
            </label>
          ))}
        </div>
        {!isSavable && <p className="text-xs text-custom-text-error">{t(`${OU}.policy.default_must_be_allowed`)}</p>}
      </fieldset>

      <div className="flex flex-wrap gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="orca-policy-sla" className="text-sm text-custom-text-200">
            {t(`${OU}.policy.sla_hours`)}
          </label>
          <Input
            id="orca-policy-sla"
            type="number"
            min="1"
            value={slaHours}
            onChange={(event) => setSlaHours(event.target.value)}
            placeholder={t(`${OU}.policy.no_limit`)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="orca-policy-max-open" className="text-sm text-custom-text-200">
            {t(`${OU}.policy.max_open`)}
          </label>
          <Input
            id="orca-policy-max-open"
            type="number"
            min="1"
            value={maxOpen}
            onChange={(event) => setMaxOpen(event.target.value)}
            placeholder={t(`${OU}.policy.no_limit`)}
          />
        </div>
      </div>

      <div className="flex justify-end">
        <Button variant="primary" size="sm" onClick={handleSave} loading={isSaving} disabled={!isSavable || isLoading}>
          {t("save")}
        </Button>
      </div>
    </div>
  );
});
