/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import path from "node:path";
import { defineConfig } from "vitest/config";

/**
 * Vitest for the web app's own logic and for components rendered on their own.
 *
 * Deliberately not the app's `vite.config.ts`: that config loads the React
 * Router plugin, which wants a route manifest and a browser. A store is a
 * plain object with methods, and a presentational component is a function of
 * its props; testing either should need neither.
 *
 * Two environments, because the two kinds of test want different things and a
 * single `jsdom` project would pay for a DOM in every store test:
 *
 * * **logic** — `node`, for stores and helpers, matched by `*.test.ts`;
 * * **components** — `jsdom`, for `*.test.tsx`.
 *
 * The alias list is the subset of `tsconfig.json`'s paths these tests may
 * reach. Kept explicit rather than through `vite-tsconfig-paths` so a test
 * that starts pulling in the whole app fails here, visibly, instead of
 * quietly loading half of it.
 */
const alias = {
  "@/services": path.resolve(__dirname, "./core/services"),
  "@/store": path.resolve(__dirname, "./core/store"),
  "@": path.resolve(__dirname, "./core"),
};

export default defineConfig({
  test: {
    projects: [
      {
        resolve: { alias },
        test: {
          name: "logic",
          environment: "node",
          globals: true,
          include: ["core/**/*.test.ts", "helpers/**/*.test.ts"],
        },
      },
      {
        resolve: { alias },
        test: {
          name: "components",
          environment: "jsdom",
          globals: true,
          include: ["core/**/*.test.tsx"],
          setupFiles: ["./vitest.setup.ts"],
        },
      },
    ],
  },
  resolve: { alias },
});
