/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import type { IExecutiveSparklineBar, IExecutiveUnitMetrics, TExecutiveMetric } from "@plane/types";
import { Loader } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
// components
import { QueueList } from "./queue-list";

type Props = {
  workspaceSlug: string;
  onDrillDown: (unitId: string, metric: TExecutiveMetric) => void;
};

const OU = "workspace_settings.settings.organizational_units";

const COUNT_METRICS: TExecutiveMetric[] = ["backlog", "queued", "assignment_overdue", "target_overdue", "throughput"];

/**
 * @description Turns a duration in seconds into the same coarse labels the
 * queue already uses. Null is an em dash: a missing percentile is not zero.
 * @param seconds Duration from the API, or null when the sample is empty.
 * @param t The catalogue lookup the rest of this screen already uses.
 * @returns A short label, or an em dash.
 */
function formatDuration(seconds: number | null, t: (key: string, params?: Record<string, unknown>) => string): string {
  if (seconds == null) return "—";
  if (seconds < 60) return t(`${OU}.work.age.just_now`);
  if (seconds < 3600) return t(`${OU}.work.age.minutes`, { count: Math.floor(seconds / 60) });
  if (seconds < 86400) return t(`${OU}.work.age.hours`, { count: Math.floor(seconds / 3600) });
  return t(`${OU}.work.age.days`, { count: Math.floor(seconds / 86400) });
}

function formatRatio(value: number | null): string {
  if (value == null) return "—";
  return `${Math.round(value * 100)}%`;
}

/**
 * @description CSS bars for daily throughput. The library the product
 * already uses is `@plane/ui`; adding a chart package for a sparkline would
 * be a new dependency the RFC forbids.
 */
function ThroughputSparkline(props: { bars: IExecutiveSparklineBar[] }) {
  const max = Math.max(0, ...props.bars.map((bar) => bar.count));
  return (
    <div className="flex h-6 w-24 items-end gap-px" aria-hidden="true">
      {props.bars.map((bar) => (
        <div
          key={bar.date}
          className="bg-custom-primary-100/80 min-w-px flex-1 rounded-sm"
          style={{ height: `${max === 0 ? 0 : Math.max((bar.count / max) * 100, bar.count > 0 ? 12 : 0)}%` }}
          title={`${bar.date}: ${bar.count}`}
        />
      ))}
    </div>
  );
}

function CountButton(props: {
  value: number;
  metric: TExecutiveMetric;
  unit: IExecutiveUnitMetrics;
  label: string;
  onDrillDown: Props["onDrillDown"];
}) {
  return (
    <button
      type="button"
      aria-label={`${props.unit.name}: ${props.label}`}
      className="text-custom-primary-100 focus-visible:ring-custom-primary-100 rounded tabular-nums outline-none hover:underline focus-visible:ring-2"
      onClick={() => props.onDrillDown(props.unit.unit_id, props.metric)}
    >
      {props.value}
    </button>
  );
}

/**
 * @description The executive table and the process summary. Drill-down is
 * owned by the page so the queue list can sit below the table without the
 * table knowing about routing.
 */
