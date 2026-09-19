/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * @description Crash guard: i18next-icu unconditionally returns raw objects when
 * `t()` is called with a branch (namespace-node) key, regardless of
 * `returnObjects: false`. Without this wrapper, React unmounts the subtree with
 * "Objects are not valid as a React child". Strings pass through;
 * numbers/booleans are stringified; objects/null/undefined fall back to the key
 * itself plus a dev-mode warning. Can be removed once `t()` gains key-level type
 * safety (Phase 2 of the i18n roadmap).
 *
 * Shared by the hook and by the standalone `translate()`, so a key that renders
 * one way inside a component cannot render another way outside one.
 * @param key The catalogue key that was looked up, used as the fallback.
 * @param value Whatever i18next returned for it.
 * @returns A string, always.
 */
export function coerceToString(key: string, value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (process.env.NODE_ENV !== "production") {
    // eslint-disable-next-line no-console
    console.warn(
      `[i18n] Translation for key "${key}" is not a string (got ${
        value === null ? "null" : typeof value
      }). This is likely a missing key or a namespace-node lookup. Returning the key as fallback.`
    );
  }
  return key;
}
