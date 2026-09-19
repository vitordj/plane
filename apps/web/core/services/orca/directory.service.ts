/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import type {
  IDirectoryConnection,
  IDirectoryConnectionWithToken,
  IDirectoryIdentity,
  IDirectorySyncSummary,
} from "@plane/types";
import { APIService } from "@/services/api.service";

/**
 * @description Client for the workspace's directory (SCIM) connection, served
 * under the fork's own /api/orca/ namespace (see FORK.md). Every endpoint here
 * is workspace-admin only: issuing a SCIM token hands a machine the power to
 * grant project access through areas.
 */
export class DirectoryService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private basePath(workspaceSlug: string): string {
    return `/api/orca/workspaces/${workspaceSlug}/directory`;
  }

  async getConnection(workspaceSlug: string): Promise<IDirectoryConnection> {
    return this.get(`${this.basePath(workspaceSlug)}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateConnection(workspaceSlug: string, data: Partial<IDirectoryConnection>): Promise<IDirectoryConnection> {
    return this.patch(`${this.basePath(workspaceSlug)}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Mint a new SCIM token. The plain token is in the response and
   * cannot be read back afterwards, so the caller must show it immediately.
   * Any previously issued token stops working at once.
   */
  async issueToken(workspaceSlug: string): Promise<IDirectoryConnectionWithToken> {
    return this.post(`${this.basePath(workspaceSlug)}/token/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async revokeToken(workspaceSlug: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug)}/token/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Replay the stored directory data onto the areas. Calls nothing
   * external — it is the repair pass for identities that arrived before the
   * person was a workspace member.
   */
  async resync(workspaceSlug: string): Promise<IDirectorySyncSummary> {
    return this.post(`${this.basePath(workspaceSlug)}/resync/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getUnresolvedIdentities(workspaceSlug: string): Promise<IDirectoryIdentity[]> {
    return this.get(`${this.basePath(workspaceSlug)}/unresolved/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
