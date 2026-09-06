/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { X } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IAvailabilityState, TUnavailabilityReason } from "@plane/types";
import { CustomSelect, Input, Loader } from "@plane/ui";
import { renderFormattedDate } from "@plane/utils";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  /**
   * Whose absences these are. Omitted means your own — the common case, and
   * the one that needs no permission beyond being in the workspace.
   */
  workspaceMemberId?: string;
  memberName?: string;
};

const OU = "workspace_settings.settings.organizational_units";

const REASONS: TUnavailabilityReason[] = ["vacation", "leave", "other"];

/**
 * @description When somebody is not taking work, and until when.
 *
 * A window rather than a switch, and that is the whole design: a switch has to
 * be turned back by somebody who remembers, and the one thing everybody
 * forgets after a holiday is the toggle they set before it. An end date left
 * empty means indefinite, which is what long leave looks like and what the
 * ranking treats as away until somebody closes it.
 *
 * The form asks for a reason from three coarse values and offers no free-text
 * note, on purpose: the queue needs to know somebody is away, not why in any
 * detail a colleague could read off a screen.
 */
export const AvailabilityForm = observer(function AvailabilityForm(props: Props) {
  const { workspaceSlug, workspaceMemberId, memberName } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [state, setState] = useState<IAvailabilityState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [reason, setReason] = useState<TUnavailabilityReason>("vacation");

  const load = async () => {
    setIsLoading(true);
    try {
      const response = workspaceMemberId
        ? await store.fetchMemberAvailability(workspaceSlug, workspaceMemberId)
        : await store.fetchMyAvailability(workspaceSlug);
      setState(response);
    } catch {
      // The feature is off, or the reader may not see this person's absences;
      // either way there is nothing to show and nothing to say about it.
      setState(null);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, workspaceMemberId]);

  const handleAdd = async () => {
    if (!from) return;
    setIsSaving(true);
    try {
      const payload = {
        unavailable_from: new Date(from).toISOString(),
        unavailable_until: until ? new Date(until).toISOString() : null,
        reason,
      };
      if (workspaceMemberId) await store.addMemberAvailability(workspaceSlug, workspaceMemberId, payload);
      else await store.addMyAvailability(workspaceSlug, payload);
      setFrom("");
      setUntil("");
      await load();
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.availability.toast.added`) });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.availability.toast.not_added`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemove = async (windowId: string) => {
    try {
      if (workspaceMemberId) await store.removeMemberAvailability(workspaceSlug, workspaceMemberId, windowId);
      else await store.removeMyAvailability(workspaceSlug, windowId);
      await load();
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.availability.toast.removed`) });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.availability.toast.not_removed`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    }
  };

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="36px" />
        <Loader.Item height="36px" />
      </Loader>
    );

  if (state === null) return null;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h4 className="text-sm text-custom-text-200 font-medium">
          {memberName ? t(`${OU}.availability.title_for`, { name: memberName }) : t(`${OU}.availability.title`)}
        </h4>
        <p className="text-xs text-custom-text-300">{t(`${OU}.availability.description`)}</p>
      </div>

      <p className="text-sm text-custom-text-200">
        {state.available ? t(`${OU}.availability.here`) : t(`${OU}.availability.away`)}
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor="orca-availability-from" className="text-xs text-custom-text-300">
            {t(`${OU}.availability.from`)}
          </label>
          <Input
            id="orca-availability-from"
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="orca-availability-until" className="text-xs text-custom-text-300">
            {t(`${OU}.availability.until`)}
          </label>
          <Input
            id="orca-availability-until"
            type="date"
            value={until}
            onChange={(event) => setUntil(event.target.value)}
            placeholder={t(`${OU}.availability.indefinite`)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-custom-text-300">{t(`${OU}.work.reason_label`)}</span>
          <CustomSelect
            value={reason}
            label={t(`${OU}.availability.reason.${reason}`)}
            onChange={(value: TUnavailabilityReason) => setReason(value)}
          >
            {REASONS.map((value) => (
              <CustomSelect.Option key={value} value={value}>
                {t(`${OU}.availability.reason.${value}`)}
              </CustomSelect.Option>
            ))}
          </CustomSelect>
        </div>
        <Button variant="primary" size="sm" onClick={handleAdd} loading={isSaving} disabled={!from}>
          {t(`${OU}.availability.add`)}
        </Button>
      </div>

      {state.windows.length === 0 ? (
        <p className="text-sm text-custom-text-300">{t(`${OU}.availability.empty`)}</p>
      ) : (
        <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
          {state.windows.map((window) => (
            <div key={window.id} className="flex items-center justify-between gap-3 px-4 py-2">
              <p className="text-sm text-custom-text-200">
                {renderFormattedDate(window.unavailable_from)}
                {" → "}
                {window.unavailable_until
                  ? renderFormattedDate(window.unavailable_until)
                  : t(`${OU}.availability.indefinite`)}
                <span className="text-xs text-custom-text-300 ml-2">
                  {t(`${OU}.availability.reason.${window.reason}`)}
                </span>
              </p>
              <button
                type="button"
                aria-label={t(`${OU}.availability.remove_aria`)}
                className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 rounded p-1 outline-none focus-visible:ring-2"
                onClick={() => handleRemove(window.id)}
              >
                <X className="size-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
