/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, computed, makeObservable, observable, runInAction } from "mobx";
import type {
  IIssueRouting,
  IOrganizationalUnit,
  IOrganizationalUnitAccessChange,
  IOrganizationalUnitCoordinator,
  IOrganizationalUnitMembership,
  IOrganizationalUnitProject,
  IOrganizationalUnitWorkload,
  IQueueRow,
  IQueueViewer,
  IUserOrganizationalUnit,
  TOrganizationalUnitAssignMode,
  TOrganizationalUnitMemberRole,
} from "@plane/types";
import { OrganizationalUnitService } from "@/services/orca/organizational-unit.service";
import type { CoreRootStore } from "../root.store";

/**
 * One area's queue as the Work tab holds it: the two lists the tab shows,
 * split here rather than in the component because the split comes from two
 * separate requests, and who the viewer is to this area.
 */
export interface IUnitQueue {
  waiting: IQueueRow[];
  inProgress: IQueueRow[];
  viewer: IQueueViewer;
  loader: boolean;
}

export interface IOrganizationalUnitStore {
  // observables
  unitMap: Record<string, IOrganizationalUnit>;
  membershipMap: Record<string, IOrganizationalUnitMembership[]>;
  projectMap: Record<string, IOrganizationalUnitProject[]>;
  workloadMap: Record<string, IOrganizationalUnitWorkload[]>;
  queueByUnit: Record<string, IUnitQueue>;
  coordinatorMap: Record<string, IOrganizationalUnitCoordinator[]>;
  myUnits: IUserOrganizationalUnit[] | null;
  loader: boolean;
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
  getQueueByUnitId: (unitId: string) => IUnitQueue;
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
  claimIssueRouting: (workspaceSlug: string, projectId: string, issueId: string) => Promise<IIssueRouting>;
  reassignIssueRouting: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    executorId: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ) => Promise<IIssueRouting>;
  returnIssueRouting: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ) => Promise<IIssueRouting>;
  fetchQueue: (workspaceSlug: string, unitId: string) => Promise<IUnitQueue>;
  claim: (workspaceSlug: string, unitId: string, row: IQueueRow) => Promise<IIssueRouting>;
  assign: (workspaceSlug: string, unitId: string, row: IQueueRow, executorId: string) => Promise<IIssueRouting>;
  returnToQueue: (workspaceSlug: string, unitId: string, row: IQueueRow) => Promise<IIssueRouting>;
  fetchCoordinators: (workspaceSlug: string, unitId: string) => Promise<IOrganizationalUnitCoordinator[]>;
}

/**
 * The shape `getQueueByUnitId` hands back for an area nobody has fetched yet.
 * Frozen and shared: it is only ever read, and a fresh object per call would
 * make every observer re-render on every read.
 */
