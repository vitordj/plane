/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import type {
  IIssueRouting,
  IOrganizationalUnit,
  IOrganizationalUnitAccessChange,
  IOrganizationalUnitMembership,
  IOrganizationalUnitProject,
  IOrganizationalUnitWorkload,
  IUserOrganizationalUnit,
  TOrganizationalUnitMemberRole,
  TOrganizationalUnitAssignMode,
} from "@plane/types";
import { APIService } from "@/services/api.service";

/**
 * @description Client for the Orca organizational layer, served under the
 * fork's own /api/orca/ namespace (see FORK.md). Mutations require workspace
 * admin; reads are available to any workspace member.
 */
export class OrganizationalUnitService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private basePath(workspaceSlug: string): string {
    return `/api/orca/workspaces/${workspaceSlug}/organizational-units`;
  }

  /**
   * @description Which Orca features this instance has switched on. Served
   * outside the organizational-units kill switch on purpose: the UI has to be
   * able to ask whether the layer exists in order to hide it, which it could
   * not do through an endpoint the same switch makes invisible.
   */
  async getOrcaConfig(workspaceSlug: string): Promise<{ organizational_units_enabled: boolean }> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/config/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getOrganizationalUnits(workspaceSlug: string): Promise<IOrganizationalUnit[]> {
    return this.get(`${this.basePath(workspaceSlug)}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getOrganizationalUnit(workspaceSlug: string, unitId: string): Promise<IOrganizationalUnit> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createOrganizationalUnit(
    workspaceSlug: string,
    data: Partial<IOrganizationalUnit>
  ): Promise<IOrganizationalUnit> {
    return this.post(`${this.basePath(workspaceSlug)}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async updateOrganizationalUnit(
    workspaceSlug: string,
    unitId: string,
    data: Partial<IOrganizationalUnit>
  ): Promise<IOrganizationalUnit> {
    return this.patch(`${this.basePath(workspaceSlug)}/${unitId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async deleteOrganizationalUnit(workspaceSlug: string, unitId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug)}/${unitId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async getMembers(workspaceSlug: string, unitId: string): Promise<IOrganizationalUnitMembership[]> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/members/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async addMembers(
    workspaceSlug: string,
    unitId: string,
    workspaceMemberIds: string[],
    role: TOrganizationalUnitMemberRole = "member"
  ): Promise<IOrganizationalUnitMembership[]> {
    return this.post(`${this.basePath(workspaceSlug)}/${unitId}/members/`, {
      workspace_member_ids: workspaceMemberIds,
      role,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async updateMember(
    workspaceSlug: string,
    unitId: string,
    membershipId: string,
    data: Partial<IOrganizationalUnitMembership>
  ): Promise<IOrganizationalUnitMembership> {
    return this.patch(`${this.basePath(workspaceSlug)}/${unitId}/members/${membershipId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async removeMember(workspaceSlug: string, unitId: string, membershipId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug)}/${unitId}/members/${membershipId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async getProjects(workspaceSlug: string, unitId: string): Promise<IOrganizationalUnitProject[]> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/projects/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async linkProject(
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    defaultRole: number
  ): Promise<IOrganizationalUnitProject> {
    return this.post(`${this.basePath(workspaceSlug)}/${unitId}/projects/`, {
      project_id: projectId,
      default_role: defaultRole,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async updateLinkedProject(
    workspaceSlug: string,
    unitId: string,
    linkId: string,
    data: Partial<IOrganizationalUnitProject>
  ): Promise<IOrganizationalUnitProject> {
    return this.patch(`${this.basePath(workspaceSlug)}/${unitId}/projects/${linkId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async unlinkProject(workspaceSlug: string, unitId: string, linkId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug)}/${unitId}/projects/${linkId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /**
   * @description Read-only preview of what reconciliation would change. Never
   * writes, so it is safe to call while an admin is still editing.
   */
  async getEffectiveAccess(
    workspaceSlug: string,
    unitId: string
  ): Promise<{ changes: IOrganizationalUnitAccessChange[] }> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/effective-access/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getWorkload(workspaceSlug: string, unitId: string): Promise<IOrganizationalUnitWorkload[]> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/workload/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getMyOrganizationalUnits(workspaceSlug: string): Promise<IUserOrganizationalUnit[]> {
    return this.get(`${this.basePath(workspaceSlug)}/me/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getIssueOrganizationalUnit(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<{ organizational_unit: IOrganizationalUnit | null; routing: IIssueRouting | null }> {
    return this.get(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async setIssueOrganizationalUnit(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    unitId: string
  ): Promise<{ organizational_unit: IOrganizationalUnit }> {
    return this.post(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/`,
      { organizational_unit_id: unitId }
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async clearIssueOrganizationalUnit(workspaceSlug: string, projectId: string, issueId: string): Promise<void> {
    return this.delete(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /**
   * @description Assigns the least-loaded eligible member of the responsible
   * unit. Never replaces existing assignees.
   */
  async assignFromOrganizationalUnit(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { unitId?: string; mode?: TOrganizationalUnitAssignMode }
  ): Promise<{ assigned: { user_id: string; open_issues: number } | null; reason: string }> {
    return this.post(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit-assign/`,
      {
        ...(options?.unitId ? { organizational_unit_id: options.unitId } : {}),
        ...(options?.mode ? { mode: options.mode } : {}),
      }
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }
}
