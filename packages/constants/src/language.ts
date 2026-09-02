/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Interface languages, for consumers that must not pull in `@plane/i18n`.
 *
 * `@plane/i18n` owns the real list in `src/constants/language.ts`, and this is
 * a deliberate mirror of it rather than a re-export: importing anything from
 * `@plane/i18n` runs its index, which creates and initializes an i18next
 * instance as a module side effect. The god-mode admin app has no translations
 * and no `TranslationProvider`, so booting an i18next instance there just to
 * read nineteen labels would be worse than this duplication.
 *
 * The two lists are kept honest by `test_default_language.py`, which parses
 * both files plus the locale directories and fails the build if they disagree.
 */
export type TLanguageCode =
  | "en"
  | "fr"
  | "es"
  | "ja"
  | "zh-CN"
  | "zh-TW"
  | "ru"
  | "it"
  | "cs"
  | "sk"
  | "de"
  | "ua"
  | "pl"
  | "ko"
  | "pt-BR"
  | "id"
  | "ro"
  | "vi-VN"
  | "tr-TR";

export type TLanguageChoice = {
  label: string;
  value: TLanguageCode;
};

/** Labels are endonyms — each language names itself, as in the language picker. */
export const LANGUAGE_CHOICES: TLanguageChoice[] = [
  { label: "English", value: "en" },
  { label: "Français", value: "fr" },
  { label: "Español", value: "es" },
  { label: "日本語", value: "ja" },
  { label: "简体中文", value: "zh-CN" },
  { label: "繁體中文", value: "zh-TW" },
  { label: "Русский", value: "ru" },
  { label: "Italiano", value: "it" },
  { label: "Čeština", value: "cs" },
  { label: "Slovenčina", value: "sk" },
  { label: "Deutsch", value: "de" },
  { label: "Українська", value: "ua" },
  { label: "Polski", value: "pl" },
  { label: "한국어", value: "ko" },
  { label: "Português Brasil", value: "pt-BR" },
  { label: "Bahasa Indonesia", value: "id" },
  { label: "Română", value: "ro" },
  { label: "Tiếng việt", value: "vi-VN" },
  { label: "Türkçe", value: "tr-TR" },
];

/** The one locale guaranteed complete: it is the source catalogue. */
export const DEFAULT_LANGUAGE_FALLBACK: TLanguageCode = "en";

/**
 * The instance configuration key holding the organization's default language.
 * `as const` so it narrows to the literal and can index
 * `IFormattedInstanceConfiguration`, whose keys are a union of literals.
 */
export const DEFAULT_LANGUAGE_CONFIG_KEY = "DEFAULT_LANGUAGE" as const;
