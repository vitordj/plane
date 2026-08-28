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
import { AnchorButton } from "@makeplane/propel/components/anchor-button";
import { Button } from "@makeplane/propel/components/button";
import { Switch } from "@makeplane/propel/components/switch";
import type { TInstanceAuthenticationMethodKeys } from "@plane/types";
// hooks
import { useInstance } from "@/hooks/store";

type Props = {
  disabled: boolean;
  updateConfig: (key: TInstanceAuthenticationMethodKeys, value: string) => void;
};

export const OidcFreeConfiguration = observer(function OidcFreeConfiguration(props: Props) {
  const { disabled, updateConfig } = props;
  // store
  const { formattedConfig } = useInstance();
  // derived values
  const OidcFreeConfig = formattedConfig?.IS_OIDC_FREE_ENABLED ?? "";
  const OidcFreeConfigured = [
    !!formattedConfig?.OIDC_FREE_HOST,
    !!formattedConfig?.OIDC_FREE_CLIENT_ID,
    !!formattedConfig?.OIDC_FREE_CLIENT_SECRET,
    !!formattedConfig?.OIDC_FREE_SCOPE,
    !!formattedConfig?.OIDC_FREE_AUTH_URI,
    !!formattedConfig?.OIDC_FREE_TOKEN_URL,
    !!formattedConfig?.OIDC_FREE_USERINFO_URL,
    !!formattedConfig?.OIDC_FREE_CALLBACK_URI,
  ].every(Boolean);

  return (
    <>
      {OidcFreeConfigured ? (
        <div className="flex items-center gap-4">
          <AnchorButton
            variant="primary"
            size="sm"
            nativeButton={false}
            render={<Link href="/authentication/oidc-free" />}
            label="Edit"
          />
          <Switch
            checked={Boolean(parseInt(OidcFreeConfig))}
            onCheckedChange={() => {
              updateConfig("IS_OIDC_FREE_ENABLED", parseInt(OidcFreeConfig) ? "0" : "1");
            }}
            size="sm"
            disabled={disabled}
          />
        </div>
      ) : (
        <Button
          variant="secondary"
          size="sm"
          stretch="auto"
          nativeButton={false}
          render={<Link href="/authentication/oidc-free" />}
          icon={<Settings2 className="h-4 w-4 p-0.5 text-tertiary" />}
          label="Configure"
        />
      )}
    </>
  );
});
