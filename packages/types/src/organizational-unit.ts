/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Types for the Orca organizational layer: units (areas, squads, committees)
 * that group workspace members and grant them project access through the
 * native ProjectMember mechanism. See FORK.md and docs/organizational-units.md.
 */

/** Role a person holds inside a unit — governs the unit, not its projects. */
export type TOrganizationalUnitMemberRole = "lead" | "member";

/** Assignment behavior when a unit is asked to take a work item. */
export type TOrganizationalUnitAssignMode = "fill_empty" | "append";

/** What reconciliation would do, or did, for one person on one project. */
export type TOrganizationalUnitAccessAction =
  | "none"
  | "create"
  | "reactivate"
  | "elevate"
  | "lower"
  | "restore_baseline"
  | "deactivate"
  | "skip_manual_drift";

export interface IOrganizationalUnit {
  id: string;
  name: string;
  slug: string;
  description: string;
  logo_props: Record<string, unknown>;
  is_active: boolean;
  workspace: string;
  member_count: number;
  project_count: number;
  created_at: string;
  updated_at: string;
}

export interface IOrganizationalUnitMembership {
  id: string;
  organizational_unit: string;
  workspace_member: string;
  role: TOrganizationalUnitMemberRole;
  is_active: boolean;
  member_id: string;
  display_name: string;
  email: string;
  avatar_url: string;
  /** The person's workspace role, which caps any role a unit can grant. */
  workspace_role: number;
  created_at: string;
}

export interface IOrganizationalUnitProject {
  id: string;
  organizational_unit: string;
  project: string;
  /** Project role every member of the unit inherits (20 / 15 / 5). */
  default_role: number;
  project_name: string;
  project_identifier: string;
  created_at: string;
}

/** Why a person has inherited access to a project. */
export interface IOrganizationalUnitAccessSource {
  organizational_unit_id: string;
  organizational_unit_name: string;
  membership_id: string;
  role: number;
}

export interface IOrganizationalUnitAccessChange {
  workspace_member_id: string;
  project_id: string;
  current_role: number | null;
  desired_role: number | null;
  action: TOrganizationalUnitAccessAction;
  sources: IOrganizationalUnitAccessSource[];
}

export interface IOrganizationalUnitWorkload {
  workspace_member_id: string;
  display_name: string;
  role: TOrganizationalUnitMemberRole;
  /** Open work items assigned across the unit's own live projects. */
  open_issues: number;
}

export interface IUserOrganizationalUnit {
  organizational_unit: IOrganizationalUnit;
  role: TOrganizationalUnitMemberRole;
  projects: IOrganizationalUnitProject[];
}
