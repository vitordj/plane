/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { RefreshCw } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Tooltip } from "@plane/propel/tooltip";
import type {
  IExecutiveDrilldown,
  IExecutiveMetrics,
  IExecutiveUnitRow,
  TExecutiveMetric,
  TExecutivePeriod,
} from "@plane/types";
import { Loader } from "@plane/ui";
// services
import { OrganizationalUnitService } from "@/services/orca/organizational-unit.service";
// components
import { ExecutiveDrilldown } from "./executive-drilldown";

const service = new OrganizationalUnitService();

const OU = "workspace_settings.settings.organizational_units";

const PERIODS: TExecutivePeriod[] = ["7d", "30d", "90d"];

/** Which columns open a list of rows, and which are populations without one. */
const DRILLABLE: Record<string, TExecutiveMetric> = {
  backlog: "backlog",
  queued: "queued",
  assignment_overdue: "assignment_overdue",
  target_overdue: "target_overdue",
  assigned_open: "assigned_open",
  throughput: "throughput",
};

type Props = {
  workspaceSlug: string;
};

/**
 * @description Every area of the workspace side by side (item 5.3).
 *
 * The page is a table on purpose. A director comparing six areas is comparing
 * numbers, and a wall of cards makes that harder rather than easier; the one
 * graphic here is a bar under throughput, which is a comparison between the
 * areas on screen and not a series over time. A real sparkline needs a daily
 * history nothing stores yet — that is item 5.2, and inventing a series from a
 * single number would be drawing a trend that does not exist.
 *
 * Every number that has rows behind it opens them. A number nobody can open is
 * a number nobody can check.
 */
