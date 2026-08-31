/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, computed, makeObservable, observable, runInAction } from "mobx";
import type {
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
  // computed
  units: IOrganizationalUnit[];
  // helpers
  getUnitById: (unitId: string) => IOrganizationalUnit | undefined;
  getMembersByUnitId: (unitId: string) => IOrganizationalUnitMembership[];
  getProjectsByUnitId: (unitId: string) => IOrganizationalUnitProject[];
  getWorkloadByUnitId: (unitId: string) => IOrganizationalUnitWorkload[];
  // actions
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
  myUnits: IUserOrganizationalUnit[] | null = null;
  loader = false;

  rootStore: CoreRootStore;
  service: OrganizationalUnitService;

  constructor(_rootStore: CoreRootStore) {
    makeObservable(this, {
      unitMap: observable,
      membershipMap: observable,
      projectMap: observable,
      workloadMap: observable,
      myUnits: observable,
      loader: observable.ref,
      units: computed,
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
    });

    this.rootStore = _rootStore;
    this.service = new OrganizationalUnitService();
  }

  get units(): IOrganizationalUnit[] {
    return Object.values(this.unitMap).toSorted((a, b) => a.name.localeCompare(b.name));
  }

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
}
