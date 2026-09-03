/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import type {
  IAssignmentCandidate,
  IAssignmentDecision,
  IAssignmentPolicy,
  IIssueRouting,
  IUnitCoordinator,
  IUnitQueueRow,
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

  // --- the area's queue -------------------------------------------------

  /**
   * @description What is waiting in an area. Rows carry what the caller may do
   * with them, so the interface never has to work out the policy itself.
   */
  async getUnitQueue(
    workspaceSlug: string,
    unitId: string,
    filters?: { routing_state?: string; project?: string; executor?: string; overdue?: boolean }
  ): Promise<IUnitQueueRow[]> {
    const query = new URLSearchParams();
    if (filters?.routing_state) query.set("routing_state", filters.routing_state);
    if (filters?.project) query.set("project", filters.project);
    if (filters?.executor) query.set("executor", filters.executor);
    if (filters?.overdue) query.set("overdue", "true");
    const suffix = query.toString() ? `?${query.toString()}` : "";

    return this.get(`/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/queue/${suffix}`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description The decisions taken in an area, newest first. */
  async getUnitDecisions(workspaceSlug: string, unitId: string, issueId?: string): Promise<IAssignmentDecision[]> {
    const suffix = issueId ? `?issue=${issueId}` : "";
    return this.get(`/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/decisions/${suffix}`)
      .then((response) => response?.data?.results ?? response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Who an area could hand this work item to, and how loaded they are. */
  async getAssignmentCandidates(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<{ effective_mode: string; candidates: IAssignmentCandidate[]; excluded: IAssignmentCandidate[] }> {
    return this.get(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/candidates/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Take a queued work item for yourself. */
  async claimIssue(workspaceSlug: string, projectId: string, issueId: string) {
    return this.post(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/claim/`,
      {}
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /**
   * @description Give a work item to a member of the area.
   * @param expectedDecisionId The decision the caller was looking at — the API
   * refuses if somebody moved the work first, rather than undoing them.
   */
  async assignIssueTo(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    primaryExecutor: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ) {
    return this.post(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/assign-to/`,
      {
        primary_executor: primaryExecutor,
        reason: options?.reason ?? "",
        ...(options?.expectedDecisionId ? { expected_decision_id: options.expectedDecisionId } : {}),
      }
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Hand a work item back to its area's queue. */
  async returnIssueToQueue(workspaceSlug: string, projectId: string, issueId: string, reason = "") {
    return this.post(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/return/`,
      { reason }
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Move responsibility for a work item to another area. */
  async transferIssueUnit(workspaceSlug: string, projectId: string, issueId: string, unitId: string, reason = "") {
    return this.post(
      `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit/transfer/`,
      { organizational_unit_id: unitId, reason }
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  // --- policy and coordinators -------------------------------------------

  /** @description The policy an area applies, resolved for the area or one project. */
  async getResolvedPolicy(workspaceSlug: string, unitId: string, projectId?: string) {
    const path = projectId
      ? `/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/projects/${projectId}/policy/`
      : `/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/policy/`;
    return this.get(path)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Set how an area assigns work. Admin only. */
  async setPolicy(
    workspaceSlug: string,
    unitId: string,
    policy: Partial<IAssignmentPolicy>,
    projectId?: string
  ): Promise<IAssignmentPolicy> {
    const path = projectId
      ? `/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/projects/${projectId}/policy/write/`
      : `/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/policy/write/`;
    return this.put(path, policy)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Who runs this area's queue. */
  async getCoordinators(workspaceSlug: string, unitId: string): Promise<IUnitCoordinator[]> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/coordinators/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Make somebody a coordinator of this area. Admin only. */
  async addCoordinator(workspaceSlug: string, unitId: string, workspaceMemberId: string) {
    return this.post(`/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/coordinators/`, {
      workspace_member: workspaceMemberId,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Withdraw somebody's coordination. Admin only. */
  async removeCoordinator(workspaceSlug: string, unitId: string, coordinatorId: string) {
    return this.delete(
      `/api/orca/workspaces/${workspaceSlug}/organizational-units/${unitId}/coordinators/${coordinatorId}/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }
}
