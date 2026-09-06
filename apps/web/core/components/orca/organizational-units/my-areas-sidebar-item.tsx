/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { Users } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { joinUrlPath } from "@plane/utils";
// components
import { SidebarNavItem } from "@/components/sidebar/sidebar-navigation";
// hooks
import { useAppTheme } from "@/hooks/store/use-app-theme";
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The sidebar's way into "My areas".
 *
 * Rendered only for somebody who belongs to at least one area: for everybody
 * else the page would be an empty screen, and the sidebar is not the place to
 * advertise a feature the workspace may not use. It is a fork addition and
 * deliberately not part of `WORKSPACE_SIDEBAR_*`, whose items carry navigation
 * preferences and pinning that upstream owns — keeping it separate is what
 * makes the next upstream sync a no-op here.
 */
export const MyAreasSidebarItem = observer(function MyAreasSidebarItem() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const { workspaceSlug } = useParams();
  const store = useOrganizationalUnit();
  const { toggleSidebar, isExtendedSidebarOpened, toggleExtendedSidebar } = useAppTheme();

  const slug = workspaceSlug?.toString() || "";

  useEffect(() => {
    if (!slug || store.myUnits !== null) return;
    // Once per workspace: the answer decides whether this entry exists at all,
    // and it changes only when somebody is added to or removed from an area.
    store.fetchMyUnits(slug).catch(() => undefined);
  }, [slug, store]);

  if (!slug || !store.isEnabled) return null;
  if (!store.myUnits || store.myUnits.length === 0) return null;

  const href = joinUrlPath(slug, "my-areas");

  const handleLinkClick = () => {
    if (window.innerWidth < 768) toggleSidebar();
    if (isExtendedSidebarOpened) toggleExtendedSidebar(false);
  };

  return (
    <Link href={href} onClick={handleLinkClick}>
      <SidebarNavItem isActive={pathname?.includes("/my-areas")}>
        <div className="flex items-center gap-1.5 py-[1px]">
          <Users className="size-4 flex-shrink-0" />
          <p className="text-13 leading-5 font-medium">{t(`${OU}.my_areas.title`)}</p>
        </div>
      </SidebarNavItem>
    </Link>
  );
});