const EMPTY_QUEUE: IUnitQueue = Object.freeze({
  waiting: [],
  inProgress: [],
  viewer: { is_admin: false, is_coordinator: false, is_member: false },
  loader: false,
});

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
  queueByUnit: Record<string, IUnitQueue> = {};
  coordinatorMap: Record<string, IOrganizationalUnitCoordinator[]> = {};
  myUnits: IUserOrganizationalUnit[] | null = null;
  loader = false;
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
      coordinatorMap: observable,
      myUnits: observable,
      loader: observable.ref,
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
      claimIssueRouting: action,
      reassignIssueRouting: action,
      returnIssueRouting: action,
      fetchQueue: action,
      claim: action,
      assign: action,
      returnToQueue: action,
      fetchCoordinators: action,
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

  /**
   * @description The area's queue, or an empty one while it loads. Returns a
   * filled shape rather than `undefined` so the tab never has to guard every
   * read of `waiting`/`inProgress` — an area with no work and an area not yet
   * fetched look the same to a component, and should.
   */
  getQueueByUnitId = (unitId: string) => this.queueByUnit[unitId] ?? EMPTY_QUEUE;

  getCoordinatorsByUnitId = (unitId: string) => this.coordinatorMap[unitId] ?? [];

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
      delete this.queueByUnit[unitId];
      delete this.coordinatorMap[unitId];
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

  /**
   * @description The three routing actions, read straight off the work
   * item's own panel rather than a queue row. `claim`/`assign`/`returnToQueue`
   * below exist for the Work tab and update `queueByUnit` as a side effect;
   * these do not; a panel showing one item has no queue page to keep in sync,
   * so writing to `queueByUnit` here would only ever plant a row nothing else
   * populated.
   */
  claimIssueRouting = (workspaceSlug: string, projectId: string, issueId: string) =>
    this.service.claimIssue(workspaceSlug, projectId, issueId);

  reassignIssueRouting = (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    executorId: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ) => this.service.reassignIssue(workspaceSlug, projectId, issueId, executorId, options);

  returnIssueRouting = (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    options?: { reason?: string; expectedDecisionId?: string | null }
  ) => this.service.returnIssue(workspaceSlug, projectId, issueId, options);

  /**
   * @description The two lists the Work tab shows, in two requests: what is
   * waiting on somebody (the endpoint's default) and what somebody is already
   * doing. Both are asked for together so the tab never shows a fresh inbox
   * beside a stale "in progress".
   *
   * The backend orders overdue first and the store keeps that order, so the
   * row a coordinator owes stays at the top of the list they are reading.
   * @param unitId The area whose queue to load.
   * @returns The stored queue for that area.
   */
  fetchQueue = async (workspaceSlug: string, unitId: string) => {
    runInAction(() => {
      this.queueByUnit[unitId] = { ...this.getQueueByUnitId(unitId), loader: true };
    });
    try {
      const [waitingPage, inProgressPage] = await Promise.all([
        this.service.getQueue(workspaceSlug, unitId),
        this.service.getQueue(workspaceSlug, unitId, { routing_state: "assigned" }),
      ]);
      const queue: IUnitQueue = {
        waiting: waitingPage.results ?? [],
        inProgress: inProgressPage.results ?? [],
        // Both pages carry the same viewer; the waiting one is the page the
        // tab is built around, so it wins if they ever disagree.
        viewer: waitingPage.viewer ?? inProgressPage.viewer ?? EMPTY_QUEUE.viewer,
        loader: false,
      };
      runInAction(() => {
        this.queueByUnit[unitId] = queue;
      });
      return queue;
    } catch (error) {
      runInAction(() => {
        this.queueByUnit[unitId] = { ...this.getQueueByUnitId(unitId), loader: false };
      });
      throw error;
    }
  };

  /**
   * @description Rebuilds a queue row from the routing the API returned, which
   * is the authority on where the item now stands. Everything the routing does
   * not carry — the work item's name, project, state — is unchanged by an
   * allocation, so it is kept from the row the person clicked.
   *
   * `primary_executor` arrives as a bare user id; the workspace member store
   * supplies the name and avatar the row shows. When it cannot (the person is
   * not loaded), the row keeps the id so the next fetch corrects it rather
   * than showing the wrong person.
   * @param row The row as the interface had it.
   * @param routing What the API says about the item now.
   * @returns The row to put back in the list.
   */
  private mergeRoutingIntoRow = (row: IQueueRow, routing: IIssueRouting): IQueueRow => {
    const executorId = routing.primary_executor;
    const details = executorId ? this.rootStore.memberRoot.workspace.getWorkspaceMemberDetails(executorId) : null;
    return {
      ...row,
      routing_state: routing.routing_state,
      queue_reason: routing.queue_reason,
      queued_at: routing.queued_at,
      assignment_due_at: routing.assignment_due_at,
      // A just-allocated item cannot be late on an allocation it no longer
      // owes, and one just returned starts its wait over.
      assignment_overdue: false,
      age_seconds: 0,
      primary_executor: executorId
        ? {
            id: executorId,
            display_name: details?.member?.display_name ?? row.primary_executor?.display_name ?? "",
            email: details?.member?.email ?? row.primary_executor?.email ?? "",
            avatar_url: details?.member?.avatar_url ?? row.primary_executor?.avatar_url ?? "",
          }
        : null,
      current_decision_id: routing.current_assignment_decision?.id ?? null,
    };
  };

  /**
   * @description Moves a row to the list its new routing state puts it in, and
   * drops it from the other. `assigned` is work in progress; everything else —
   * queued, allocation_failed, suspended — is still waiting on somebody.
   */
  private placeRow = (unitId: string, row: IQueueRow) => {
    const queue = this.getQueueByUnitId(unitId);
    const withoutRow = (rows: IQueueRow[]) => rows.filter((entry) => entry.issue_id !== row.issue_id);
    const goesToInProgress = row.routing_state === "assigned";
    runInAction(() => {
      this.queueByUnit[unitId] = {
        ...queue,
        waiting: goesToInProgress ? withoutRow(queue.waiting) : [row, ...withoutRow(queue.waiting)],
        inProgress: goesToInProgress ? [row, ...withoutRow(queue.inProgress)] : withoutRow(queue.inProgress),
      };
    });
  };

  /**
   * @description Takes the item for the person clicking. Rethrows so the
   * component can name the reason the API gave — "somebody beat you to it" and
   * "this area does not let you claim" are different problems to the person
   * holding the mouse.
   */
  claim = async (workspaceSlug: string, unitId: string, row: IQueueRow) => {
    const routing = await this.service.claimIssue(workspaceSlug, row.project.id, row.issue_id);
    this.placeRow(unitId, this.mergeRoutingIntoRow(row, routing));
    return routing;
  };

  assign = async (workspaceSlug: string, unitId: string, row: IQueueRow, executorId: string) => {
    const routing = await this.service.reassignIssue(workspaceSlug, row.project.id, row.issue_id, executorId, {
      expectedDecisionId: row.current_decision_id,
    });
    this.placeRow(unitId, this.mergeRoutingIntoRow(row, routing));
    return routing;
  };

  returnToQueue = async (workspaceSlug: string, unitId: string, row: IQueueRow) => {
    const routing = await this.service.returnIssue(workspaceSlug, row.project.id, row.issue_id, {
      expectedDecisionId: row.current_decision_id,
    });
    this.placeRow(unitId, this.mergeRoutingIntoRow(row, routing));
    return routing;
  };

  fetchCoordinators = async (workspaceSlug: string, unitId: string) => {
    const response = await this.service.getCoordinators(workspaceSlug, unitId);
    runInAction(() => {
      this.coordinatorMap[unitId] = response;
    });
    return response;
  };
}
