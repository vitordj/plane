/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { isEmpty } from "lodash-es";
import Link from "next/link";
import { useForm } from "react-hook-form";
// plane internal packages
import { API_BASE_URL } from "@plane/constants";
import { Button, getButtonStyling } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IFormattedInstanceConfiguration, TInstanceOidcFreeAuthenticationConfigurationKeys } from "@plane/types";
// components
import { CodeBlock } from "@/components/common/code-block";
import { ConfirmDiscardModal } from "@/components/common/confirm-discard-modal";
import type { TControllerInputFormField } from "@/components/common/controller-input";
import { ControllerInput } from "@/components/common/controller-input";
import type { TControllerSwitchFormField } from "@/components/common/controller-switch";
import { ControllerSwitch } from "@/components/common/controller-switch";
import type { TCopyField } from "@/components/common/copy-field";
import { CopyField } from "@/components/common/copy-field";
// hooks
import { useInstance } from "@/hooks/store";

type Props = {
  config: IFormattedInstanceConfiguration;
};

type OidcFreeConfigFormValues = Record<TInstanceOidcFreeAuthenticationConfigurationKeys, string>;

export function InstanceOidcFreeConfigForm(props: Props) {
  const { config } = props;
  // states
  const [isDiscardChangesModalOpen, setIsDiscardChangesModalOpen] = useState(false);
  // store hooks
  const { updateInstanceConfigurations } = useInstance();
  // form data
  const {
    handleSubmit,
    control,
    reset,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<OidcFreeConfigFormValues>({
    defaultValues: {
      OIDC_FREE_DISPLAY_NAME: config["OIDC_FREE_DISPLAY_NAME"],
      OIDC_FREE_DISCOVERY_URL: config["OIDC_FREE_DISCOVERY_URL"],
      OIDC_FREE_CLIENT_ID: config["OIDC_FREE_CLIENT_ID"],
      OIDC_FREE_CLIENT_SECRET: config["OIDC_FREE_CLIENT_SECRET"],
      OIDC_FREE_AUTH_URL: config["OIDC_FREE_AUTH_URL"],
      OIDC_FREE_TOKEN_URL: config["OIDC_FREE_TOKEN_URL"],
      OIDC_FREE_USERINFO_URL: config["OIDC_FREE_USERINFO_URL"],
      OIDC_FREE_JWKS_URL: config["OIDC_FREE_JWKS_URL"],
      OIDC_FREE_SCOPE: config["OIDC_FREE_SCOPE"],
      OIDC_FREE_ALLOW_UNVERIFIED_EMAIL: config["OIDC_FREE_ALLOW_UNVERIFIED_EMAIL"] || "0",
      ENABLE_OIDC_FREE_SYNC: config["ENABLE_OIDC_FREE_SYNC"] || "0",
    },
  });

  const originURL = !isEmpty(API_BASE_URL) ? API_BASE_URL : typeof window !== "undefined" ? window.location.origin : "";

  const OIDC_FREE_FORM_FIELDS: TControllerInputFormField[] = [
    {
      key: "OIDC_FREE_DISPLAY_NAME",
      type: "text",
      label: "Display name",
      description: <>What the sign-in button calls your provider. Defaults to OIDC.</>,
      placeholder: "Azure AD",
      error: Boolean(errors.OIDC_FREE_DISPLAY_NAME),
      required: false,
    },
    {
      key: "OIDC_FREE_DISCOVERY_URL",
      type: "text",
      label: "Discovery URL",
      description: (
        <>
          Your provider&apos;s issuer or <CodeBlock>{"/.well-known/openid-configuration"}</CodeBlock> URL. Set this and
          Plane reads the three endpoints below from it, so you can leave them blank.
        </>
      ),
      placeholder: "https://login.microsoftonline.com/<tenant id>/v2.0",
      error: Boolean(errors.OIDC_FREE_DISCOVERY_URL),
      required: false,
    },
    {
      key: "OIDC_FREE_CLIENT_ID",
      type: "text",
      label: "Client ID",
      description: <>You will get this from the application you registered with your provider.</>,
      placeholder: "plane",
      error: Boolean(errors.OIDC_FREE_CLIENT_ID),
      required: true,
    },
    {
      key: "OIDC_FREE_CLIENT_SECRET",
      type: "password",
      label: "Client secret",
      description: <>Your client secret is issued alongside the client ID by your provider.</>,
      placeholder: "9b0050f94ec1b744e32ce79ea4ffacd40d4119cb",
      error: Boolean(errors.OIDC_FREE_CLIENT_SECRET),
      required: true,
    },
    {
      key: "OIDC_FREE_AUTH_URL",
      type: "text",
      label: "Authorization URL",
      description: <>Where members are sent to sign in. Only needed without a discovery URL.</>,
      placeholder: "https://sso.example.com/authorize",
      error: Boolean(errors.OIDC_FREE_AUTH_URL),
      required: false,
    },
    {
      key: "OIDC_FREE_TOKEN_URL",
      type: "text",
      label: "Token URL",
      description: <>Where Plane exchanges the authorization code for tokens.</>,
      placeholder: "https://sso.example.com/token",
      error: Boolean(errors.OIDC_FREE_TOKEN_URL),
      required: false,
    },
    {
      key: "OIDC_FREE_USERINFO_URL",
      type: "text",
      label: "User info URL",
      description: (
        <>
          Where Plane reads the member&apos;s claims. Providers may serve this from a different host than the two above,
          so give it in full.
        </>
      ),
      placeholder: "https://sso.example.com/userinfo",
      error: Boolean(errors.OIDC_FREE_USERINFO_URL),
      required: false,
    },
    {
      key: "OIDC_FREE_JWKS_URL",
      type: "text",
      label: "JWKS URL",
      description: (
        <>
          The keys Plane verifies your provider&apos;s <CodeBlock>id_token</CodeBlock> with. A discovery URL supplies
          this, and leaving it blank means sign-in tokens are not verified.
        </>
      ),
      placeholder: "https://sso.example.com/.well-known/jwks.json",
      error: Boolean(errors.OIDC_FREE_JWKS_URL),
      required: false,
    },
    {
      key: "OIDC_FREE_SCOPE",
      type: "text",
      label: "Scopes",
      description: (
        <>
          Space-separated scopes to request. The <CodeBlock>openid</CodeBlock> scope is required, and Plane needs an
          email address to identify the member.
        </>
      ),
      placeholder: "openid email profile",
      error: Boolean(errors.OIDC_FREE_SCOPE),
      required: false,
    },
  ];

  const OIDC_FREE_FORM_SWITCH_FIELDS: TControllerSwitchFormField<OidcFreeConfigFormValues>[] = [
    {
      name: "ENABLE_OIDC_FREE_SYNC",
      label: "OIDC",
    },
    {
      name: "OIDC_FREE_ALLOW_UNVERIFIED_EMAIL",
      label: "OIDC",
      description:
        "Accept email addresses your provider does not mark as verified. Only turn this on for a provider that vouches for the addresses it asserts, such as a directory whose members you administer.",
    },
  ];

  const OIDC_FREE_SERVICE_FIELD: TCopyField[] = [
    {
      key: "Callback_URI",
      label: "Callback URI",
      url: `${originURL}/auth/oidc-free/callback/`,
      description: (
        <>
          We will auto-generate this. Register it with your provider as an allowed{" "}
          <CodeBlock darkerShade>redirect URI</CodeBlock>.
        </>
      ),
    },
  ];

  const onSubmit = async (formData: OidcFreeConfigFormValues) => {
    const payload: Partial<OidcFreeConfigFormValues> = { ...formData };

    try {
      const response = await updateInstanceConfigurations(payload);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Done!",
        message: "Your OIDC authentication is configured. You should test it now.",
      });
      reset({
        OIDC_FREE_DISPLAY_NAME: response.find((item) => item.key === "OIDC_FREE_DISPLAY_NAME")?.value,
        OIDC_FREE_DISCOVERY_URL: response.find((item) => item.key === "OIDC_FREE_DISCOVERY_URL")?.value,
        OIDC_FREE_CLIENT_ID: response.find((item) => item.key === "OIDC_FREE_CLIENT_ID")?.value,
        OIDC_FREE_CLIENT_SECRET: response.find((item) => item.key === "OIDC_FREE_CLIENT_SECRET")?.value,
        OIDC_FREE_AUTH_URL: response.find((item) => item.key === "OIDC_FREE_AUTH_URL")?.value,
        OIDC_FREE_TOKEN_URL: response.find((item) => item.key === "OIDC_FREE_TOKEN_URL")?.value,
        OIDC_FREE_USERINFO_URL: response.find((item) => item.key === "OIDC_FREE_USERINFO_URL")?.value,
        OIDC_FREE_JWKS_URL: response.find((item) => item.key === "OIDC_FREE_JWKS_URL")?.value,
        OIDC_FREE_SCOPE: response.find((item) => item.key === "OIDC_FREE_SCOPE")?.value,
        OIDC_FREE_ALLOW_UNVERIFIED_EMAIL: response.find((item) => item.key === "OIDC_FREE_ALLOW_UNVERIFIED_EMAIL")
          ?.value,
        ENABLE_OIDC_FREE_SYNC: response.find((item) => item.key === "ENABLE_OIDC_FREE_SYNC")?.value,
      });
    } catch (err) {
      console.error(err);
    }
  };

  const handleGoBack = (e: React.MouseEvent<HTMLAnchorElement, MouseEvent>) => {
    if (isDirty) {
      e.preventDefault();
      setIsDiscardChangesModalOpen(true);
    }
  };

  return (
    <>
      <ConfirmDiscardModal
        isOpen={isDiscardChangesModalOpen}
        onDiscardHref="/authentication"
        handleClose={() => setIsDiscardChangesModalOpen(false)}
      />
      <div className="flex flex-col gap-8">
        <div className="grid w-full grid-cols-2 gap-x-12 gap-y-8">
          <div className="col-span-2 flex flex-col gap-y-4 pt-1 md:col-span-1">
            <div className="pt-2.5 text-18 font-medium">Provider-provided details for Plane</div>
            {OIDC_FREE_FORM_FIELDS.map((field) => (
              <ControllerInput
                key={field.key}
                control={control}
                type={field.type}
                name={field.key}
                label={field.label}
                description={field.description}
                placeholder={field.placeholder}
                error={field.error}
                required={field.required}
              />
            ))}
            {OIDC_FREE_FORM_SWITCH_FIELDS.map((field) => (
              <ControllerSwitch key={field.name} control={control} field={field} />
            ))}
            <div className="flex flex-col gap-1 pt-4">
              <div className="flex items-center gap-4">
                <Button
                  variant="primary"
                  size="lg"
                  onClick={(e) => void handleSubmit(onSubmit)(e)}
                  loading={isSubmitting}
                  disabled={!isDirty}
                >
                  {isSubmitting ? "Saving" : "Save changes"}
                </Button>
                <Link href="/authentication" className={getButtonStyling("secondary", "lg")} onClick={handleGoBack}>
                  Go back
                </Link>
              </div>
            </div>
          </div>
          <div className="col-span-2 md:col-span-1">
            <div className="flex flex-col gap-y-4 rounded-lg bg-layer-1 px-6 pt-1.5 pb-4">
              <div className="pt-2 text-18 font-medium">Plane-provided details for your provider</div>
              {OIDC_FREE_SERVICE_FIELD.map((field) => (
                <CopyField key={field.key} label={field.label} url={field.url} description={field.description} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
