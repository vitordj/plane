/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
// components
import { AvailabilityForm } from "@/components/orca/organizational-units/availability-form";
import { ProfileSettingsHeading } from "@/components/settings/profile/heading";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUser, useUserProfile } from "@/hooks/store/user";
// local imports
import { ProfileSettingsDefaultPreferencesList } from "./default-list";
import { ProfileSettingsLanguageAndTimezonePreferencesList } from "./language-and-timezone-list";

export const PreferencesProfileSettings = observer(function PreferencesProfileSettings() {
  const { t } = useTranslation();
  // hooks
  const { data: userProfile } = useUserProfile();
  const { data: currentUser } = useUser();
  const { currentWorkspace } = useWorkspace();
  const {
    workspace: { getWorkspaceMemberDetails },
  } = useMember();
  const store = useOrganizationalUnit();

  useEffect(() => {
    if (currentWorkspace?.slug) store.fetchConfig(currentWorkspace.slug);
  }, [currentWorkspace?.slug, store]);

  if (!userProfile) return null;

  const workspaceSlug = currentWorkspace?.slug;
  const workspaceMemberId = currentUser?.id ? (getWorkspaceMemberDetails(currentUser.id)?.id ?? "me") : "me";

  return (
    <div className="size-full">
      <ProfileSettingsHeading
        title={t("account_settings.preferences.heading")}
        description={t("account_settings.preferences.description")}
      />
      <div className="mt-7 flex w-full flex-col gap-6">
        <section>
          <ProfileSettingsDefaultPreferencesList />
        </section>
        <section className="flex flex-col gap-y-3">
          <div className="text-h6-medium text-primary">{t("language_and_time")}</div>
          <ProfileSettingsLanguageAndTimezonePreferencesList />
        </section>
        {store.availabilityEnabled && workspaceSlug && (
          <section className="flex flex-col gap-y-3">
            <AvailabilityForm workspaceSlug={workspaceSlug} workspaceMemberId={workspaceMemberId} forSelf />
          </section>
        )}
      </div>
    </div>
  );
});
