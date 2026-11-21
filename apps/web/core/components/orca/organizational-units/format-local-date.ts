/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * App locale codes that are not valid BCP-47 language tags, mapped to the tag
 * `Intl` understands. The app's Ukrainian code is `ua`; the language tag is `uk`.
 */
const INTL_LOCALE_BY_APP_LOCALE: Record<string, string> = { ua: "uk" };

/**
 * @description Formats an ISO date for the viewer's locale (medium date style).
 * Shared by the availability form and the queue rows so both read dates alike.
 * Falls back to the runtime default locale when `Intl` rejects the code, and
 * to the raw string when the date cannot be parsed.
 * @param iso An ISO date or date-time string.
 * @param locale The app locale code (for example `pt-BR` or `ua`).
 * @returns The formatted date.
 */
export function formatLocalDate(iso: string, locale: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat(INTL_LOCALE_BY_APP_LOCALE[locale] ?? locale, { dateStyle: "medium" }).format(parsed);
  } catch {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(parsed);
  }
}
