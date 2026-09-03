/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, computed, makeObservable, observable, runInAction } from "mobx";
import type {
  IAssignmentCandidate,
  IAssignmentDecision,
  IIssueRouting,
  IExecutiveSummary,
  IMemberAvailability,
  IMembershipAllocation,
  IUnitQueueRow,
  IOrganizationalUnit,
  IOrganizationalUnitAccessChange,
  IOrganizationalUnitMembership,
  IOrganizationalUnitProject,
  IOrganizationalUnitWorkload,
  IUserOrganizationalUnit,
  TOrganizationalUnitAssignMode,
  TOrganizationalUnitMemberRole,
} from "@plane/types";
import { OrganizationalUnitService } from "@/services/orca/organizational-unit.service";
import type { CoreRootStore } from "../root.store";

export interface IOrganizationalUnitStore {
  // observables
  unitMap: Record<string, IOrganizationalUnit>;
  membershipMap: Record<string, IOrganizationalUnitMembership[]>;
  projectMap: Record<string, IOrganizationalUnitProject[]>;
  workloadMap: Record<string, IOrganizationalUnitWorkload[]>;
  myUnits: IUserOrganizationalUnit[] | null;
  loader: boolean;
  /** `null` until the config endpoint answers; see `isEnabled`. */
  featureEnabled: boolean | null;
  /** `null` until the config endpoint answers; availability is off by default. */
  availabilityEnabled: boolean | null;
  /** Absences by workspace member id, only for the people actually looked at. */
  availabilityByMember: Record<string, IMemberAvailability[]>;
  // computed
  units: IOrganizationalUnit[];
  isEnabled: boolean;
  // helpers
  getUnitById: (unitId: string) => IOrganizationalUnit | undefined;
  getMembersByUnitId: (unitId: string) => IOrganizationalUnitMembership[];
  getProjectsByUnitId: (unitId: string) => IOrganizationalUnitProject[];
  getWorkloadByUnitId: (unitId: string) => IOrganizationalUnitWorkload[];
  // actions
  fetchConfig: (workspaceSlug: string) => Promise<boolean>;
  fetchUnits: (workspaceSlug: string) => Promise<IOrganizationalUnit[]>;
  createUnit: (workspaceSlug: string, data: Partial<IOrganizationalUnit>) => Promise<IOrganizationalUnit>;
  updateUnit: (
    workspaceSlug: string,
    unitId: string,
    data: Partial<IOrganizationalUnit>
  ) => Promise<IOrganizationalUnit>;
  deleteUnit: (workspaceSlug: string, unitId: string) => Promise<void>;
  fetchMembers: (workspaceSlug: string, unitId: string) => Promise<IOrganizationalUnitMembership[]>;
  addMembers: (
    workspaceSlug: string,
    unitId: string,
    workspaceMemberIds: string[],
    role?: TOrganizationalUnitMemberRole
  ) => Promise<IOrganizationalUnitMembership[]>;
  updateMemberRole: (
    workspaceSlug: string,
    unitId: string,
    membershipId: string,
    role: TOrganizationalUnitMemberRole
  ) => Promise<IOrganizationalUnitMembership>;
  removeMember: (workspaceSlug: string, unitId: string, membershipId: string) => Promise<void>;
  fetchProjects: (workspaceSlug: string, unitId: string) => Promise<IOrganizationalUnitProject[]>;
  linkProject: (
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    defaultRole: number
  ) => Promise<IOrganizationalUnitProject>;
  updateLinkedProjectRole: (
    workspaceSlug: string,
    unitId: string,
    linkId: string,
    defaultRole: number
  ) => Promise<IOrganizationalUnitProject>;
  unlinkProject: (workspaceSlug: string, unitId: string, linkId: string) => Promise<void>;
  fetchEffectiveAccess: (workspaceSlug: string, unitId: string) => Promise<IOrganizationalUnitAccessChange[]>;
  fetchWorkload: (workspaceSlug: string, unitId: string) => Promise<IOrganizationalUnitWorkload[]>;
  fetchMyUnits: (workspaceSlug: string) => Promise<IUserOrganizationalUnit[]>;
  assignIssueFromUnit: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { unitId?: string; mode?: TOrganizationalUnitAssignMode }
  ) => Promise<{ assigned: { user_id: string; open_issues: number } | null; reason: string }>;
  fetchIssueUnit: (workspaceSlug: string, projectId: string, issueId: string) => Promise<IOrganizationalUnit | null>;
  /** The queue state of a work item: who is executing it, and why it waits. */
  fetchIssueRouting: (workspaceSlug: string, projectId: string, issueId: string) => Promise<IIssueRouting | null>;
  // --- the area's queue --------------------------------------------------
  /** Rows per area, so a queue survives navigating away and back. */
  queueByUnit: Record<string, IUnitQueueRow[]>;
  getQueueByUnitId: (unitId: string) => IUnitQueueRow[];
  fetchQueue: (
    workspaceSlug: string,
    unitId: string,
    filters?: { routing_state?: string; project?: string; executor?: string; overdue?: boolean }
  ) => Promise<IUnitQueueRow[]>;
  fetchDecisions: (workspaceSlug: string, unitId: string, issueId?: string) => Promise<IAssignmentDecision[]>;
  fetchCandidates: (
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ) => Promise<{ effective_mode: string; candidates: IAssignmentCandidate[]; excluded: IAssignmentCandidate[] }>;
  claim: (workspaceSlug: string, unitId: string, projectId: string, issueId: string) => Promise<void>;
  assignTo: (
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    issueId: string,
    primaryExecutor: string,
    expectedDecisionId?: string | null,
    /** Free text kept on the decision — "accepted_suggestion", for instance. */
    reason?: string
  ) => Promise<void>;
  returnToQueue: (
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    issueId: string,
    reason?: string
  ) => Promise<void>;
  transferUnit: (
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    issueId: string,
    destinationUnitId: string,
    reason?: string
  ) => Promise<void>;
  getAvailabilityByMemberId: (workspaceMemberId: string) => IMemberAvailability[];
  fetchMyAvailability: (workspaceSlug: string, workspaceMemberId: string) => Promise<IMemberAvailability[]>;
  fetchMemberAvailability: (workspaceSlug: string, workspaceMemberId: string) => Promise<IMemberAvailability[]>;
  addAvailability: (
    workspaceSlug: string,
    workspaceMemberId: string,
    payload: { unavailable_from: string; unavailable_until?: string | null; reason?: string },
    forSelf?: boolean
  ) => Promise<void>;
  removeAvailability: (
    workspaceSlug: string,
    workspaceMemberId: string,
    availabilityId: string,
    forSelf?: boolean
  ) => Promise<void>;
  setAllocationSettings: (
    workspaceSlug: string,
    unitId: string,
    membershipId: string,
    payload: Partial<IMembershipAllocation>
  ) => Promise<IMembershipAllocation>;
  fetchExecutiveSummary: (workspaceSlug: string, period: string, unitId?: string) => Promise<IExecutiveSummary>;
  setIssueUnit: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    unitId: string
  ) => Promise<IOrganizationalUnit>;
  clearIssueUnit: (workspaceSlug: string, projectId: string, issueId: string) => Promise<void>;
}

