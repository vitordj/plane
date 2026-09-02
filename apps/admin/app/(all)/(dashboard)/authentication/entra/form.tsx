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
import type { IFormattedInstanceConfiguration, TInstanceEntraAuthenticationConfigurationKeys } from "@plane/types";
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

type EntraConfigFormValues = Record<TInstanceEntraAuthenticationConfigurationKeys, string>;

const ENTRA_FORM_SWITCH_FIELD: TControllerSwitchFormField<EntraConfigFormValues> = {
  name: "ENABLE_ENTRA_SYNC",
  label: "Microsoft Entra ID",
};

const APP_REGISTRATIONS_URL = "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade";

export function InstanceEntraConfigForm(props: Props) {
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
  } = useForm<EntraConfigFormValues>({
    defaultValues: {
      ENTRA_TENANT_ID: config["ENTRA_TENANT_ID"],
      ENTRA_CLIENT_ID: config["ENTRA_CLIENT_ID"],
      ENTRA_CLIENT_SECRET: config["ENTRA_CLIENT_SECRET"],
      ENABLE_ENTRA_SYNC: config["ENABLE_ENTRA_SYNC"] || "0",
    },
  });

  const originURL = !isEmpty(API_BASE_URL) ? API_BASE_URL : typeof window !== "undefined" ? window.location.origin : "";

  const ENTRA_FORM_FIELDS: TControllerInputFormField[] = [
    {
      key: "ENTRA_TENANT_ID",
      type: "text",
      label: "Directory (tenant) ID",
      // The tenant is a security control, not a convenience setting: Plane
      // matches an OAuth identity to an account by email, so a multi-tenant
      // authority would let any Azure directory assert any address.
      description: (
        <>
          The <CodeBlock darkerShade>Directory (tenant) ID</CodeBlock> shown on your app registration&apos;s Overview
          page. A specific tenant is required — <CodeBlock darkerShade>common</CodeBlock> and{" "}
          <CodeBlock darkerShade>organizations</CodeBlock> are rejected, because they would let accounts outside your
          directory sign in with any email address.
        </>
      ),
      placeholder: "72f988bf-86f1-41af-91ab-2d7cd011db47",
      error: Boolean(errors.ENTRA_TENANT_ID),
      required: true,
    },
    {
      key: "ENTRA_CLIENT_ID",
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
            Entra app registration.
          </a>
        </>
      ),
      placeholder: "3f7c2a91-0b4e-4d8a-9c65-1e2f3a4b5c6d",
      error: Boolean(errors.ENTRA_CLIENT_ID),
      required: true,
    },
    {
      key: "ENTRA_CLIENT_SECRET",
      type: "password",
      label: "Client secret",
      description: (
        <>
          Create this under <CodeBlock darkerShade>Certificates &amp; secrets</CodeBlock> in your app registration, and
          copy the secret <span className="font-medium">Value</span> — not the Secret ID. Entra shows it only once, and
          it expires on the date you chose.
        </>
      ),
      placeholder: "abc8Q~xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      error: Boolean(errors.ENTRA_CLIENT_SECRET),
      required: true,
    },
  ];

  const ENTRA_SERVICE_FIELD: TCopyField[] = [
    {
      key: "Callback_URI",
      label: "Redirect URI",
      url: `${originURL}/auth/entra/callback/`,
      description: (
        <>
          We will auto-generate this. In your app registration, add it under{" "}
          <CodeBlock darkerShade>Authentication</CodeBlock> as a platform of type <CodeBlock darkerShade>Web</CodeBlock>
          . Also grant the delegated Microsoft Graph permission <CodeBlock darkerShade>User.Read</CodeBlock>, which is
          what lets Plane read the signed-in person&apos;s name and email.
        </>
      ),
    },
  ];

  const onSubmit = async (formData: EntraConfigFormValues) => {
    const payload: Partial<EntraConfigFormValues> = { ...formData };

    try {
      const response = await updateInstanceConfigurations(payload);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Done!",
        message: "Your Microsoft Entra ID authentication is configured. You should test it now.",
      });
      reset({
        ENTRA_TENANT_ID: response.find((item) => item.key === "ENTRA_TENANT_ID")?.value,
        ENTRA_CLIENT_ID: response.find((item) => item.key === "ENTRA_CLIENT_ID")?.value,
        ENTRA_CLIENT_SECRET: response.find((item) => item.key === "ENTRA_CLIENT_SECRET")?.value,
        ENABLE_ENTRA_SYNC: response.find((item) => item.key === "ENABLE_ENTRA_SYNC")?.value,
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
            {ENTRA_FORM_FIELDS.map((field) => (
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
            <ControllerSwitch control={control} field={ENTRA_FORM_SWITCH_FIELD} />
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
              <div className="pt-2 text-18 font-medium">Plane-provided details for Entra</div>
              {ENTRA_SERVICE_FIELD.map((field) => (
                <CopyField key={field.key} label={field.label} url={field.url} description={field.description} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
