/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback } from "react";
import { useTranslation as useI18nextTranslation } from "react-i18next";
import { coerceToString } from "../core/coerce-to-string";
import { SUPPORTED_LANGUAGES, LANGUAGE_STORAGE_KEY } from "../constants/language";
import type { TLanguage, ILanguageOption } from "../types";

export type TTranslationStore = {
  t: (key: string, params?: Record<string, unknown>) => string;
  currentLocale: TLanguage;
  changeLanguage: (lng: TLanguage) => void;
  languages: ILanguageOption[];
};

export function useTranslation(): TTranslationStore {
  // No namespace arg — fallbackNS in the i18next config ensures all namespaces
  // are searched for any key. Passing NAMESPACES here would trigger concurrent
  // async loads per component, causing a re-render cascade.
  const { t, i18n } = useI18nextTranslation();

  const changeLanguage = useCallback(
    (lng: TLanguage) => {
      void (async () => {
        try {
          await i18n.changeLanguage(lng);
          if (typeof window === "undefined") return;
          localStorage.setItem(LANGUAGE_STORAGE_KEY, lng);
          document.documentElement.lang = lng;
        } catch (err) {
          console.error("Failed to change language:", err);
        }
      })();
    },
    [i18n]
  );

  return {
    t: (key: string, params?: Record<string, unknown>) =>
      coerceToString(key, params === undefined ? t(key) : t(key, params)),
    currentLocale: i18n.language as TLanguage,
    changeLanguage,
    languages: SUPPORTED_LANGUAGES,
  };
}
