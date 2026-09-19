/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import useSWR from "swr";
// components
import { PageWrapper } from "@/components/common/page-wrapper";
// hooks
import { useInstance } from "@/hooks/store";
// local imports
import { GeneralConfigurationForm } from "./form";
// Orca (fork)
import { DefaultLanguageForm } from "./language-form";
// types
import type { Route } from "./+types/page";

function GeneralPage() {
  const { instance, instanceAdmins, fetchInstanceConfigurations, formattedConfig } = useInstance();

  // Orca (fork): the default language is an InstanceConfiguration key rather
  // than a column on the instance, so it needs the configurations fetch the
  // other configuration screens make.
  useSWR("INSTANCE_CONFIGURATIONS", () => fetchInstanceConfigurations());

  return (
    <PageWrapper
      header={{
        title: "General settings",
        description:
          "Change the name of your instance and instance admin e-mail addresses. Enable or disable telemetry in your instance.",
      }}
    >
      <div className="space-y-8">
        {instance && instanceAdmins && <GeneralConfigurationForm instance={instance} instanceAdmins={instanceAdmins} />}
        {formattedConfig && (
          <div className="border-t border-subtle pt-8">
            <DefaultLanguageForm config={formattedConfig} />
          </div>
        )}
      </div>
    </PageWrapper>
  );
}

export const meta: Route.MetaFunction = () => [{ title: "General Settings - God Mode" }];

export default observer(GeneralPage);
