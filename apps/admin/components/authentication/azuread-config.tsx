/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import Link from "next/link";
// icons
import { Settings2 } from "lucide-react";
// plane internal packages
import { getButtonStyling } from "@plane/propel/button";
import type { TInstanceAuthenticationMethodKeys } from "@plane/types";
import { ToggleSwitch } from "@plane/ui";
import { cn } from "@plane/utils";
// hooks
import { useInstance } from "@/hooks/store";

type Props = {
  disabled: boolean;
  updateConfig: (key: TInstanceAuthenticationMethodKeys, value: string) => void;
};

export const AzureADConfiguration = observer(function AzureADConfiguration(props: Props) {
  const { disabled, updateConfig } = props;
  // store
  const { formattedConfig } = useInstance();
  // derived values
  const azureADConfig = formattedConfig?.IS_AZUREAD_ENABLED ?? "";
  const azureADConfigured =
    !!formattedConfig?.AZUREAD_TENANT_ID &&
    !!formattedConfig?.AZUREAD_CLIENT_ID &&
    !!formattedConfig?.AZUREAD_CLIENT_SECRET;

  return (
    <>
      {azureADConfigured ? (
        <div className="flex items-center gap-4">
          <Link href="/authentication/azuread" className={cn(getButtonStyling("link", "base"), "font-medium")}>
            Edit
          </Link>
          <ToggleSwitch
            value={Boolean(parseInt(azureADConfig))}
            onChange={() => {
              Boolean(parseInt(azureADConfig)) === true
                ? updateConfig("IS_AZUREAD_ENABLED", "0")
                : updateConfig("IS_AZUREAD_ENABLED", "1");
            }}
            size="sm"
            disabled={disabled}
          />
        </div>
      ) : (
        <Link href="/authentication/azuread" className={cn(getButtonStyling("secondary", "base"), "text-tertiary")}>
          <Settings2 className="h-4 w-4 p-0.5 text-tertiary" />
          Configure
        </Link>
      )}
    </>
  );
});