/**
 * @description Store for the Orca organizational layer. Membership and project
 * links change who can access what, so every mutation refetches the affected
 * collection rather than patching it locally — the server reconciles access as
 * part of the same request, and its result is the truth.
 */
export class OrganizationalUnitStore implements IOrganizationalUnitStore {
  unitMap: Record<string, IOrganizationalUnit> = {};
  membershipMap: Record<string, IOrganizationalUnitMembership[]> = {};
  projectMap: Record<string, IOrganizationalUnitProject[]> = {};
  workloadMap: Record<string, IOrganizationalUnitWorkload[]> = {};
  queueByUnit: Record<string, IUnitQueueRow[]> = {};
  myUnits: IUserOrganizationalUnit[] | null = null;
  loader = false;
  featureEnabled: boolean | null = null;
  availabilityEnabled: boolean | null = null;
  availabilityByMember: Record<string, IMemberAvailability[]> = {};

  rootStore: CoreRootStore;
  service: OrganizationalUnitService;

  constructor(_rootStore: CoreRootStore) {
    makeObservable(this, {
      unitMap: observable,
      membershipMap: observable,
      projectMap: observable,
      workloadMap: observable,
      queueByUnit: observable,
      myUnits: observable,
      loader: observable.ref,
      featureEnabled: observable.ref,
      availabilityEnabled: observable.ref,
      availabilityByMember: observable,
      units: computed,
      isEnabled: computed,
      fetchConfig: action,
      fetchUnits: action,
      createUnit: action,
      updateUnit: action,
      deleteUnit: action,
      fetchMembers: action,
      addMembers: action,
      updateMemberRole: action,
      removeMember: action,
      fetchProjects: action,
      linkProject: action,
      updateLinkedProjectRole: action,
      unlinkProject: action,
      fetchEffectiveAccess: action,
      fetchWorkload: action,
      fetchMyUnits: action,
      assignIssueFromUnit: action,
      fetchQueue: action,
      claim: action,
      assignTo: action,
      returnToQueue: action,
      transferUnit: action,
      fetchMyAvailability: action,
      fetchMemberAvailability: action,
      addAvailability: action,
      removeAvailability: action,
      setAllocationSettings: action,
      fetchExecutiveSummary: action,
    });

    this.rootStore = _rootStore;
    this.service = new OrganizationalUnitService();
  }

