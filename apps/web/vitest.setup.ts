/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * Unmount whatever a test rendered, so the next one starts on an empty
 * document. Without this, `getByText` finds the previous test's markup and the
 * failure it produces points at the wrong test.
 */
afterEach(() => {
  cleanup();
});
