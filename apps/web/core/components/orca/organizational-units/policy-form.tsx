/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
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
  /** Set to scope the policy to one project instead of the whole area. */
  projectId?: string;
};

const OU = "workspace_settings.settings.organizational_units";
const MODES: TAssignmentMode[] = ["manual", "self_claim", "least_loaded"];

/**
 * @description How an area assigns work. Admin only: this decides where every
 * future work item in the area lands, which is a workspace-shaped decision
 * rather than a coordinator-shaped one.
 *
 * The allowed list is what an automation may ask for. Leaving a mode out is
 * not a suggestion — a request for it is refused, never quietly downgraded.
 */
export const AssignmentPolicyForm = observer(function AssignmentPolicyForm(props: Props) {
  const { workspaceSlug, unitId, projectId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [defaultMode, setDefaultMode] = useState<TAssignmentMode>("manual");
  const [allowedModes, setAllowedModes] = useState<TAssignmentMode[]>(["manual"]);
  const [slaMinutes, setSlaMinutes] = useState<string>("");
  const [maxOpen, setMaxOpen] = useState<string>("");
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    store.service
      .getResolvedPolicy(workspaceSlug, unitId, projectId)
      .then((resolved) => {
        if (cancelled || !resolved) return;
        setDefaultMode(resolved.effective_mode);
        setAllowedModes(resolved.policy?.allowed_modes ?? [resolved.effective_mode]);
        setSlaMinutes(
          resolved.assignment_sla_seconds ? String(Math.round(resolved.assignment_sla_seconds / 60)) : ""
        );
        setMaxOpen(resolved.policy?.max_open_items_per_member ? String(resolved.policy.max_open_items_per_member) : "");
      })
      .catch(() => undefined);
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
    // Mirrors the server's own rule, so the refusal arrives before the round
    // trip rather than as a toast the person has to decode.
    if (!allowedModes.includes(defaultMode)) {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.policy.default_must_be_allowed`) });
      return;
    }

    setIsSaving(true);
    try {
      await store.service.setPolicy(
        workspaceSlug,
        unitId,
        {
          default_mode: defaultMode,
          allowed_modes: allowedModes,
          assignment_sla_seconds: slaMinutes ? Number(slaMinutes) * 60 : null,
          max_open_items_per_member: maxOpen ? Number(maxOpen) : null,
        },
        projectId
      );
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.policy.saved`) });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.policy.not_saved`) });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <label className="text-body-xs-medium text-custom-text-200">{t(`${OU}.policy.default_mode`)}</label>
        <CustomSelect
          value={defaultMode}
          onChange={(value: TAssignmentMode) => setDefaultMode(value)}
          label={t(`${OU}.queue.mode.${defaultMode}`)}
        >
          {MODES.map((mode) => (
            <CustomSelect.Option key={mode} value={mode}>
              {t(`${OU}.queue.mode.${mode}`)}
            </CustomSelect.Option>
          ))}
        </CustomSelect>
        <p className="text-custom-text-400 text-body-2xs-regular">{t(`${OU}.policy.default_mode_help`)}</p>
      </div>

      <fieldset className="space-y-1">
        <legend className="text-body-xs-medium text-custom-text-200">{t(`${OU}.policy.allowed_modes`)}</legend>
        <div className="flex flex-wrap gap-3">
          {MODES.map((mode) => (
            <label key={mode} className="text-body-xs-regular text-custom-text-200 flex items-center gap-1.5">
              <input type="checkbox" checked={allowedModes.includes(mode)} onChange={() => toggleAllowed(mode)} />
              {t(`${OU}.queue.mode.${mode}`)}
            </label>
          ))}
        </div>
        <p className="text-custom-text-400 text-body-2xs-regular">{t(`${OU}.policy.allowed_modes_help`)}</p>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <label className="text-body-xs-medium text-custom-text-200">{t(`${OU}.policy.sla_minutes`)}</label>
          <Input
            type="number"
            min={0}
            value={slaMinutes}
            onChange={(event) => setSlaMinutes(event.target.value)}
            placeholder={t(`${OU}.policy.no_limit`)}
          />
          <p className="text-custom-text-400 text-body-2xs-regular">{t(`${OU}.policy.sla_help`)}</p>
        </div>
        <div className="space-y-1">
          <label className="text-body-xs-medium text-custom-text-200">{t(`${OU}.policy.max_open`)}</label>
          <Input
            type="number"
            min={0}
            value={maxOpen}
            onChange={(event) => setMaxOpen(event.target.value)}
            placeholder={t(`${OU}.policy.no_limit`)}
          />
          <p className="text-custom-text-400 text-body-2xs-regular">{t(`${OU}.policy.max_open_help`)}</p>
        </div>
      </div>

      <Button variant="primary" size="sm" loading={isSaving} onClick={handleSave}>
        {t("save")}
      </Button>
    </div>
  );
});