  get units(): IOrganizationalUnit[] {
    return Object.values(this.unitMap).toSorted((a, b) => a.name.localeCompare(b.name));
  }

  /**
   * @description Whether to render the organizational layer at all. Optimistic
   * while `featureEnabled` is still `null`: the layer is on by default, so
   * assuming "off" before the config lands would make the UI flicker the
   * section away on every load. A disabled instance answers 404 on every
   * organizational route anyway, so a brief optimistic render cannot leak
   * anything.
   */
  get isEnabled(): boolean {
    return this.featureEnabled !== false;
  }

  fetchConfig = async (workspaceSlug: string) => {
    try {
      const response = await this.service.getOrcaConfig(workspaceSlug);
      const enabled = response?.organizational_units_enabled ?? true;
      runInAction(() => {
        this.featureEnabled = enabled;
        // Availability is off by default, so an absent field means off — the
        // opposite of the layer's own switch, and deliberately so: showing a
        // form nothing reads is worse than not showing one.
        this.availabilityEnabled = response?.availability_enabled ?? false;
      });
      return enabled;
    } catch {
      // An unreachable config endpoint says nothing about the feature, so keep
      // the default rather than hiding a layer that may well be on.
      runInAction(() => {
        this.featureEnabled = true;
        this.availabilityEnabled = false;
      });
      return true;
    }
  };

  getUnitById = (unitId: string) => this.unitMap[unitId];

  getMembersByUnitId = (unitId: string) => this.membershipMap[unitId] ?? [];

  getProjectsByUnitId = (unitId: string) => this.projectMap[unitId] ?? [];

  getWorkloadByUnitId = (unitId: string) => this.workloadMap[unitId] ?? [];

  fetchUnits = async (workspaceSlug: string) => {
    this.loader = true;
    try {
      const response = await this.service.getOrganizationalUnits(workspaceSlug);
      runInAction(() => {
        this.unitMap = response.reduce<Record<string, IOrganizationalUnit>>((map, unit) => {
          map[unit.id] = unit;
          return map;
        }, {});
        this.loader = false;
      });
      return response;
    } catch (error) {
      runInAction(() => {
        this.loader = false;
      });
      throw error;
    }
  };

  createUnit = async (workspaceSlug: string, data: Partial<IOrganizationalUnit>) => {
    const response = await this.service.createOrganizationalUnit(workspaceSlug, data);
    runInAction(() => {
      this.unitMap[response.id] = response;
    });
    return response;
  };

  updateUnit = async (workspaceSlug: string, unitId: string, data: Partial<IOrganizationalUnit>) => {
    const response = await this.service.updateOrganizationalUnit(workspaceSlug, unitId, data);
    runInAction(() => {
      this.unitMap[unitId] = { ...this.unitMap[unitId], ...response };
    });
    return response;
  };

  deleteUnit = async (workspaceSlug: string, unitId: string) => {
    await this.service.deleteOrganizationalUnit(workspaceSlug, unitId);
    runInAction(() => {
      delete this.unitMap[unitId];
      delete this.membershipMap[unitId];
      delete this.projectMap[unitId];
      delete this.workloadMap[unitId];
    });
  };

  fetchMembers = async (workspaceSlug: string, unitId: string) => {
    const response = await this.service.getMembers(workspaceSlug, unitId);
    runInAction(() => {
      this.membershipMap[unitId] = response;
    });
    return response;
  };

  addMembers = async (
    workspaceSlug: string,
    unitId: string,
    workspaceMemberIds: string[],
    role: TOrganizationalUnitMemberRole = "member"
  ) => {
    const response = await this.service.addMembers(workspaceSlug, unitId, workspaceMemberIds, role);
    await Promise.all([this.fetchMembers(workspaceSlug, unitId), this.fetchUnits(workspaceSlug)]);
    return response;
  };

