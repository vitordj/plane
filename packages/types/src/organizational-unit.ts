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

/**
 * Assignment behavior when a unit is asked to take a work item.
 *
 * `fill_empty` and `append` are the v1 vocabulary and are deprecated: the API
 * still accepts them, and both now mean automatic allocation. Prefer the
 * modes below, which are what the area's policy is written in.
 */
export type TOrganizationalUnitAssignMode = "fill_empty" | "append";

/** How an area picks the person who executes a work item. */
export type TAssignmentMode = "manual" | "self_claim" | "least_loaded" | "explicit";

/** Where a work item stands in its area's queue. */
export type TRoutingState = "queued" | "assigned" | "allocation_failed" | "suspended";

/** Why a work item is waiting. */
export type TQueueReason =
  | ""
  | "new_item"
  | "awaiting_coordinator"
  | "awaiting_claim"
  | "no_eligible_member"
  | "executor_unavailable"
  | "manually_returned";

/** The queue state of one work item, as the API returns it. */
export interface IIssueRouting {
  id: string;
  organizational_unit: string;
  routing_state: TRoutingState;
  queue_reason: TQueueReason;
  queued_at: string | null;
  assignment_due_at: string | null;
  /** The one person answerable for the work; others are collaborators. */
  primary_executor: string | null;
  current_assignment_decision: string | null;
}

/** The policy an area applies, already resolved for a project. */
export interface IResolvedAssignmentPolicy {
  effective_mode: TAssignmentMode;
  policy_source: "request" | "unit_project" | "unit" | "fallback";
  policy_version: number | null;
  assignment_sla_seconds: number | null;
}

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
  /**
   * Projects this unit covers, and therefore may own work in. Archived
   * projects are left out, exactly as the API's coverage rule does.
   */
  project_ids: string[];
  /** Whether the unit was created by hand or pushed by the directory. */
  sync_source: TDirectorySyncSource;
  /** The directory group this unit mirrors; empty when it is not bound. */
  external_id: string;
  directory_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface IOrganizationalUnitMembership {
  id: string;
  organizational_unit: string;
  workspace_member: string;
  role: TOrganizationalUnitMemberRole;
  is_active: boolean;
  /** Whether the directory added this person, or an admin did. */
  sync_source: TDirectorySyncSource;
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

/**
 * Directory (SCIM) provisioning. An Entra group binds to a unit and supplies
 * its members; what the unit grants stays a Plane decision. See
 * docs/entra-directory-sync.md.
 */

/** Where a unit or a membership came from. */
export type TDirectorySyncSource = "manual" | "scim";

/** Whether a directory identity could be matched to a workspace member. */
export type TDirectoryIdentityState = "linked" | "unresolved";

/** Counters from one projection pass, shown after a sync or a resync. */
export interface IDirectorySyncSummary {
  memberships_created?: number;
  memberships_reactivated?: number;
  memberships_deactivated?: number;
  identities_linked?: number;
  identities_unresolved?: number;
  unresolved_user_names?: string[];
}

export interface IDirectoryConnection {
  id: string;
  provider: string;
  is_enabled: boolean;
  tenant_id: string;
  /** Whether a pushed group with no bound unit may create one. */
  auto_create_units: boolean;
  /** Whether the directory may withdraw the memberships it created. */
  deprovision_removes_membership: boolean;
  /** First characters of the installed token; the token itself is never returned. */
  token_prefix: string;
  token_issued_at: string | null;
  token_last_used_at: string | null;
  last_sync_at: string | null;
  last_sync_summary: IDirectorySyncSummary;
  has_token: boolean;
  /** Tenant URL to paste into the Entra provisioning form. */
  scim_base_url: string;
  created_at: string;
  updated_at: string;
}

/** Returned only by the token endpoint, and only once. */
export interface IDirectoryConnectionWithToken extends IDirectoryConnection {
  token: string;
}

export interface IDirectoryIdentity {
  id: string;
  external_id: string;
  user_name: string;
  email: string;
  display_name: string;
  /** The directory's own view of the person, not Plane's. */
  is_active: boolean;
  state: TDirectoryIdentityState;
  workspace_member: string | null;
  workspace_member_display_name: string | null;
  last_seen_at: string | null;
  created_at: string;
}