export const OrganizationalUnitExecutiveView = observer(function OrganizationalUnitExecutiveView(props: Props) {
  const { workspaceSlug, onDrillDown } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const report = store.executiveReport;
  const drill = store.executiveDrillDown;

  if (store.executiveLoader && !report) {
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="48px" />
        <Loader.Item height="48px" />
        <Loader.Item height="48px" />
      </Loader>
    );
  }

  if (!report) return <p className="text-sm text-custom-text-300">{t(`${OU}.executive.empty`)}</p>;

  const drillUnit = report.units.find((row) => row.unit_id === drill?.unit_id);

  return (
    <div className="flex flex-col gap-8">
      <div className="overflow-x-auto">
        {report.units.length === 0 ? (
          <p className="text-sm text-custom-text-300">{t(`${OU}.executive.empty`)}</p>
        ) : (
          <table className="text-sm w-full min-w-[64rem] border-collapse text-left">
            <thead>
              <tr className="text-custom-text-300 border-custom-border-200 text-xs border-b">
                <th className="py-2 pr-3 font-medium">{t(`${OU}.executive.indicators.area`)}</th>
                {COUNT_METRICS.map((metric) => (
                  <th key={metric} className="py-2 pr-3 font-medium">
                    {t(`${OU}.executive.indicators.${metric}`)}
                  </th>
                ))}
                <th className="py-2 pr-3 font-medium">{t(`${OU}.executive.indicators.queue_age_p50`)}</th>
                <th className="py-2 pr-3 font-medium">{t(`${OU}.executive.indicators.queue_age_p90`)}</th>
                <th className="py-2 pr-3 font-medium">{t(`${OU}.executive.indicators.cycle_time_p50`)}</th>
                <th className="py-2 pr-3 font-medium">{t(`${OU}.executive.indicators.cycle_time_p90`)}</th>
                <th className="py-2 pr-3 font-medium">{t(`${OU}.executive.indicators.concentration_top3`)}</th>
                <th className="py-2 pr-3 font-medium">{t(`${OU}.executive.indicators.auto_assign_kept_ratio`)}</th>
              </tr>
            </thead>
            <tbody>
              {report.units.map((unit) => (
                <tr key={unit.unit_id} className="border-custom-border-200 border-b">
                  <td className="text-custom-text-100 py-3 pr-3 font-medium">
                    {unit.name}
                    {unit.hidden_count > 0 && (
                      <p className="text-custom-text-400 text-xs font-normal mt-0.5">
                        {t(`${OU}.executive.hidden_count`, { count: unit.hidden_count })}
                      </p>
                    )}
                  </td>
                  {COUNT_METRICS.map((metric) => (
                    <td key={metric} className="py-3 pr-3">
                      <div className="flex flex-col gap-1">
                        <CountButton
                          value={unit[metric]}
                          metric={metric}
                          unit={unit}
                          label={t(`${OU}.executive.indicators.${metric}`)}
                          onDrillDown={onDrillDown}
                        />
                        {metric === "throughput" && <ThroughputSparkline bars={unit.throughput_sparkline} />}
                      </div>
                    </td>
                  ))}
                  <td className="py-3 pr-3 tabular-nums">{formatDuration(unit.queue_age_p50, t)}</td>
                  <td className="py-3 pr-3 tabular-nums">{formatDuration(unit.queue_age_p90, t)}</td>
                  <td className="py-3 pr-3 tabular-nums">{formatDuration(unit.cycle_time_p50, t)}</td>
                  <td className="py-3 pr-3 tabular-nums">{formatDuration(unit.cycle_time_p90, t)}</td>
                  <td className="py-3 pr-3 tabular-nums">{formatRatio(unit.concentration_top3)}</td>
                  <td className="py-3 pr-3 tabular-nums">{formatRatio(unit.auto_assign_kept_ratio)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <section className="flex max-w-3xl flex-col gap-3">
        <h4 className="text-custom-text-100 text-base font-medium">{t(`${OU}.executive.processes.heading`)}</h4>
        <div className="text-custom-text-300 text-sm flex flex-wrap gap-x-6 gap-y-1">
          <span>
            {t(`${OU}.executive.processes.running`)}: {report.processes.running}
          </span>
          <span>
            {t(`${OU}.executive.processes.completed`)}: {report.processes.completed}
          </span>
          <span>
            {t(`${OU}.executive.processes.lead_time_p50`)}: {formatDuration(report.processes.lead_time_p50, t)}
          </span>
          <span>
            {t(`${OU}.executive.processes.lead_time_p90`)}: {formatDuration(report.processes.lead_time_p90, t)}
          </span>
        </div>
        {report.processes.running === 0 && report.processes.completed === 0 ? (
          <p className="text-sm text-custom-text-300">{t(`${OU}.executive.processes.empty`)}</p>
        ) : report.processes.delayed_steps.length === 0 ? null : (
          <ul className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
            {report.processes.delayed_steps.map((step) => (
              <li
                key={`${step.process_instance_id}:${step.step_key}`}
                className="text-sm flex items-center justify-between px-4 py-2"
              >
                <span className="text-custom-text-100">
                  {t(`${OU}.executive.processes.step`, { step: step.step_key, template: step.template_name })}
                </span>
                <span className="text-custom-text-300 tabular-nums">{formatDuration(step.late_seconds, t)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {drill && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-4">
            <h4 className="text-custom-text-100 text-base font-medium">
              {t(`${OU}.executive.drill_down.title`, {
                metric: t(`${OU}.executive.indicators.${drill.metric}`),
                unit: drillUnit?.name ?? "",
              })}
            </h4>
            <Button variant="secondary" size="sm" onClick={() => store.clearExecutiveDrillDown()}>
              {t(`${OU}.executive.drill_down.close`)}
            </Button>
          </div>
          {drill.hidden_count > 0 && (
            <p className="text-sm text-custom-text-300">
              {t(`${OU}.executive.hidden_count`, { count: drill.hidden_count })}
            </p>
          )}
          <QueueList
            workspaceSlug={workspaceSlug}
            unitId={drill.unit_id}
            rows={drill.results ?? []}
            isLoading={store.executiveDrillLoader}
            emptyMessage={t(`${OU}.executive.drill_down.empty`)}
            onAssign={() => undefined}
          />
        </section>
      )}
    </div>
  );
});
