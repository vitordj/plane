/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { CalendarOff, X } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { TAvailabilityReason } from "@plane/types";
import { CustomSelect, Loader } from "@plane/ui";
// components
import { DateDropdown } from "@/components/dropdowns/date";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
// local
import { formatLocalDate } from "./format-local-date";

type Props = {
  workspaceSlug: string;
  /** The `WorkspaceMember` id the absences are about. `"me"` keys the store when it is unknown. */
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
  const { t, currentLocale } = useTranslation();

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
      // R2.23: normalize `from` to local start-of-day and `until` to local
      // end-of-day so single-day absences (from === until) satisfy the server's
      // strict `unavailable_until > unavailable_from` check and cover the full
      // chosen end day.
      const normalizedFrom = new Date(from);
      normalizedFrom.setHours(0, 0, 0, 0);
      let normalizedUntil: Date | null = null;
      if (until) {
        normalizedUntil = new Date(until);
        normalizedUntil.setHours(23, 59, 59, 999);
      }

      await store.addAvailability(
        workspaceSlug,
        workspaceMemberId,
        {
          unavailable_from: normalizedFrom.toISOString(),
          unavailable_until: normalizedUntil ? normalizedUntil.toISOString() : null,
          reason,
        },
        forSelf
      );
      setFrom(null);
      setUntil(null);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.availability.added`) });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.availability.not_added`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemove = async (availabilityId: string) => {
    try {
      await store.removeAvailability(workspaceSlug, workspaceMemberId, availabilityId, forSelf);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.availability.removed`) });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.availability.not_removed`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <h4 className="text-sm text-custom-text-200 flex items-center gap-1.5 font-medium">
          <CalendarOff className="size-3.5" />
          {t(`${OU}.availability.title`)}
        </h4>
        <p className="text-custom-text-400 text-xs mt-0.5">{t(`${OU}.availability.help`)}</p>
      </div>

      {isLoading ? (
        <Loader className="space-y-2">
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
        </Loader>
      ) : (
        <div className="space-y-1">
          {rows.length === 0 && <p className="text-custom-text-400 text-xs py-3">{t(`${OU}.availability.empty`)}</p>}
          {rows.map((row) => (
            <div key={row.id} className="border-custom-border-200 flex items-center gap-2 rounded border px-2 py-1.5">
              <span className="text-custom-text-100 text-xs flex-1 truncate">
                {row.unavailable_until
                  ? t(`${OU}.availability.range`, {
                      from: formatLocalDate(row.unavailable_from, currentLocale),
                      until: formatLocalDate(row.unavailable_until, currentLocale),
                    })
                  : t(`${OU}.availability.open_ended`, {
                      from: formatLocalDate(row.unavailable_from, currentLocale),
                    })}
              </span>
              <span className="text-custom-text-400 text-[11px]">{t(`${OU}.availability.reason.${row.reason}`)}</span>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => handleRemove(row.id)}
                  aria-label={t(`${OU}.availability.remove_aria`)}
                  className="text-custom-text-400 hover:text-custom-text-100"
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
            maxDate={until ?? undefined}
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
            buttonClassName="text-xs"
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
