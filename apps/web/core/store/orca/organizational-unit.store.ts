/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, computed, makeObservable, observable, runInAction } from "mobx";
import type {
  IAssignmentCandidates,
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
  IQueueCapabilities,
  IQueueItem,
  IUserOrganizationalUnit,
  TAssignmentPolicyPayload,
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
  queueByUnit: Record<string, IQueueItem[]>;
  capabilitiesByUnit: Record<string, IQueueCapabilities>;
  decisionsByUnit: Record<string, IAssignmentDecisionEntry[]>;
  coordinatorsByUnit: Record<string, IOrganizationalUnitCoordinator[]>;
  myUnits: IUserOrganizationalUnit[] | null;
  loader: boolean;
  queueLoader: boolean;
  /** `null` until the config endpoint answers; see `isEnabled`. */
  featureEnabled: boolean | null;
  // computed
  units: IOrganizationalUnit[];
  isEnabled: boolean;
  // helpers
  getUnitById: (unitId: string) => IOrganizationalUnit | undefined;
  getMembersByUnitId: (unitId: string) => IOrganizationalUnitMembership[];
  getProjectsByUnitId: (unitId: string) => IOrganizationalUnitProject[];
  getWorkloadByUnitId: (unitId: string) => IOrganizationalUnitWorkload[];
  getQueueByUnitId: (unitId: string) => IQueueItem[];
  getCapabilitiesByUnitId: (unitId: string) => IQueueCapabilities;
  getDecisionsByUnitId: (unitId: string) => IAssignmentDecisionEntry[];
  getCoordinatorsByUnitId: (unitId: string) => IOrganizationalUnitCoordinator[];
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
  ) => Promise<{
    assigned: { user_id: string; open_issues: number; last_assigned_at: string | null } | null;
    reason: string;
    routing: IIssueRouting | null;
  }>;
  fetchIssueUnit: (
    workspaceSlug: string,
    projectId: string,
    issueId: string
  ) => Promise<{ unit: IOrganizationalUnit | null; routing: IIssueRouting | null }>;
  setIssueUnit: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    unitId: string
  ) => Promise<{ unit: IOrganizationalUnit; routing: IIssueRouting | null }>;
  clearIssueUnit: (workspaceSlug: string, projectId: string, issueId: string) => Promise<void>;
  // the area's queue and the coordinator's actions (item 2.3)
  fetchQueue: (
    workspaceSlug: string,
    unitId: string,
    filters?: { routingState?: string; overdue?: boolean; projectId?: string; executorId?: string }
  ) => Promise<IQueueItem[]>;
  fetchDecisions: (workspaceSlug: string, unitId: string) => Promise<IAssignmentDecisionEntry[]>;
  fetchCoordinators: (workspaceSlug: string, unitId: string) => Promise<IOrganizationalUnitCoordinator[]>;
  addCoordinator: (workspaceSlug: string, unitId: string, memberId: string) => Promise<void>;
  removeCoordinator: (workspaceSlug: string, unitId: string, coordinatorId: string) => Promise<void>;
  fetchPolicy: (workspaceSlug: string, unitId: string, projectId?: string) => Promise<IAssignmentPolicyResolution>;
  writePolicy: (
    workspaceSlug: string,
    unitId: string,
    data: TAssignmentPolicyPayload,
    projectId?: string
  ) => Promise<IAssignmentPolicy>;
  claim: (workspaceSlug: string, projectId: string, issueId: string, unitId?: string) => Promise<IIssueRouting>;
  assign: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    executorId: string,
    options?: { unitId?: string; expectedDecisionId?: string | null; reason?: string }
  ) => Promise<IIssueRouting>;
  returnToQueue: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { unitId?: string; reason?: string }
  ) => Promise<IIssueRouting>;
  suspendIssue: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { unitId?: string; reason?: string }
  ) => Promise<IIssueRouting>;
  transferIssue: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    toUnitId: string,
    options?: { unitId?: string; reason?: string }
  ) => Promise<IIssueRouting>;
  fetchCandidates: (workspaceSlug: string, projectId: string, issueId: string) => Promise<IAssignmentCandidates>;
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
  queueByUnit: Record<string, IQueueItem[]> = {};
  capabilitiesByUnit: Record<string, IQueueCapabilities> = {};
  decisionsByUnit: Record<string, IAssignmentDecisionEntry[]> = {};
  coordinatorsByUnit: Record<string, IOrganizationalUnitCoordinator[]> = {};
  myUnits: IUserOrganizationalUnit[] | null = null;
  loader = false;
  queueLoader = false;
  featureEnabled: boolean | null = null;

  rootStore: CoreRootStore;
  service: OrganizationalUnitService;

  constructor(_rootStore: CoreRootStore) {
    makeObservable(this, {
      unitMap: observable,
      membershipMap: observable,
      projectMap: observable,
      workloadMap: observable,
      queueByUnit: observable,
      capabilitiesByUnit: observable,
      decisionsByUnit: observable,
      coordinatorsByUnit: observable,
      myUnits: observable,
      loader: observable.ref,
      queueLoader: observable.ref,
      featureEnabled: observable.ref,
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
      fetchDecisions: action,
      fetchCoordinators: action,
      addCoordinator: action,
      removeCoordinator: action,
      writePolicy: action,
      claim: action,
      assign: action,
      returnToQueue: action,
      suspendIssue: action,
      transferIssue: action,
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
      });
      return enabled;
    } catch {
      // An unreachable config endpoint says nothing about the feature, so keep
      // the default rather than hiding a layer that may well be on.
      runInAction(() => {
        this.featureEnabled = true;
      });
      return true;
    }
  };

  getUnitById = (unitId: string) => this.unitMap[unitId];

  getMembersByUnitId = (unitId: string) => this.membershipMap[unitId] ?? [];

  getProjectsByUnitId = (unitId: string) => this.projectMap[unitId] ?? [];

  getWorkloadByUnitId = (unitId: string) => this.workloadMap[unitId] ?? [];

  getQueueByUnitId = (unitId: string) => this.queueByUnit[unitId] ?? [];

  /**
   * @description What the reader may do with this area's queue. Nothing until
   * the queue has been read once: assuming a capability the server has not
   * granted would draw a button whose only outcome is a 403.
   */
  getCapabilitiesByUnitId = (unitId: string) =>
    this.capabilitiesByUnit[unitId] ?? { can_claim: false, can_assign: false, can_return: false };

  getDecisionsByUnitId = (unitId: string) => this.decisionsByUnit[unitId] ?? [];

  getCoordinatorsByUnitId = (unitId: string) => this.coordinatorsByUnit[unitId] ?? [];

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

  /**
   * @description The responsible area and, with it, where the item stands:
   * queued for a coordinator, waiting for a claim, or assigned. The two travel
   * together because marking an area is what applies its policy — showing the
   * area without the outcome is how "I set the area, why is nobody on it?"
   * happens.
   */
  fetchIssueUnit = async (workspaceSlug: string, projectId: string, issueId: string) => {
    const response = await this.service.getIssueOrganizationalUnit(workspaceSlug, projectId, issueId);
    return { unit: response.organizational_unit ?? null, routing: response.routing ?? null };
  };

  setIssueUnit = async (workspaceSlug: string, projectId: string, issueId: string, unitId: string) => {
    const response = await this.service.setIssueOrganizationalUnit(workspaceSlug, projectId, issueId, unitId);
    return { unit: response.organizational_unit, routing: response.routing ?? null };
  };

  clearIssueUnit = async (workspaceSlug: string, projectId: string, issueId: string) =>
    this.service.clearIssueOrganizationalUnit(workspaceSlug, projectId, issueId);

  // --- the area's queue and the coordinator's actions (item 2.3) ------------

  /**
   * @description Read the area's queue and remember what the reader may do
   * with it. The filters are the server's, not this store's: which rows come
   * back and in what order is a decision the API owns, so two surfaces reading
   * the same queue cannot disagree about what "waiting" means.
   */
  fetchQueue = async (
    workspaceSlug: string,
    unitId: string,
    filters?: { routingState?: string; overdue?: boolean; projectId?: string; executorId?: string }
  ) => {
    this.queueLoader = true;
    try {
      const response = await this.service.getQueue(workspaceSlug, unitId, filters);
      runInAction(() => {
        this.queueByUnit[unitId] = response.items;
        this.capabilitiesByUnit[unitId] = response.capabilities;
        this.queueLoader = false;
      });
      return response.items;
    } catch (error) {
      runInAction(() => {
        this.queueLoader = false;
      });
      throw error;
    }
  };

  fetchDecisions = async (workspaceSlug: string, unitId: string) => {
    const response = await this.service.getDecisions(workspaceSlug, unitId);
    runInAction(() => {
      this.decisionsByUnit[unitId] = response.results ?? [];
    });
    return this.decisionsByUnit[unitId];
  };

  fetchCoordinators = async (workspaceSlug: string, unitId: string) => {
    const response = await this.service.getCoordinators(workspaceSlug, unitId);
    runInAction(() => {
      this.coordinatorsByUnit[unitId] = response;
    });
    return response;
  };

  addCoordinator = async (workspaceSlug: string, unitId: string, memberId: string) => {
    await this.service.addCoordinator(workspaceSlug, unitId, memberId);
    // Refetched rather than patched in: appointing a coordinator reconciles
    // native project access on the server, and the server's answer is the
    // truth about what that left behind.
    await this.fetchCoordinators(workspaceSlug, unitId);
  };

  removeCoordinator = async (workspaceSlug: string, unitId: string, coordinatorId: string) => {
    await this.service.removeCoordinator(workspaceSlug, unitId, coordinatorId);
    await this.fetchCoordinators(workspaceSlug, unitId);
  };

  /**
   * @description The resolved policy, not the stored row: what *would* happen
   * in this area, or in this project of it. Not cached, because the settings
   * form reads it to fill itself in and a stale answer there would silently
   * rewrite a policy somebody else just changed.
   */
  fetchPolicy = async (workspaceSlug: string, unitId: string, projectId?: string) =>
    this.service.getPolicy(workspaceSlug, unitId, projectId);

  writePolicy = async (workspaceSlug: string, unitId: string, data: TAssignmentPolicyPayload, projectId?: string) =>
    this.service.writePolicy(workspaceSlug, unitId, data, projectId);

  /**
   * @description Each action answers with the item's new routing state, and
   * each one refreshes the queue of the area it belongs to when the caller
   * says which — an action taken from the queue has to leave the queue right.
   */
  private refreshQueue = async (workspaceSlug: string, unitId?: string) => {
    if (!unitId) return;
    try {
      await this.fetchQueue(workspaceSlug, unitId);
    } catch {
      // A stale list is a worse outcome than a failed refresh, but not one
      // worth failing the action the person just took: they saw it succeed.
    }
  };

  claim = async (workspaceSlug: string, projectId: string, issueId: string, unitId?: string) => {
    const response = await this.service.claimIssue(workspaceSlug, projectId, issueId);
    await this.refreshQueue(workspaceSlug, unitId);
    return response.routing;
  };

  assign = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    executorId: string,
    options?: { unitId?: string; expectedDecisionId?: string | null; reason?: string }
  ) => {
    const response = await this.service.reassignIssue(workspaceSlug, projectId, issueId, executorId, {
      reason: options?.reason,
      expectedDecisionId: options?.expectedDecisionId,
    });
    await this.refreshQueue(workspaceSlug, options?.unitId);
    return response.routing;
  };

  returnToQueue = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { unitId?: string; reason?: string }
  ) => {
    const response = await this.service.returnIssueToQueue(workspaceSlug, projectId, issueId, {
      reason: options?.reason,
    });
    await this.refreshQueue(workspaceSlug, options?.unitId);
    return response.routing;
  };

  suspendIssue = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { unitId?: string; reason?: string }
  ) => {
    const response = await this.service.suspendIssue(workspaceSlug, projectId, issueId, {
      reason: options?.reason,
    });
    await this.refreshQueue(workspaceSlug, options?.unitId);
    return response.routing;
  };

  transferIssue = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    toUnitId: string,
    options?: { unitId?: string; reason?: string }
  ) => {
    const response = await this.service.transferIssueUnit(workspaceSlug, projectId, issueId, toUnitId, {
      reason: options?.reason,
    });
    await this.refreshQueue(workspaceSlug, options?.unitId);
    return response.routing;
  };

  fetchCandidates = async (workspaceSlug: string, projectId: string, issueId: string) =>
    this.service.getCandidates(workspaceSlug, projectId, issueId);
}
