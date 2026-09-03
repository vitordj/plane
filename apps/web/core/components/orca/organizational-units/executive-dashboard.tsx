/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { AlertTriangle } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IExecutiveSummary, IExecutiveUnitMetrics, TExecutivePeriod } from "@plane/types";
import { Loader, Tooltip } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  /** Opens the area's queue when a coordinator wants to see the rows behind a number. */
  onOpenUnit?: (unitId: string) => void;
};

const OU = "workspace_settings.settings.organizational_units";
const PERIODS: TExecutivePeriod[] = ["7d", "30d", "90d"];

/** @description Seconds as something a person reads at a glance. */
function duration(seconds: number | null, t: (key: string, values?: Record<string, unknown>) => string) {
  if (seconds === null) return "—";
  if (seconds < 3600) return t(`${OU}.executive.minutes`, { count: Math.max(1, Math.round(seconds / 60)) });
  if (seconds < 86400) return t(`${OU}.executive.hours`, { count: Math.round(seconds / 3600) });
  return t(`${OU}.executive.days`, { count: Math.round(seconds / 86400) });
}

function percent(ratio: number | null) {
  return ratio === null ? "—" : `${Math.round(ratio * 100)}%`;
}

/**
 * @description The areas of a workspace side by side. Admin only, because the
 * aggregate is a different thing from the parts — "which area is drowning" is
 * a management question, and the parts are already visible to the people in
 * each area.
 *
 * Every column has a tooltip with its definition. A number somebody cannot
 * check is a number they argue with instead of acting on, and the first
 * argument is always about what it means.
 */
