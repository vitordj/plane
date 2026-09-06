/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import path from "node:path";
import { defineConfig } from "vitest/config";

/**
 * Vitest for the web app's own logic — stores, helpers, anything that decides
 * something without a DOM.
 *
 * Deliberately a `node` environment and deliberately not the app's
 * `vite.config.ts`: that config loads the React Router plugin, which wants a
 * route manifest and a browser. A store is a plain object with methods, and
 * testing one should not need either.
 *
 * The alias list is the subset of `tsconfig.json`'s paths that a store can
 * reach. Kept explicit rather than through `vite-tsconfig-paths` so a test
 * that starts pulling in the whole app fails here, visibly, instead of
 * quietly loading half of it.
 */
export default defineConfig({
  test: {
    environment: "node",
    globals: true,
    include: ["core/**/*.test.ts", "helpers/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@/services": path.resolve(__dirname, "./core/services"),
      "@/store": path.resolve(__dirname, "./core/store"),
      "@": path.resolve(__dirname, "./core"),
    },
  },
});
