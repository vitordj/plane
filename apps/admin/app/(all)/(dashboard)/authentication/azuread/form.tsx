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
import type { IFormattedInstanceConfiguration, TInstanceAzureADAuthenticationConfigurationKeys } from "@plane/types";
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

type AzureADConfigFormValues = Record<TInstanceAzureADAuthenticationConfigurationKeys, string>;

const AZUREAD_FORM_SWITCH_FIELD: TControllerSwitchFormField<AzureADConfigFormValues> = {
  name: "ENABLE_AZUREAD_SYNC",
  label: "Microsoft Entra ID",
};

const APP_REGISTRATIONS_URL = "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade";

export function InstanceAzureADConfigForm(props: Props) {
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
  } = useForm<AzureADConfigFormValues>({
    defaultValues: {
      AZUREAD_TENANT_ID: config["AZUREAD_TENANT_ID"],
      AZUREAD_CLIENT_ID: config["AZUREAD_CLIENT_ID"],
      AZUREAD_CLIENT_SECRET: config["AZUREAD_CLIENT_SECRET"],
      ENABLE_AZUREAD_SYNC: config["ENABLE_AZUREAD_SYNC"] || "0",
    },
  });

  const originURL = !isEmpty(API_BASE_URL) ? API_BASE_URL : typeof window !== "undefined" ? window.location.origin : "";

  const AZUREAD_FORM_FIELDS: TControllerInputFormField[] = [
    {
      key: "AZUREAD_TENANT_ID",
      type: "text",
      label: "Directory (tenant) ID",
      description: (
        <>
          The tenant whose accounts may sign in. Use the directory ID from your{" "}
          <a
            href={APP_REGISTRATIONS_URL}
            target="_blank"
            className="text-accent-primary hover:underline"
            rel="noreferrer"
          >
            app registration overview
          </a>
          . Setting this to <CodeBlock darkerShade>common</CodeBlock> or{" "}
          <CodeBlock darkerShade>organizations</CodeBlock> lets accounts from <em>any</em> Microsoft directory sign in,
          so only use those for a deliberately multi-tenant instance.
        </>
      ),
      placeholder: "72f988bf-86f1-41af-91ab-2d7cd011db47",
      error: Boolean(errors.AZUREAD_TENANT_ID),
      required: true,
    },
    {
      key: "AZUREAD_CLIENT_ID",
      type: "text",
      label: "Application (client) ID",
      description: (
        <>
          You will get this from your{" "}
          <a
            href={APP_REGISTRATIONS_URL}
            target="_blank"
            className="text-accent-primary hover:underline"
            rel="noreferrer"
          >
            Entra ID app registration.
          </a>
        </>
      ),
      placeholder: "5a1c2f30-9d4b-4e7a-8f26-1b3c9de0a4f7",
      error: Boolean(errors.AZUREAD_CLIENT_ID),
      required: true,
    },
    {
      key: "AZUREAD_CLIENT_SECRET",
      type: "password",
      label: "Client secret",
      description: (
        <>
          Create this under <CodeBlock darkerShade>Certificates &amp; secrets</CodeBlock> in the same app registration
          and paste the secret <em>value</em>, not its ID. Entra secrets expire, so note the expiry date.
        </>
      ),
      placeholder: "abc8Q~Xy1zLmNoPqRsTuVwXyZ0123456789aBcDe",
      error: Boolean(errors.AZUREAD_CLIENT_SECRET),
      required: true,
    },
  ];

  const AZUREAD_SERVICE_FIELD: TCopyField[] = [
    {
      key: "Redirect_URI",
      label: "Redirect URI",
      url: `${originURL}/auth/azuread/callback/`,
      description: (
        <>
          We will auto-generate this. Add it as a <CodeBlock darkerShade>Web</CodeBlock> redirect URI under{" "}
          <CodeBlock darkerShade>Authentication</CodeBlock> in your{" "}
          <a
            href={APP_REGISTRATIONS_URL}
            target="_blank"
            className="text-accent-primary hover:underline"
            rel="noreferrer"
          >
            app registration.
          </a>{" "}
          The app also needs the delegated Microsoft Graph permission <CodeBlock darkerShade>User.Read</CodeBlock>.
        </>
      ),
    },
  ];

  const onSubmit = async (formData: AzureADConfigFormValues) => {
    const payload: Partial<AzureADConfigFormValues> = { ...formData };

    try {
      const response = await updateInstanceConfigurations(payload);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Done!",
        message: "Your Microsoft Entra ID authentication is configured. You should test it now.",
      });
      reset({
        AZUREAD_TENANT_ID: response.find((item) => item.key === "AZUREAD_TENANT_ID")?.value,
        AZUREAD_CLIENT_ID: response.find((item) => item.key === "AZUREAD_CLIENT_ID")?.value,
        AZUREAD_CLIENT_SECRET: response.find((item) => item.key === "AZUREAD_CLIENT_SECRET")?.value,
        ENABLE_AZUREAD_SYNC: response.find((item) => item.key === "ENABLE_AZUREAD_SYNC")?.value,
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
            <div className="pt-2.5 text-18 font-medium">Entra-provided details for Plane</div>
            {AZUREAD_FORM_FIELDS.map((field) => (
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
            <ControllerSwitch control={control} field={AZUREAD_FORM_SWITCH_FIELD} />
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
              <div className="pt-2 text-18 font-medium">Plane-provided details for Entra ID</div>
              {AZUREAD_SERVICE_FIELD.map((field) => (
                <CopyField key={field.key} label={field.label} url={field.url} description={field.description} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