export const ExecutiveDashboard = observer(function ExecutiveDashboard(props: Props) {
  const { workspaceSlug, onOpenUnit } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [period, setPeriod] = useState<TExecutivePeriod>("30d");
  const [summary, setSummary] = useState<IExecutiveSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    store
      .fetchExecutiveSummary(workspaceSlug, period)
      .then((result) => {
        if (!cancelled) setSummary(result);
      })
      .catch(() => {
        if (!cancelled) setSummary(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, period, store]);

  const columns: { key: string; help: string }[] = [
    { key: "backlog", help: "backlog_help" },
    { key: "queued", help: "queued_help" },
    { key: "assignment_overdue", help: "assignment_overdue_help" },
    { key: "target_overdue", help: "target_overdue_help" },
    { key: "queue_age", help: "queue_age_help" },
    { key: "throughput", help: "throughput_help" },
    { key: "cycle_time", help: "cycle_time_help" },
    { key: "concentration", help: "concentration_help" },
    { key: "auto_kept", help: "auto_kept_help" },
  ];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-body-sm-medium text-custom-text-100">{t(`${OU}.executive.title`)}</h3>
          <p className="text-custom-text-400 text-body-2xs-regular">{t(`${OU}.executive.help`)}</p>
        </div>
        <div className="border-custom-border-200 flex shrink-0 gap-1 rounded border p-0.5" role="tablist">
          {PERIODS.map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={period === option}
              onClick={() => setPeriod(option)}
              className={`text-body-2xs-medium rounded px-2 py-1 ${
                period === option ? "bg-custom-background-80 text-custom-text-100" : "text-custom-text-300"
              }`}
            >
              {t(`${OU}.executive.period.${option}`)}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <Loader className="space-y-2">
          <Loader.Item height="40px" />
          <Loader.Item height="40px" />
          <Loader.Item height="40px" />
        </Loader>
      ) : !summary || summary.units.length === 0 ? (
        <p className="text-custom-text-400 text-body-xs-regular py-10 text-center">
          {t(`${OU}.executive.empty`)}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left">
              <thead>
                <tr className="border-custom-border-200 border-b">
                  <th className="text-custom-text-300 text-body-2xs-medium px-3 py-2">
                    {t(`${OU}.executive.area`)}
                  </th>
                  {columns.map((column) => (
                    <th key={column.key} className="text-custom-text-300 text-body-2xs-medium px-3 py-2">
                      <Tooltip tooltipContent={t(`${OU}.executive.${column.help}`)}>
                        <span>{t(`${OU}.executive.${column.key}`)}</span>
                      </Tooltip>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary.units.map((row) => (
                  <UnitRow key={row.unit.id} row={row} onOpenUnit={onOpenUnit} t={t} />
                ))}
              </tbody>
            </table>
          </div>

          <section className="border-custom-border-200 space-y-2 rounded border p-3">
            <h4 className="text-body-xs-medium text-custom-text-200">{t(`${OU}.executive.processes`)}</h4>
            <div className="text-custom-text-300 text-body-xs-regular flex flex-wrap gap-x-6 gap-y-1">
              <span>{t(`${OU}.executive.running`, { count: summary.processes.running })}</span>
              <span>{t(`${OU}.executive.completed`, { count: summary.processes.completed })}</span>
              <span>
                {t(`${OU}.executive.lead_time`, {
                  p50: duration(summary.processes.lead_time.p50, t),
                  p90: duration(summary.processes.lead_time.p90, t),
                })}
              </span>
            </div>
            {summary.processes.slowest_steps.length > 0 && (
              <div className="text-custom-text-400 text-body-2xs-regular">
                {t(`${OU}.executive.slowest_steps`)}:{" "}
                {summary.processes.slowest_steps
                  .map((step) => `${step.step_key} (${step.waiting})`)
                  .join(" · ")}
              </div>
            )}
          </section>

          <p className="text-custom-text-400 text-body-2xs-regular">
            {t(`${OU}.executive.definitions_note`)}
          </p>
        </>
      )}
    </div>
  );
});

type RowProps = {
  row: IExecutiveUnitMetrics;
  onOpenUnit?: (unitId: string) => void;
  t: (key: string, values?: Record<string, unknown>) => string;
};

const UnitRow = observer(function UnitRow(props: RowProps) {
  const { row, onOpenUnit, t } = props;

  const cell = "text-body-xs-regular text-custom-text-200 px-3 py-2";

  return (
    <tr className="border-custom-border-200 border-b last:border-b-0">
      <td className="px-3 py-2">
        <button
          type="button"
          onClick={() => onOpenUnit?.(row.unit.id)}
          className="text-body-xs-medium text-custom-text-100 hover:text-custom-primary-100 text-left"
        >
          {row.unit.name}
        </button>
      </td>
      <td className={cell}>{row.backlog}</td>
      <td className={cell}>{row.queued}</td>
      <td className={cell}>
        {row.assignment_overdue > 0 ? (
          <span className="text-danger-primary flex items-center gap-1">
            <AlertTriangle className="size-3" />
            {row.assignment_overdue}
          </span>
        ) : (
          row.assignment_overdue
        )}
      </td>
      <td className={cell}>{row.target_overdue}</td>
      <td className={cell}>
        {duration(row.queue_age.p50, t)} / {duration(row.queue_age.p90, t)}
      </td>
      <td className={cell}>{row.throughput}</td>
      <td className={cell}>
        {duration(row.cycle_time.p50, t)} / {duration(row.cycle_time.p90, t)}
      </td>
      <td className={cell}>
        {/* The sample travels with the ratio: 100% of an area of two is not
            the same finding as 100% of an area of twelve. */}
        <Tooltip
          tooltipContent={t(`${OU}.executive.concentration_of`, {
            open: row.concentration_top3.open_items,
            people: row.concentration_top3.executors,
          })}
        >
          <span>{percent(row.concentration_top3.ratio)}</span>
        </Tooltip>
      </td>
      <td className={cell}>
        <Tooltip
          tooltipContent={t(`${OU}.executive.auto_kept_of`, { count: row.auto_assign_kept.decisions })}
        >
          <span>{percent(row.auto_assign_kept.ratio)}</span>
        </Tooltip>
      </td>
    </tr>
  );
});
