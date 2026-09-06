/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import type {
  IAssignmentCandidates,
  IAvailabilityState,
  IAvailabilityWindow,
  IMembershipAllocation,
  IAssignmentDecisionEntry,
  IAssignmentPolicy,
  IAssignmentPolicyResolution,
  IIssueRouting,
  IOrganizationalUnit,
  IOrganizationalUnitAccessChange,
  IOrganizationalUnitCoordinator,
  IOrganizationalUnitMembership,
  IOrganizationalUnitProject,
  IOrganizationalUnitWorkload,
  IUnitQueue,
  IUserOrganizationalUnit,
  TAssignmentPolicyPayload,
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

  // --- the area's queue and the coordinator's actions (item 2.2) ------------

  private itemPath(workspaceSlug: string, projectId: string, issueId: string): string {
    return `/api/orca/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/organizational-unit`;
  }

  /**
   * @description What the area has waiting, overdue first and oldest first,
   * with what the reader is allowed to do about it.
   * @param filters `routing_state` takes a state or `all`; the rest narrow.
   */
  async getQueue(
    workspaceSlug: string,
    unitId: string,
    filters?: { routingState?: string; overdue?: boolean; projectId?: string; executorId?: string; limit?: number }
  ): Promise<IUnitQueue> {
    const params = new URLSearchParams();
    if (filters?.routingState) params.set("routing_state", filters.routingState);
    if (filters?.overdue !== undefined) params.set("overdue", String(filters.overdue));
    if (filters?.projectId) params.set("project", filters.projectId);
    if (filters?.executorId) params.set("executor", filters.executorId);
    if (filters?.limit) params.set("limit", String(filters.limit));
    const query = params.toString();
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/queue/${query ? `?${query}` : ""}`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description The area's decision log, most recent first, each entry
   * carrying the decision it replaced. Coordinators and admins only.
   */
  async getDecisions(
    workspaceSlug: string,
    unitId: string,
    options?: { issueId?: string }
  ): Promise<{ results: IAssignmentDecisionEntry[] }> {
    const query = options?.issueId ? `?issue=${options.issueId}` : "";
    return this.get(`${this.basePath(workspaceSlug)}/${unitId}/decisions/${query}`)
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

  async addCoordinator(
    workspaceSlug: string,
    unitId: string,
    memberId: string
  ): Promise<IOrganizationalUnitCoordinator> {
    return this.post(`${this.basePath(workspaceSlug)}/${unitId}/coordinators/`, { member_id: memberId })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async removeCoordinator(workspaceSlug: string, unitId: string, coordinatorId: string): Promise<void> {
    return this.delete(`${this.basePath(workspaceSlug)}/${unitId}/coordinators/${coordinatorId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /**
   * @description The policy in force for the area, or for one of its projects:
   * resolved, not stored — the project's policy over the area's over the
   * fallback (RFC §6.3). A client reading the raw rows would have to
   * reimplement that precedence and would get it wrong the first time a
   * project policy appeared.
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
   * @description Write the area's policy, or the one that overrides it for a
   * single project. The read is `getPolicy`, which answers with the *resolved*
   * policy instead — project over area over fallback.
   */
  async writePolicy(
    workspaceSlug: string,
    unitId: string,
    data: TAssignmentPolicyPayload,
    projectId?: string
  ): Promise<IAssignmentPolicy> {
    const path = projectId
      ? `${this.basePath(workspaceSlug)}/${unitId}/projects/${projectId}/policy/write/`
      : `${this.basePath(workspaceSlug)}/${unitId}/policy/write/`;
    return this.put(path, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Take a queued item for yourself. */
  async claimIssue(workspaceSlug: string, projectId: string, issueId: string): Promise<{ routing: IIssueRouting }> {
    return this.post(`${this.itemPath(workspaceSlug, projectId, issueId)}/claim/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /**
   * @description Hand the item to somebody else.
   * @param expectedDecisionId What the caller last read, so two coordinators
   * acting at once do not silently overwrite each other.
   */
  async reassignIssue(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    executorId: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ): Promise<{ routing: IIssueRouting }> {
    return this.post(`${this.itemPath(workspaceSlug, projectId, issueId)}/reassign/`, {
      executor_id: executorId,
      ...(options?.reason ? { reason: options.reason } : {}),
      ...(options?.expectedDecisionId ? { expected_decision_id: options.expectedDecisionId } : {}),
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Put the item back in the area's queue. */
  async returnIssueToQueue(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { reason?: string }
  ): Promise<{ routing: IIssueRouting }> {
    return this.post(
      `${this.itemPath(workspaceSlug, projectId, issueId)}/return/`,
      options?.reason ? { reason: options.reason } : {}
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Park an item blocked on something outside the area. */
  async suspendIssue(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { reason?: string }
  ): Promise<{ routing: IIssueRouting }> {
    return this.post(
      `${this.itemPath(workspaceSlug, projectId, issueId)}/suspend/`,
      options?.reason ? { reason: options.reason } : {}
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description Move responsibility for the item to another area. */
  async transferIssueUnit(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    toUnitId: string,
    options?: { reason?: string }
  ): Promise<{ routing: IIssueRouting }> {
    return this.post(`${this.itemPath(workspaceSlug, projectId, issueId)}/transfer/`, {
      organizational_unit_id: toUnitId,
      ...(options?.reason ? { reason: options.reason } : {}),
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /**
   * @description Who could take this item, in the order the allocator would
   * pick them. Read-only: the ranking is recomputed when the decision is made.
   */
  async getCandidates(workspaceSlug: string, projectId: string, issueId: string): Promise<IAssignmentCandidates> {
    return this.get(`${this.itemPath(workspaceSlug, projectId, issueId)}/candidates/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // --- availability and per-area limits (Phase 3) ---------------------------

  /** @description Your own absences, and whether one covers right now. */
  async getMyAvailability(workspaceSlug: string): Promise<IAvailabilityState> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/availability/me/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  /**
   * @description Record an absence for yourself.
   * @param data `unavailable_until` omitted means indefinite, which is allowed
   * and is what long leave looks like.
   */
  async addMyAvailability(
    workspaceSlug: string,
    data: { unavailable_from: string; unavailable_until?: string | null; reason?: string }
  ): Promise<IAvailabilityWindow> {
    return this.post(`/api/orca/workspaces/${workspaceSlug}/availability/me/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async removeMyAvailability(workspaceSlug: string, windowId: string): Promise<void> {
    return this.delete(`/api/orca/workspaces/${workspaceSlug}/availability/me/?id=${windowId}`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /**
   * @description Somebody else's absences. A coordinator of any area they
   * belong to, or a workspace Admin — any area, because an absence is not
   * per-area and demanding the "right" coordinator would leave whoever
   * noticed unable to record it.
   */
  async getMemberAvailability(workspaceSlug: string, workspaceMemberId: string): Promise<IAvailabilityState> {
    return this.get(`/api/orca/workspaces/${workspaceSlug}/members/${workspaceMemberId}/availability/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async addMemberAvailability(
    workspaceSlug: string,
    workspaceMemberId: string,
    data: { unavailable_from: string; unavailable_until?: string | null; reason?: string }
  ): Promise<IAvailabilityWindow> {
    return this.post(`/api/orca/workspaces/${workspaceSlug}/members/${workspaceMemberId}/availability/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async removeMemberAvailability(workspaceSlug: string, workspaceMemberId: string, windowId: string): Promise<void> {
    return this.delete(
      `/api/orca/workspaces/${workspaceSlug}/members/${workspaceMemberId}/availability/?id=${windowId}`
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  /** @description What this area may put on this person. */
  async getMembershipAllocation(
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

  /**
   * @description Write it. Sending `max_open_items` at all requires being a
   * coordinator; anybody may send `accepts_new_work` for themselves.
   */
  async writeMembershipAllocation(
    workspaceSlug: string,
    unitId: string,
    membershipId: string,
    data: { accepts_new_work?: boolean; max_open_items?: number | null }
  ): Promise<IMembershipAllocation> {
    return this.put(`${this.basePath(workspaceSlug)}/${unitId}/members/${membershipId}/allocation/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }
}