  updateMemberRole = async (
    workspaceSlug: string,
    unitId: string,
    membershipId: string,
    role: TOrganizationalUnitMemberRole
  ) => {
    const response = await this.service.updateMember(workspaceSlug, unitId, membershipId, { role });
    await this.fetchMembers(workspaceSlug, unitId);
    return response;
  };

  removeMember = async (workspaceSlug: string, unitId: string, membershipId: string) => {
    await this.service.removeMember(workspaceSlug, unitId, membershipId);
    await Promise.all([this.fetchMembers(workspaceSlug, unitId), this.fetchUnits(workspaceSlug)]);
  };

  fetchProjects = async (workspaceSlug: string, unitId: string) => {
    const response = await this.service.getProjects(workspaceSlug, unitId);
    runInAction(() => {
      this.projectMap[unitId] = response;
    });
    return response;
  };

  linkProject = async (workspaceSlug: string, unitId: string, projectId: string, defaultRole: number) => {
    const response = await this.service.linkProject(workspaceSlug, unitId, projectId, defaultRole);
    await Promise.all([this.fetchProjects(workspaceSlug, unitId), this.fetchUnits(workspaceSlug)]);
    return response;
  };

  updateLinkedProjectRole = async (workspaceSlug: string, unitId: string, linkId: string, defaultRole: number) => {
    const response = await this.service.updateLinkedProject(workspaceSlug, unitId, linkId, {
      default_role: defaultRole,
    });
    await this.fetchProjects(workspaceSlug, unitId);
    return response;
  };

  unlinkProject = async (workspaceSlug: string, unitId: string, linkId: string) => {
    await this.service.unlinkProject(workspaceSlug, unitId, linkId);
    await Promise.all([this.fetchProjects(workspaceSlug, unitId), this.fetchUnits(workspaceSlug)]);
  };

  fetchEffectiveAccess = async (workspaceSlug: string, unitId: string) => {
    const response = await this.service.getEffectiveAccess(workspaceSlug, unitId);
    return response.changes;
  };

  fetchWorkload = async (workspaceSlug: string, unitId: string) => {
    const response = await this.service.getWorkload(workspaceSlug, unitId);
    runInAction(() => {
      this.workloadMap[unitId] = response;
    });
    return response;
  };

  fetchMyUnits = async (workspaceSlug: string) => {
    const response = await this.service.getMyOrganizationalUnits(workspaceSlug);
    runInAction(() => {
      this.myUnits = response;
    });
    return response;
  };

