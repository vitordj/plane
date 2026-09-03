/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// components
import { PageHead } from "@/components/core/page-title";
import { MyAreasRoot } from "@/components/orca/organizational-units/my-areas-root";
import type { Route } from "./+types/page";

function MyAreasPage({ params }: Route.ComponentProps) {
  const { workspaceSlug } = params;

  return (
    <>
      <PageHead title="My areas" />
      <div className="relative h-full w-full overflow-hidden overflow-y-auto p-6">
        <MyAreasRoot workspaceSlug={workspaceSlug} />
      </div>
    </>
  );
}

export default MyAreasPage;
