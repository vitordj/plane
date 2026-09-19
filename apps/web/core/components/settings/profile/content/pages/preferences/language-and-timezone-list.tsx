/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { SUPPORTED_LANGUAGES, useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { CustomSelect } from "@plane/ui";
// components
import { TimezoneSelect } from "@/components/global";
import { StartOfWeekPreference } from "@/components/profile/start-of-week-preference";
import { SettingsControlItem } from "@/components/settings/control-item";
// hooks
import { useInstance } from "@/hooks/store/use-instance";
import { useUser, useUserProfile } from "@/hooks/store/user";
// services
import { LanguagePreferenceService } from "@/services/orca/language-preference.service";

/**
 * Orca (fork): the picker's first entry, standing for "whatever the
 * organization uses" rather than for a language. Not a locale code, so it can
 * never collide with one.
 */
const FOLLOW_ORGANIZATION = "__organization_default__";

const languagePreferenceService = new LanguagePreferenceService();

export const ProfileSettingsLanguageAndTimezonePreferencesList = observer(
  function ProfileSettingsLanguageAndTimezonePreferencesList() {
    // store hooks
    const {
      data: user,
      updateCurrentUser,
      userProfile: { data: profile },
    } = useUser();
    const { updateUserProfile, fetchUserProfile } = useUserProfile();
    const { config } = useInstance();
    // translation
    const { t } = useTranslation();

    // Orca (fork): whether this person's language is theirs or the
    // organization's. Undefined until the first fetch answers, which keeps the
    // picker from flickering between the two states on load.
    const [followsOrganization, setFollowsOrganization] = useState<boolean | undefined>(undefined);

    const loadPreference = useCallback(async () => {
      try {
        const preference = await languagePreferenceService.retrieve();
        setFollowsOrganization(preference.follows_organization_default);
      } catch {
        // Falling back to "chose their own" shows the language they are
        // actually reading in, which is the honest thing to show when we
        // cannot tell where it came from.
        setFollowsOrganization(false);
      }
    }, []);

    useEffect(() => {
      void loadPreference();
    }, [loadPreference]);

    const handleTimezoneChange = async (value: string) => {
      try {
        await updateCurrentUser({ user_timezone: value });
        setToast({
          title: "Success!",
          message: "Timezone updated successfully",
          type: TOAST_TYPE.SUCCESS,
        });
      } catch (_error) {
        setToast({
          title: "Error!",
          message: "Failed to update timezone",
          type: TOAST_TYPE.ERROR,
        });
      }
    };

    const handleLanguageChange = async (value: string) => {
      try {
        if (value === FOLLOW_ORGANIZATION) {
          // Hands the choice back. The API also moves this person onto the
          // current default, so the profile has to be re-read.
          await languagePreferenceService.update(true);
          await fetchUserProfile();
          setFollowsOrganization(true);
        } else {
          // Changing the language is what marks it as this person's own; the
          // API records that from the change itself.
          await updateUserProfile({ language: value });
          setFollowsOrganization(false);
        }
        setToast({
          title: t("common.success"),
          message: t("language_updated"),
          type: TOAST_TYPE.SUCCESS,
        });
      } catch (_error) {
        setToast({
          title: t("language_not_updated"),
          message: t("something_went_wrong_please_try_again"),
          type: TOAST_TYPE.ERROR,
        });
      }
    };

    const getLanguageLabel = (value: string) => {
      const selectedLanguage = SUPPORTED_LANGUAGES.find((l) => l.value === value);
      if (!selectedLanguage) return value;
      return selectedLanguage.label;
    };

    // Orca (fork): the organization's default, and how this person stands
    // relative to it.
    const organizationDefault = config?.default_language;
    const followOrganizationLabel = organizationDefault
      ? t("language_follow_organization_named", { language: getLanguageLabel(organizationDefault) })
      : t("language_follow_organization");

    const selectedValue = followsOrganization ? FOLLOW_ORGANIZATION : profile?.language;
    const selectedLabel = followsOrganization
      ? followOrganizationLabel
      : profile?.language
        ? getLanguageLabel(profile.language)
        : t("language_select_placeholder");

    // Say which of the two states they are in, rather than repeating the
    // generic line. Silent until the fetch answers.
    const stateHint =
      followsOrganization === undefined
        ? undefined
        : followsOrganization
          ? t("language_following_organization")
          : organizationDefault && organizationDefault !== profile?.language
            ? t("language_organization_default", { language: getLanguageLabel(organizationDefault) })
            : undefined;

    return (
      <div className="flex flex-col gap-y-1">
        <SettingsControlItem
          title={t("timezone")}
          description={t("timezone_setting")}
          control={<TimezoneSelect value={user?.user_timezone || "Asia/Kolkata"} onChange={handleTimezoneChange} />}
        />
        <SettingsControlItem
          title={t("language")}
          description={stateHint ? `${t("language_setting")} ${stateHint}` : t("language_setting")}
          control={
            <CustomSelect
              value={selectedValue}
              label={selectedLabel}
              onChange={handleLanguageChange}
              buttonClassName="border border-subtle-1"
              className="rounded-md"
              input
              placement="bottom-end"
            >
              {/* Orca (fork): the way back to following the organization. Only
                  offered once the instance has told us what its default is. */}
              {organizationDefault && (
                <CustomSelect.Option value={FOLLOW_ORGANIZATION}>{followOrganizationLabel}</CustomSelect.Option>
              )}
              {SUPPORTED_LANGUAGES.map((item) => (
                <CustomSelect.Option key={item.value} value={item.value}>
                  {item.label}
                </CustomSelect.Option>
              ))}
            </CustomSelect>
          }
        />
        <StartOfWeekPreference
          option={{
            title: "First day of the week",
            description: "This will change how all calendars in your app look.",
          }}
        />
      </div>
    );
  }
);
