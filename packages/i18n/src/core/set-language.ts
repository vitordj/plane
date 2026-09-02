/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { initPromise, i18nInstance } from "./instance";
import { LANGUAGE_STORAGE_KEY } from "../constants/language";
import type { TLanguage } from "../types";

/**
 * Tracks whether this session has applied a language that belongs to a person
 * rather than to the instance.
 *
 * Two async boot paths race to set the language: the instance configuration
 * (which carries the organization's default) and the user profile (which
 * carries the person's own choice). Either can resolve first. The stored
 * preference alone cannot arbitrate — on a fresh device there is nothing
 * stored yet, and the profile fetch is exactly what puts it there. This flag
 * lets applyDefaultLanguage() stand down once a real preference has landed,
 * whichever order the two requests complete in.
 */
let hasExplicitLanguage = false;

/** Reads the stored preference, tolerating environments without localStorage. */
function readStoredLanguage(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY);
  } catch {
    // Private mode and "block site data" both throw on access rather than
    // returning null. Treat that as "no preference" instead of crashing boot.
    return null;
  }
}

/** Applies a language to i18next and the document, without persisting it. */
async function applyLanguage(lng: TLanguage): Promise<void> {
  await initPromise;
  await i18nInstance.changeLanguage(lng);
  if (typeof window !== "undefined") {
    document.documentElement.lang = lng;
  }
}

/**
 * @description Applies a language chosen by the person and remembers it, so the
 * next boot renders in it before any network request resolves.
 * @param lng The language the person picked, or the one their profile carries.
 */
export async function setLanguage(lng: TLanguage): Promise<void> {
  hasExplicitLanguage = true;
  await applyLanguage(lng);
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, lng);
    } catch {
      // Storage being unavailable costs a re-fetch on next boot, nothing more.
    }
  }
}

/**
 * @description Applies the organization's default language, and only that.
 *
 * Deliberately does not persist: the value belongs to the instance, not to the
 * viewer. Writing it to localStorage would make it indistinguishable from a
 * choice the person made, so a later change to the instance setting would
 * never reach anyone who had loaded the app once.
 *
 * Stands down when the person already has a language — stored from a previous
 * visit, or applied from their profile earlier in this session.
 * @param lng The instance default, from the instance configuration.
 * @returns Whether the default was applied.
 */
export async function applyDefaultLanguage(lng: TLanguage | undefined): Promise<boolean> {
  if (!lng) return false;
  if (hasExplicitLanguage || readStoredLanguage()) return false;
  await applyLanguage(lng);
  return true;
}

/**
 * @description Forgets the viewer's language and falls back to the
 * organization's default. Called on sign-out: the next person to reach this
 * browser is not the one who just left, so the sign-in screen should speak the
 * organization's language rather than the previous account's.
 * @param fallback The instance default to land on once the preference is gone.
 */
export async function clearLanguagePreference(fallback: TLanguage): Promise<void> {
  hasExplicitLanguage = false;
  if (typeof window !== "undefined") {
    try {
      localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    } catch {
      // Nothing to clean up if storage is unavailable.
    }
  }
  await applyLanguage(fallback);
}