export const ExecutiveTable = observer(function ExecutiveTable(props: Props) {
  const { workspaceSlug } = props;
  const { t } = useTranslation();

  const [period, setPeriod] = useState<TExecutivePeriod>("30d");
  const [metrics, setMetrics] = useState<IExecutiveMetrics | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [drilldown, setDrilldown] = useState<{ unit: IExecutiveUnitRow; metric: TExecutiveMetric } | null>(null);
  const [drilldownData, setDrilldownData] = useState<IExecutiveDrilldown | null>(null);
  const [isDrilling, setIsDrilling] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      setIsLoading(true);
      try {
        setMetrics(await service.getExecutiveMetrics(workspaceSlug, { period, refresh }));
      } finally {
        setIsLoading(false);
      }
    },
    [workspaceSlug, period]
  );

  useEffect(() => {
    void load();
    // Changing the period changes the population, so the open drill-down is
    // about a number that no longer exists.
    setDrilldown(null);
    setDrilldownData(null);
  }, [load]);

  useEffect(() => {
    if (!drilldown) return;
    let cancelled = false;
    const run = async () => {
      setIsDrilling(true);
      try {
        const data = await service.getExecutiveDrilldown(workspaceSlug, {
          unit: drilldown.unit.unit.id,
          metric: drilldown.metric,
          period,
        });
        if (!cancelled) setDrilldownData(data);
      } finally {
        if (!cancelled) setIsDrilling(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [drilldown, workspaceSlug, period]);

  const maxThroughput = useMemo(() => Math.max(1, ...(metrics?.units ?? []).map((row) => row.throughput)), [metrics]);

  const noValue = t(`${OU}.executive.no_value`);

  /** Seconds as something readable at a glance. `null` stays "—". */
  const duration = (seconds: number | null) => {
    if (seconds === null || seconds === undefined) return noValue;
    if (seconds < 3600) return t(`${OU}.executive.duration.minutes`, { value: Math.max(1, Math.round(seconds / 60)) });
    if (seconds < 86400) return t(`${OU}.executive.duration.hours`, { value: Math.round(seconds / 3600) });
    return t(`${OU}.executive.duration.days`, { value: Math.round(seconds / 86400) });
  };

  const share = (value: number | null) => (value === null ? noValue : `${Math.round(value * 100)}%`);

  const openDrilldown = (row: IExecutiveUnitRow, column: string) => {
    const metric = DRILLABLE[column];
    if (!metric) return;
    setDrilldownData(null);
    setDrilldown({ unit: row, metric });
  };

  const numberCell = (row: IExecutiveUnitRow, column: keyof IExecutiveUnitRow, emphasis = false) => {
    const value = row[column] as number;
    const metric = DRILLABLE[column as string];
    const className = `text-sm ${emphasis && value > 0 ? "text-red-500 font-medium" : "text-custom-text-200"}`;
    if (!metric) return <span className={className}>{value}</span>;
    return (
      <button
        type="button"
        className={`${className} hover:text-custom-primary-100 underline-offset-2 hover:underline`}
        onClick={() => openDrilldown(row, column as string)}
      >
        {value}
      </button>
    );
  };

  if (isLoading && !metrics)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
      </Loader>
    );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          {PERIODS.map((option) => (
            <button
              key={option}
              type="button"
              className={`text-xs rounded px-2 py-1 ${
                option === period
                  ? "bg-custom-primary-100 text-white"
                  : "text-custom-text-300 hover:bg-custom-background-80"
              }`}
              onClick={() => setPeriod(option)}
            >
              {t(`${OU}.executive.period.${option}`)}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          {metrics && (
            // Shown because the answer is cached for five minutes: a reader who
            // cannot tell how old a number is reads a stale one as news.
            <span className="text-xs text-custom-text-400">
              {t(`${OU}.executive.generated_at`, {
                time: new Date(metrics.generated_at).toLocaleTimeString(),
              })}
            </span>
          )}
          <Button variant="secondary" size="sm" prependIcon={<RefreshCw />} onClick={() => void load(true)}>
            {t(`${OU}.executive.refresh`)}
          </Button>
        </div>
      </div>

      {!metrics || metrics.units.length === 0 ? (
        <p className="text-sm text-custom-text-300 border-custom-border-200 rounded border border-dashed px-4 py-8 text-center">
          {t(`${OU}.executive.empty`)}
        </p>
      ) : (
        <div className="border-custom-border-200 overflow-x-auto rounded border">
          <table className="w-full min-w-[52rem] text-left">
            <thead className="bg-custom-background-90 text-xs text-custom-text-300">
              <tr>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.area`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.backlog`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.queued`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.assignment_overdue`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.target_overdue`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.queue_age`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.throughput`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.cycle_time`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.concentration`)}</th>
                <th className="px-3 py-2 font-medium">{t(`${OU}.executive.columns.kept_ratio`)}</th>
              </tr>
            </thead>
            <tbody className="divide-custom-border-200 divide-y">
              {metrics.units.map((row) => (
                <tr key={row.unit.id}>
                  <td className="px-3 py-2">
                    <span className="text-sm text-custom-text-100">{row.unit.name}</span>
                  </td>
                  <td className="px-3 py-2">{numberCell(row, "backlog")}</td>
                  <td className="px-3 py-2">{numberCell(row, "queued")}</td>
                  <td className="px-3 py-2">{numberCell(row, "assignment_overdue", true)}</td>
                  <td className="px-3 py-2">{numberCell(row, "target_overdue", true)}</td>
                  <td className="px-3 py-2">
                    <Tooltip tooltipContent={t(`${OU}.executive.tooltips.queue_age`)}>
                      <span className="text-sm text-custom-text-200">
                        {duration(row.queue_age_p50)} / {duration(row.queue_age_p90)}
                      </span>
                    </Tooltip>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-col gap-1">
                      {numberCell(row, "throughput")}
                      {/* A comparison between the areas on screen, not a trend:
                          the bar is this area's throughput against the busiest
                          one in the same period. */}
                      <span className="bg-custom-background-80 block h-1 w-16 rounded">
                        <span
                          className="bg-custom-primary-100 block h-1 rounded"
                          style={{ width: `${Math.round((row.throughput / maxThroughput) * 100)}%` }}
                        />
                      </span>
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <Tooltip tooltipContent={t(`${OU}.executive.tooltips.cycle_time`)}>
                      <span className="text-sm text-custom-text-200">
                        {duration(row.cycle_time_p50)} / {duration(row.cycle_time_p90)}
                      </span>
                    </Tooltip>
                  </td>
                  <td className="px-3 py-2">
                    <Tooltip tooltipContent={t(`${OU}.executive.tooltips.concentration`)}>
                      <span className="text-sm text-custom-text-200">{share(row.concentration_top3)}</span>
                    </Tooltip>
                  </td>
                  <td className="px-3 py-2">
                    <Tooltip tooltipContent={t(`${OU}.executive.tooltips.kept_ratio`)}>
                      <span className="text-sm text-custom-text-200">{share(row.auto_assign_kept_ratio)}</span>
                    </Tooltip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {drilldown && (
        <ExecutiveDrilldown
          workspaceSlug={workspaceSlug}
          title={t(`${OU}.executive.drilldown.title`, {
            metric: t(`${OU}.executive.columns.${drilldown.metric}`),
            unit: drilldown.unit.unit.name,
          })}
          isLoading={isDrilling}
          data={drilldownData}
          onClose={() => {
            setDrilldown(null);
            setDrilldownData(null);
          }}
        />
      )}

      {metrics && (
        <section className="flex flex-col gap-2">
          <h4 className="text-sm text-custom-text-200 font-medium">{t(`${OU}.executive.processes.title`)}</h4>
          <div className="border-custom-border-200 flex flex-wrap gap-6 rounded border p-4">
            <div>
              <p className="text-xs text-custom-text-400">{t(`${OU}.executive.processes.running`)}</p>
              <p className="text-lg text-custom-text-100">{metrics.processes.running}</p>
            </div>
            <div>
              <p className="text-xs text-custom-text-400">{t(`${OU}.executive.processes.completed`)}</p>
              <p className="text-lg text-custom-text-100">{metrics.processes.completed}</p>
            </div>
            <div>
              <p className="text-xs text-custom-text-400">{t(`${OU}.executive.processes.lead_time`)}</p>
              <p className="text-lg text-custom-text-100">
                {duration(metrics.processes.lead_time_p50)} / {duration(metrics.processes.lead_time_p90)}
              </p>
            </div>
            <div className="min-w-[12rem]">
              <p className="text-xs text-custom-text-400">{t(`${OU}.executive.processes.late_steps`)}</p>
              {metrics.processes.late_steps.length === 0 ? (
                <p className="text-sm text-custom-text-300">{t(`${OU}.executive.processes.no_late_steps`)}</p>
              ) : (
                <ul className="flex flex-col">
                  {metrics.processes.late_steps.map((step) => (
                    <li key={`${step.template_name}-${step.step_key}`} className="text-sm text-custom-text-200">
                      {step.template_name ? `${step.template_name} · ` : ""}
                      {step.step_key} ({step.late_count})
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>
      )}

      <p className="text-xs text-custom-text-400">{t(`${OU}.executive.definitions_note`)}</p>
    </div>
  );
});
