/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

/**
 * Whether the signed-in person's interface language is their own choice or the
 * organization's.
 */
export type TUserLanguagePreference = {
  follows_organization_default: boolean;
  organization_default_language: string;
};

/**
 * @description Client for the signed-in person's language preference, served
 * under the fork's own /api/orca/ namespace (see FORK.md).
 *
 * `Profile.language` says which language somebody reads in; it cannot say
 * whether that was their decision. This is the sidecar that can, and the only
 * way back to following the organization once you have picked something.
 */
export class LanguagePreferenceService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private readonly path = "/api/orca/users/me/language-preference/";

  async retrieve(): Promise<TUserLanguagePreference> {
    return this.get(this.path)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @param followsOrganizationDefault `true` also moves the person onto the
   * current default — saying "use whatever the organization uses" and staying
   * on yesterday's language would be a switch that does nothing.
   */
  async update(followsOrganizationDefault: boolean): Promise<TUserLanguagePreference> {
    return this.patch(this.path, { follows_organization_default: followsOrganizationDefault })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
