/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import type {
  IAssignmentCandidate,
  IAssignmentPolicyPayload,
  IAssignmentPolicyResolution,
  IAssignmentDecisionDetail,
  IIssueRouting,
  IMemberAvailability,
  IMembershipAllocation,
  IOrganizationalUnit,
  IOrganizationalUnitAccessChange,
  IOrganizationalUnitCoordinator,
  IOrganizationalUnitMembership,
  IOrganizationalUnitProject,
  IOrganizationalUnitWorkload,
  IQueuePage,
  IUserOrganizationalUnit,
  TPaginatedResponse,
  TOrganizationalUnitMemberRole,
  TOrganizationalUnitAssignMode,
  TRoutingState,
  IExecutiveDrillDown,
  IExecutiveReport,
  TExecutiveMetric,
  TExecutivePeriod,
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
  async getOrcaConfig(workspaceSlug: string): Promise<{
    organizational_units_enabled: boolean;
    public_api_enabled?: boolean;
    availability_enabled?: boolean;
  }> {
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
  ): Promise<{ organizational_unit: IOrganizationalUnit; routing: IIssueRouting | null }> {
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
  ): Promise<{
    assigned: { user_id: string; open_issues: number; last_assigned_at: string | null } | null;
    reason: string;
    routing: IIssueRouting | null;
  }> {
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

  private issuePath(workspaceSlug: string, projectId: string, issueId: string): string {
    return `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit`;
  }

  /**
   * @description One page of an area's queue. The backend orders overdue
   * first, so the interface never re-sorts: two sort orders for the same list
   * is how a coordinator stops trusting either.
   * @param params `routing_state` (a state or `all`), `overdue`, `project`,
   * `executor`, plus the native `cursor`/`per_page`.
   * @returns The paginated envelope, with `viewer` alongside `results`.
   */
  async getQueue(
    workspaceSlug: string,
    unitId: string,
    params?: {
      routing_state?: TRoutingState | "all";
      overdue?: boolean;
      project?: string;
      executor?: string;
      cursor?: string;
      per_page?: number;
    }
  ): Promise<IQueuePage> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/queue/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Takes the work item for the caller. */
  async claimIssue(workspaceSlug: string, projectId: string, issueId: string): Promise<IIssueRouting> {
    return this.post(`${this.issuePath(workspaceSlug, projectId, issueId)}/claim/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Hands the work item to a named person, as a coordinator.
   * @param expectedDecisionId The decision the interface was looking at. The
   * API rejects the call with `ORG_DECISION_STALE` when someone else has
   * allocated the item since, rather than silently overwriting them.
   */
  async reassignIssue(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    executorId: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ): Promise<IIssueRouting> {
    return this.post(`${this.issuePath(workspaceSlug, projectId, issueId)}/reassign/`, {
      executor_id: executorId,
      ...(options?.reason ? { reason: options.reason } : {}),
      ...(options?.expectedDecisionId ? { expected_decision_id: options.expectedDecisionId } : {}),
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Puts an assigned work item back in the area's queue. */
  async returnIssue(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ): Promise<IIssueRouting> {
    return this.post(`${this.issuePath(workspaceSlug, projectId, issueId)}/return/`, {
      ...(options?.reason ? { reason: options.reason } : {}),
      ...(options?.expectedDecisionId ? { expected_decision_id: options.expectedDecisionId } : {}),
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getCoordinators(workspaceSlug: string, unitId: string): Promise<IOrganizationalUnitCoordinator[]> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/coordinators/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Makes a workspace member a coordinator of this area. The
   * server reconciles their project access as part of the same request, so no
   * separate grant call follows.
   */
  async addCoordinator(
    workspaceSlug: string,
    unitId: string,
    workspaceMemberId: string
  ): Promise<IOrganizationalUnitCoordinator> {
    return this.post(`${this.basePath(workspaceSlug)}/${unitId}/coordinators/`, {
      workspace_member_id: workspaceMemberId,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async removeCoordinator(workspaceSlug: string, unitId: string, coordinatorId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug)}/${unitId}/coordinators/${coordinatorId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description The area's allocation log, newest first. Coordinator-only:
   * it names who was and was not chosen. ``supersedes`` is expanded one level.
   */
  async getDecisions(
    workspaceSlug: string,
    unitId: string,
    params?: { issue?: string; cursor?: string; per_page?: number }
  ): Promise<TPaginatedResponse<IAssignmentDecisionDetail[]>> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/decisions/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Moves responsibility to another area. Gated by coordination
   * of the *origin* area: moving work away is that area's call, not the
   * destination's.
   */
  async transferIssue(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    unitId: string,
    options?: { reason?: string }
  ): Promise<IIssueRouting> {
    return this.post(`${this.issuePath(workspaceSlug, projectId, issueId)}/transfer/`, {
      unit_id: unitId,
      ...(options?.reason ? { reason: options.reason } : {}),
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description The assignment policy in force, resolved for one project
   * when ``projectId`` is given. Without this a form cannot tell whether the
   * area even allows self-claim.
   */
  async getPolicy(workspaceSlug: string, unitId: string, projectId?: string): Promise<IAssignmentPolicyResolution> {
    const path = projectId
      ? `${this.basePath(workspaceSlug)}/${unitId}/projects/${projectId}/policy/`
      : `${this.basePath(workspaceSlug)}/${unitId}/policy/`;
    return this.get(path)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Sets the assignment policy for an area, or for one of its
   * projects when `projectId` is given — the per-project policy is what lets
   * one project self-claim while the rest of the area waits on a coordinator.
   */
  async updatePolicy(
    workspaceSlug: string,
    unitId: string,
    data: IAssignmentPolicyPayload,
    projectId?: string
  ): Promise<IAssignmentPolicyResolution> {
    const path = projectId
      ? `${this.basePath(workspaceSlug)}/${unitId}/projects/${projectId}/policy/`
      : `${this.basePath(workspaceSlug)}/${unitId}/policy/`;
    return this.put(path, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Who an area could hand this work item to, and how loaded they
   * are. Backs the suggestion on rows the availability sweep handed back.
   */
  async getAssignmentCandidates(
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ): Promise<{
    effective_mode: string;
    candidates: IAssignmentCandidate[];
    excluded: IAssignmentCandidate[];
  }> {
    return this.get(`${this.issuePath(workspaceSlug, projectId, issueId)}/candidates/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // --- availability -------------------------------------------------------

  /** @description The requesting person's own absences. */
  async getMyAvailability(workspaceSlug: string): Promise<IMemberAvailability[]> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/availability/me/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Record an absence for oneself. */
  async addMyAvailability(
    workspaceSlug: string,
    payload: { unavailable_from: string; unavailable_until?: string | null; reason?: string }
  ): Promise<IMemberAvailability> {
    return this.post(`/api/orca/workspaces/${workspaceSlug}/availability/me/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Remove one of one's own absences. */
  async removeMyAvailability(workspaceSlug: string, availabilityId: string): Promise<void> {
    return this.delete(`/api/orca/workspaces/${workspaceSlug}/availability/me/${availabilityId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Somebody else's absences — coordinator of one of their areas, or admin. */
  async getMemberAvailability(workspaceSlug: string, workspaceMemberId: string): Promise<IMemberAvailability[]> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/members/${workspaceMemberId}/availability/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Record an absence for somebody else. */
  async addMemberAvailability(
    workspaceSlug: string,
    workspaceMemberId: string,
    payload: { unavailable_from: string; unavailable_until?: string | null; reason?: string }
  ): Promise<IMemberAvailability> {
    return this.post(`/api/orca/workspaces/${workspaceSlug}/members/${workspaceMemberId}/availability/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Remove somebody else's absence. */
  async removeMemberAvailability(
    workspaceSlug: string,
    workspaceMemberId: string,
    availabilityId: string
  ): Promise<void> {
    return this.delete(
      `/api/orca/workspaces/${workspaceSlug}/members/${workspaceMemberId}/availability/${availabilityId}/`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description How much work this area gives this person. */
  async getAllocationSettings(
    workspaceSlug: string,
    unitId: string,
    membershipId: string
  ): Promise<IMembershipAllocation> {
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/members/${membershipId}/allocation/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /** @description Set it. The ceiling is the coordinator's; the switch is the person's own. */
  async setAllocationSettings(
    workspaceSlug: string,
    unitId: string,
    membershipId: string,
    payload: Partial<IMembershipAllocation>
  ): Promise<IMembershipAllocation> {
    return this.put(`${this.basePath(workspaceSlug)}/${unitId}/members/${membershipId}/allocation/`, payload)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Workspace-admin aggregates by area and process. Counts
   * include items in projects the reader cannot open; each area's
   * `hidden_count` says how many.
   */
  async getExecutive(
    workspaceSlug: string,
    params?: { period?: TExecutivePeriod; unit?: string }
  ): Promise<IExecutiveReport> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/executive/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description The list behind one cell of the executive table. Items in
   * projects the reader does not belong to are omitted and counted in
   * `hidden_count`.
   */
  async getExecutiveDrillDown(
    workspaceSlug: string,
    params: { unit: string; metric: TExecutiveMetric; period?: TExecutivePeriod }
  ): Promise<IExecutiveDrillDown> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/executive/drill-down/`, { params })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
