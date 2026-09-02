/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { DEFAULT_LANGUAGE_CONFIG_KEY, DEFAULT_LANGUAGE_FALLBACK, LANGUAGE_CHOICES } from "@plane/constants";
import type { TLanguageCode } from "@plane/constants";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IFormattedInstanceConfiguration } from "@plane/types";
import { CustomSelect } from "@plane/ui";
// hooks
import { useInstance } from "@/hooks/store";

type Props = {
  config: IFormattedInstanceConfiguration;
};

/**
 * @description Orca (fork): the interface language this instance falls back to.
 *
 * Its own form rather than a field on the instance-details form above it,
 * because the two write to different endpoints — instance details are columns
 * on the Instance row, this is an InstanceConfiguration key. Every other
 * configuration screen in god-mode is shaped this way.
 *
 * Saving invalidates the cached /api/instances/ response server-side, so the
 * change reaches the sign-in screen on the next load rather than after the
 * two-hour cache window.
 */
export const DefaultLanguageForm = observer(function DefaultLanguageForm(props: Props) {
  const { config } = props;
  // store
  const { updateInstanceConfigurations } = useInstance();
  // A stored value outside the catalogue would leave the select with nothing
  // to show, so fall back the same way the API does when it reads this key.
  const storedValue = config[DEFAULT_LANGUAGE_CONFIG_KEY] as TLanguageCode | undefined;
  const initialValue = LANGUAGE_CHOICES.some((choice) => choice.value === storedValue)
    ? (storedValue as TLanguageCode)
    : DEFAULT_LANGUAGE_FALLBACK;

  const [language, setLanguage] = useState<TLanguageCode>(initialValue);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const selectedLabel =
    LANGUAGE_CHOICES.find((choice) => choice.value === language)?.label ?? DEFAULT_LANGUAGE_FALLBACK;

  const onSubmit = async () => {
    setIsSubmitting(true);
    try {
      await updateInstanceConfigurations({ [DEFAULT_LANGUAGE_CONFIG_KEY]: language });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Success",
        message: "Default language updated successfully",
      });
    } catch (error) {
      console.error(error);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error",
        message: "Default language could not be updated. Please try again.",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="text-16 font-medium text-primary">Default language</div>
        <div className="text-13 font-regular text-tertiary">
          The language new members start in, and the language the sign-in and sign-up screens use. Anyone can pick a
          different one for themselves in their profile preferences, and their choice is never overwritten.
        </div>
      </div>
      <div className="grid-col grid w-full grid-cols-1 items-center justify-between gap-8 md:grid-cols-2 lg:grid-cols-3">
        <div className="flex flex-col gap-1">
          <h4 className="text-13 text-tertiary">Language</h4>
          <CustomSelect
            value={language}
            label={selectedLabel}
            onChange={(value: TLanguageCode) => setLanguage(value)}
            buttonClassName="rounded-md border-subtle"
            maxHeight="lg"
            input
          >
            {LANGUAGE_CHOICES.map((choice) => (
              <CustomSelect.Option key={choice.value} value={choice.value} className="w-full">
                {choice.label}
              </CustomSelect.Option>
            ))}
          </CustomSelect>
        </div>
      </div>
      <div>
        <Button
          variant="primary"
          size="lg"
          onClick={() => {
            void onSubmit();
          }}
          loading={isSubmitting}
          disabled={language === initialValue}
        >
          {isSubmitting ? "Saving" : "Save language"}
        </Button>
      </div>
    </div>
  );
});
