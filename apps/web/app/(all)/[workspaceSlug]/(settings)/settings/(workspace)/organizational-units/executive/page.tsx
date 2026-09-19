/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "react-router";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { TExecutiveMetric, TExecutivePeriod } from "@plane/types";
import { Loader } from "@plane/ui";
// components
import { PageHead } from "@/components/core/page-title";
import { OrganizationalUnitExecutiveView } from "@/components/orca/organizational-units";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { OrganizationalUnitsExecutiveHeader } from "./header";

const OU = "workspace_settings.settings.organizational_units";
const PERIODS: TExecutivePeriod[] = ["7d", "30d", "90d"];

const OrganizationalUnitsExecutivePage = observer(function OrganizationalUnitsExecutivePage() {
  const { workspaceSlug } = useParams();
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const [period, setPeriod] = useState<TExecutivePeriod>("30d");

  useEffect(() => {
    if (!workspaceSlug) return;
    store.fetchConfig(workspaceSlug.toString());
    store.fetchExecutive(workspaceSlug.toString(), { period }).catch((error) => {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.executive.load_failed`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    });
    store.clearExecutiveDrillDown();
  }, [workspaceSlug, period, store, t]);

  const handleDrillDown = (unitId: string, metric: TExecutiveMetric) => {
    if (!workspaceSlug) return;
    store.fetchExecutiveDrillDown(workspaceSlug.toString(), { unit: unitId, metric, period }).catch((error) => {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.executive.load_failed`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    });
  };

  const title = t(`${OU}.executive.title`);

  if (!store.isEnabled) {
    return (
      <SettingsContentWrapper hugging header={<OrganizationalUnitsExecutiveHeader />}>
        <PageHead title={title} />
        <div className="flex flex-col gap-2 p-6">
          <h3 className="text-xl text-custom-text-100 font-medium">{t(`${OU}.executive.heading`)}</h3>
          <p className="text-sm text-custom-text-300">{t(`${OU}.disabled`)}</p>
        </div>
      </SettingsContentWrapper>
    );
  }

  return (
    <SettingsContentWrapper hugging header={<OrganizationalUnitsExecutiveHeader />}>
      <PageHead title={title} />
      <div className="flex flex-col gap-6 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-subtle pb-6">
          <div>
            <h3 className="text-xl text-custom-text-100 font-medium">{t(`${OU}.executive.heading`)}</h3>
            <p className="text-sm text-custom-text-300">{t(`${OU}.executive.description`)}</p>
          </div>
          <div className="flex items-center gap-2" role="group" aria-label={t(`${OU}.executive.period.label`)}>
            {PERIODS.map((value) => (
              <Button
                key={value}
                variant={period === value ? "primary" : "secondary"}
                size="sm"
                onClick={() => setPeriod(value)}
              >
                {t(`${OU}.executive.period.${value}`)}
              </Button>
            ))}
          </div>
        </div>

        {store.executiveLoader && !store.executiveReport ? (
          <Loader className="flex flex-col gap-2">
            <Loader.Item height="56px" />
            <Loader.Item height="56px" />
          </Loader>
        ) : (
          workspaceSlug && (
            <OrganizationalUnitExecutiveView workspaceSlug={workspaceSlug.toString()} onDrillDown={handleDrillDown} />
          )
        )}
      </div>
    </SettingsContentWrapper>
  );
});

export default OrganizationalUnitsExecutivePage;