  assignIssueFromUnit = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { unitId?: string; mode?: TOrganizationalUnitAssignMode }
  ) => this.service.assignFromOrganizationalUnit(workspaceSlug, projectId, issueId, options);

  fetchIssueUnit = async (workspaceSlug: string, projectId: string, issueId: string) => {
    const response = await this.service.getIssueOrganizationalUnit(workspaceSlug, projectId, issueId);
    return response.organizational_unit ?? null;
  };

  fetchIssueRouting = async (workspaceSlug: string, projectId: string, issueId: string) => {
    const response = await this.service.getIssueOrganizationalUnit(workspaceSlug, projectId, issueId);
    return response.routing ?? null;
  };

  setIssueUnit = async (workspaceSlug: string, projectId: string, issueId: string, unitId: string) => {
    const response = await this.service.setIssueOrganizationalUnit(workspaceSlug, projectId, issueId, unitId);
    return response.organizational_unit;
  };

  clearIssueUnit = async (workspaceSlug: string, projectId: string, issueId: string) =>
    this.service.clearIssueOrganizationalUnit(workspaceSlug, projectId, issueId);

  // --- the area's queue ---------------------------------------------------

  getQueueByUnitId = (unitId: string): IUnitQueueRow[] => this.queueByUnit[unitId] ?? [];

  fetchQueue = async (
    workspaceSlug: string,
    unitId: string,
    filters?: { routing_state?: string; project?: string; executor?: string; overdue?: boolean }
  ) => {
    const rows = await this.service.getUnitQueue(workspaceSlug, unitId, filters);
    runInAction(() => {
      this.queueByUnit[unitId] = rows;
    });
    return rows;
  };

  fetchDecisions = async (workspaceSlug: string, unitId: string, issueId?: string) =>
    this.service.getUnitDecisions(workspaceSlug, unitId, issueId);

  fetchCandidates = async (workspaceSlug: string, projectId: string, issueId: string) =>
    this.service.getAssignmentCandidates(workspaceSlug, projectId, issueId);

  /**
   * @description Every action refetches the queue instead of patching the row.
   * Assigning changes what everybody else may do with it, and the server is
   * the only thing that knows — a locally patched row would show a stale set
   * of actions to whoever is looking at the same queue.
   */
  claim = async (workspaceSlug: string, unitId: string, projectId: string, issueId: string) => {
    await this.service.claimIssue(workspaceSlug, projectId, issueId);
    await this.fetchQueue(workspaceSlug, unitId);
  };

  assignTo = async (
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    issueId: string,
    primaryExecutor: string,
    expectedDecisionId?: string | null,
    reason?: string
  ) => {
    await this.service.assignIssueTo(workspaceSlug, projectId, issueId, primaryExecutor, {
      expectedDecisionId,
      reason,
    });
    await this.fetchQueue(workspaceSlug, unitId);
  };

  returnToQueue = async (
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    issueId: string,
    reason = ""
  ) => {
    await this.service.returnIssueToQueue(workspaceSlug, projectId, issueId, reason);
    await this.fetchQueue(workspaceSlug, unitId);
  };

  transferUnit = async (
    workspaceSlug: string,
    unitId: string,
    projectId: string,
    issueId: string,
    destinationUnitId: string,
    reason = ""
  ) => {
    await this.service.transferIssueUnit(workspaceSlug, projectId, issueId, destinationUnitId, reason);
    await this.fetchQueue(workspaceSlug, unitId);
  };

  // --- availability -------------------------------------------------------

  getAvailabilityByMemberId = (workspaceMemberId: string) => this.availabilityByMember[workspaceMemberId] ?? [];

  /**
   * @description One's own absences. Takes the workspace member id purely to
   * key the same map the coordinator view fills — the endpoint it calls is the
   * one that only ever answers about the caller.
   */
  fetchMyAvailability = async (workspaceSlug: string, workspaceMemberId: string) => {
    const response = await this.service.getMyAvailability(workspaceSlug);
    runInAction(() => {
      this.availabilityByMember[workspaceMemberId] = response;
    });
    return response;
  };

  fetchMemberAvailability = async (workspaceSlug: string, workspaceMemberId: string) => {
    const response = await this.service.getMemberAvailability(workspaceSlug, workspaceMemberId);
    runInAction(() => {
      this.availabilityByMember[workspaceMemberId] = response;
    });
    return response;
  };

  /**
   * @description Record an absence. `forSelf` picks the route rather than the
   * permission: the "me" route is the one somebody with no coordinator rights
   * can use, so a person editing their own row must not be sent through the
   * other one.
   */
  addAvailability = async (
    workspaceSlug: string,
    workspaceMemberId: string,
    payload: { unavailable_from: string; unavailable_until?: string | null; reason?: string },
    forSelf = false
  ) => {
    if (forSelf) await this.service.addMyAvailability(workspaceSlug, payload);
    else await this.service.addMemberAvailability(workspaceSlug, workspaceMemberId, payload);
    if (forSelf) await this.fetchMyAvailability(workspaceSlug, workspaceMemberId);
    else await this.fetchMemberAvailability(workspaceSlug, workspaceMemberId);
  };

  removeAvailability = async (
    workspaceSlug: string,
    workspaceMemberId: string,
    availabilityId: string,
    forSelf = false
  ) => {
    if (forSelf) await this.service.removeMyAvailability(workspaceSlug, availabilityId);
    else await this.service.removeMemberAvailability(workspaceSlug, workspaceMemberId, availabilityId);
    if (forSelf) await this.fetchMyAvailability(workspaceSlug, workspaceMemberId);
    else await this.fetchMemberAvailability(workspaceSlug, workspaceMemberId);
  };

  setAllocationSettings = async (
    workspaceSlug: string,
    unitId: string,
    membershipId: string,
    payload: Partial<IMembershipAllocation>
  ) => {
    const response = await this.service.setAllocationSettings(workspaceSlug, unitId, membershipId, payload);
    // The ranking reads these, so anything showing a queue is now stale.
    await this.fetchMembers(workspaceSlug, unitId);
    return response;
  };

  /**
   * @description Not cached in the store: the server caches it for five
   * minutes, and a second copy here would only make "why is this stale?" have
   * two possible answers.
   */
  fetchExecutiveSummary = async (workspaceSlug: string, period: string, unitId?: string) =>
    this.service.getExecutiveSummary(workspaceSlug, period, unitId);
}
