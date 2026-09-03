/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { CalendarOff, X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { TAvailabilityReason } from "@plane/types";
import { CustomSelect, Loader } from "@plane/ui";
import { renderFormattedDate } from "@plane/utils";
// components
import { DateDropdown } from "@/components/dropdowns/date";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  /** The `WorkspaceMember` id the absences are about. */
  workspaceMemberId: string;
  /** True when the person is editing their own — it picks the endpoint. */
  forSelf?: boolean;
  /** False for a read-only view of somebody else's. */
  canEdit?: boolean;
};

const OU = "workspace_settings.settings.organizational_units";
const REASONS: TAvailabilityReason[] = ["vacation", "leave", "other"];

/**
 * @description When somebody is away. Recorded as intervals rather than a
 * single "available" flag, because the useful question is "will they be back
 * before this is due?" and a flag cannot answer it.
 *
 * Overlapping intervals are kept as they are entered: "away all week, and the
 * medical leave inside it" is two facts, and merging them into one would lose
 * the reason for each.
 */
export const AvailabilityForm = observer(function AvailabilityForm(props: Props) {
  const { workspaceSlug, workspaceMemberId, forSelf = false, canEdit = true } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [from, setFrom] = useState<Date | null>(null);
  const [until, setUntil] = useState<Date | null>(null);
  const [reason, setReason] = useState<TAvailabilityReason>("vacation");

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    const load = forSelf
      ? store.fetchMyAvailability(workspaceSlug, workspaceMemberId)
      : store.fetchMemberAvailability(workspaceSlug, workspaceMemberId);
    load
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, workspaceMemberId, forSelf, store]);

  const rows = store.getAvailabilityByMemberId(workspaceMemberId);

  const handleAdd = async () => {
    if (!from) return;
    setIsSaving(true);
    try {
      await store.addAvailability(
        workspaceSlug,
        workspaceMemberId,
        {
          unavailable_from: from.toISOString(),
          unavailable_until: until ? until.toISOString() : null,
          reason,
        },
        forSelf
      );
      setFrom(null);
      setUntil(null);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.availability.added`) });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.availability.not_added`) });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemove = async (availabilityId: string) => {
    try {
      await store.removeAvailability(workspaceSlug, workspaceMemberId, availabilityId, forSelf);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.availability.removed`) });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.availability.not_removed`) });
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <h4 className="text-body-xs-medium text-custom-text-200 flex items-center gap-1.5">
          <CalendarOff className="size-3.5" />
          {t(`${OU}.availability.title`)}
        </h4>
        <p className="text-custom-text-400 text-body-2xs-regular mt-0.5">{t(`${OU}.availability.help`)}</p>
      </div>

      {isLoading ? (
        <Loader className="space-y-2">
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
        </Loader>
      ) : (
        <div className="space-y-1">
          {rows.length === 0 && (
            <p className="text-custom-text-400 text-body-xs-regular py-3">{t(`${OU}.availability.empty`)}</p>
          )}
          {rows.map((row) => (
            <div
              key={row.id}
              className="border-custom-border-200 flex items-center gap-2 rounded border px-2 py-1.5"
            >
              <span className="text-body-xs-regular text-custom-text-100 flex-1 truncate">
                {row.unavailable_until
                  ? t(`${OU}.availability.range`, {
                      from: renderFormattedDate(row.unavailable_from),
                      until: renderFormattedDate(row.unavailable_until),
                    })
                  : t(`${OU}.availability.open_ended`, { from: renderFormattedDate(row.unavailable_from) })}
              </span>
              <span className="text-custom-text-400 text-body-2xs-regular">
                {t(`${OU}.availability.reason.${row.reason}`)}
              </span>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => handleRemove(row.id)}
                  aria-label={t(`${OU}.availability.remove_aria`)}
                  className="text-custom-text-400 hover:text-danger-primary"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {canEdit && (
        <div className="flex flex-wrap items-center gap-2">
          <DateDropdown
            value={from}
            onChange={setFrom}
            buttonVariant="border-with-text"
            placeholder={t(`${OU}.availability.from`)}
          />
          <DateDropdown
            value={until}
            onChange={setUntil}
            minDate={from ?? undefined}
            buttonVariant="border-with-text"
            placeholder={t(`${OU}.availability.until`)}
          />
          <CustomSelect
            value={reason}
            onChange={setReason}
            label={t(`${OU}.availability.reason.${reason}`)}
            buttonClassName="text-body-xs-regular"
          >
            {REASONS.map((option) => (
              <CustomSelect.Option key={option} value={option}>
                {t(`${OU}.availability.reason.${option}`)}
              </CustomSelect.Option>
            ))}
          </CustomSelect>
          <Button variant="secondary" size="sm" onClick={handleAdd} disabled={!from} loading={isSaving}>
            {t(`${OU}.availability.add`)}
          </Button>
        </div>
      )}
    </div>
  );
});
