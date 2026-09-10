/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TIssuePriorities } from "./issues";
import type { TPaginatedResponse } from "./pagination";
import type { TStateGroups } from "./state";

/**
 * Types for the Orca organizational layer: units (areas, squads, committees)
 * that group workspace members and grant them project access through the
 * native ProjectMember mechanism. See FORK.md and docs/organizational-units.md.
 */

/** Role a person holds inside a unit — governs the unit, not its projects. */
export type TOrganizationalUnitMemberRole = "lead" | "member";

/** Assignment behavior when a unit is asked to take a work item. */
export type TOrganizationalUnitAssignMode = "fill_empty" | "append";

/** Where a work item stands between "an area owns this" and "a person is on it". */
export type TRoutingState = "queued" | "assigned" | "allocation_failed" | "suspended";

/** Why an item is sitting in the queue — a coordinator needs to tell these apart. */
export type TQueueReason =
  | ""
  | "new_item"
  | "awaiting_coordinator"
  | "awaiting_claim"
  | "no_eligible_member"
  | "executor_unavailable"
  | "manually_returned";

/** How an area hands work out. */
export type TAssignmentMode = "manual" | "self_claim" | "least_loaded" | "explicit";

/** One allocation, as the work item panel reads it. */
export interface IAssignmentDecision {
  id: string;
  trigger: string;
  requested_mode: string | null;
  effective_mode: TAssignmentMode;
  policy_source: "request" | "unit_project" | "unit" | "fallback";
  policy_version: number | null;
  algorithm_version: string;
  outcome: "assigned" | "queued" | "allocation_failed" | "rejected";
  chosen_assignee: string | null;
  previous_primary_executor: string | null;
  decided_by: string | null;
  supersedes: string | null;
  reason: string;
  created_at: string;
}

/**
 * A decision as the area's log reads it: which item it was about, and one
 * level into what it replaced. ``supersedes`` is the plain decision, not
 * this shape, so the chain stops after one hop.
 */
export interface IAssignmentDecisionDetail extends Omit<IAssignmentDecision, "supersedes"> {
  issue: { id: string; sequence_id: number; name: string; project_id: string };
  supersedes: IAssignmentDecision | null;
}

/** The routing state of one work item under its responsible area. */
export interface IIssueRouting {
  id: string;
  organizational_unit: IOrganizationalUnit;
  routing_state: TRoutingState;
  queue_reason: TQueueReason;
  queued_at: string | null;
  assignment_due_at: string | null;
  primary_executor: string | null;
  current_assignment_decision: IAssignmentDecision | null;
  created_at: string;
  updated_at: string;
}

/** The policy in force for an area, resolved for one project when asked. */
export interface IAssignmentPolicyResolution {
  effective_mode: TAssignmentMode;
  policy_source: "request" | "unit_project" | "unit" | "fallback";
  policy_version: number | null;
  allowed_modes: TAssignmentMode[];
  assignment_sla_seconds: number | null;
  max_open_items_per_member: number | null;
  policy: Record<string, unknown> | null;
}

/**
 * The area's queue, as the Work tab reads it. Wider than the public
 * `queue_row`: a coordinator deciding what to do next needs the work item's
 * own project, state and priority on the same line, and the three action
 * flags below so the row can hide what this viewer may not do.
 */
export interface IQueueRow {
  issue_id: string;
  sequence_id: number;
  name: string;
  project: { id: string; identifier: string; name: string };
  state: { id: string; name: string; color: string; group: TStateGroups } | null;
  priority: TIssuePriorities;
  target_date: string | null;
  routing_state: TRoutingState;
  queue_reason: TQueueReason;
  queued_at: string | null;
  assignment_due_at: string | null;
  /** Past `assignment_due_at` with nobody on it — the row the coordinator owes. */
  assignment_overdue: boolean;
  age_seconds: number;
  primary_executor: {
    id: string;
    display_name: string;
    email: string;
    avatar_url: string;
    /** False when a covering unavailability window exists and the feature is on. */
    is_available?: boolean;
    /** End of that window, when it has one. */
    unavailable_until?: string | null;
  } | null;
  /** Sent back as `expected_decision_id`, so two coordinators cannot both act. */
  current_decision_id: string | null;
  permissions: IQueueRowPermissions;
}

/** Why somebody is away. */
export type TAvailabilityReason = "vacation" | "leave" | "other";

/**
 * One stretch of time somebody is not available for work.
 *
 * Global to the workspace rather than per area: a holiday is a holiday
 * everywhere, and asking somebody to record the same fortnight once per area
 * is how a feature stops being used.
 */
export interface IMemberAvailability {
  id: string;
  workspace_member: string;
  unavailable_from: string;
  /** Null means open-ended — away, and nobody knows until when. */
  unavailable_until: string | null;
  reason: TAvailabilityReason;
  source: "manual" | "hr" | "directory";
  created_at: string;
}

/** How much work one area gives one of its people. */
export interface IMembershipAllocation {
  id?: string;
  membership: string;
  accepts_new_work: boolean;
  /** Null means the area's policy decides alone. */
  max_open_items: number | null;
  updated_at?: string;
}

/** Somebody an area could hand a work item to, with the load that ranks them. */
export interface IAssignmentCandidate {
  user_id: string;
  display_name: string | null;
  avatar_url: string | null;
  total_open: number;
  unit_open: number;
  last_auto_at: string | null;
  excluded_reason?: string;
}

/**
 * What this viewer may do to this row, decided by the server. The interface
 * only hides what these deny; the API refuses it regardless (RFC §1.2), so a
 * stale flag costs a toast, never an unauthorized write.
 */
export interface IQueueRowPermissions {
  can_claim: boolean;
  can_assign: boolean;
  can_return: boolean;
}

/** Who the viewer is to this area, sent once per page rather than per row. */
export interface IQueueViewer {
  is_admin: boolean;
  is_coordinator: boolean;
  is_member: boolean;
}

/** One page of the queue: the native cursor envelope plus `viewer`. */
export type IQueuePage = TPaginatedResponse<IQueueRow[]> & { viewer: IQueueViewer };

/**
 * Someone who allocates an area's work. Distinct from the area's lead, and
 * from its membership: a coordinator need not belong to the area they route
 * work for (RFC §5.2).
 */
export interface IOrganizationalUnitCoordinator {
  id: string;
  workspace_member: string;
  member: { id: string; display_name: string; email: string; avatar_url: string };
  is_active: boolean;
  created_at: string;
}

/** The policy body an admin submits; every field but the mode is optional. */
export interface IAssignmentPolicyPayload {
  default_mode: TAssignmentMode;
  allowed_modes: TAssignmentMode[];
  assignment_sla_seconds?: number | null;
  max_open_items_per_member?: number | null;
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
   * Projects this area covers, and therefore may own work in. Excludes
   * archived projects, matching the API's coverage rule.
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
  email?: string;
  avatar_url: string;
  /** The person's workspace role, which caps any role a unit can grant. */
  workspace_role: number;
  /** Whether this membership is willing to take more work from its area. Default true. */
  accepts_new_work?: boolean;
  /** Personal cap on open items; null means none. */
  max_open_items?: number | null;
  /** False when a covering unavailability window exists right now. */
  is_available?: boolean;
  /** End of the covering window, when it has one. Null means open-ended. */
  unavailable_until?: string | null;
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
  leads_deactivated?: number;
  leads_demoted?: number;
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

/** Window the executive report covers. */
export type TExecutivePeriod = "7d" | "30d" | "90d";

/** Indicator a director can open as a filtered list. */
export type TExecutiveMetric = "backlog" | "queued" | "assignment_overdue" | "target_overdue" | "throughput";

/** One day's throughput for the CSS sparkline — no chart library. */
export interface IExecutiveSparklineBar {
  date: string;
  count: number;
}

/**
 * Aggregates for one area. Counts include items in projects the reader
 * cannot open; `hidden_count` is how many of those there are. Durations
 * are seconds. Ratios are 0–1, or null when the denominator is 0.
 */
export interface IExecutiveUnitMetrics {
  unit_id: string;
  name: string;
  slug: string;
  backlog: number;
  queued: number;
  assignment_overdue: number;
  target_overdue: number;
  queue_age_p50: number | null;
  queue_age_p90: number | null;
  throughput: number;
  cycle_time_p50: number | null;
  cycle_time_p90: number | null;
  concentration_top3: number | null;
  auto_assign_kept_ratio: number | null;
  throughput_sparkline: IExecutiveSparklineBar[];
  hidden_count: number;
}

/** An open process step whose promised date has passed. */
export interface IExecutiveDelayedStep {
  process_instance_id: string;
  template_name: string;
  step_key: string;
  issue_id: string;
  due_at: string | null;
  kind: "completion" | "assignment";
  late_seconds: number;
}

export interface IExecutiveProcessInstance {
  process_instance_id: string;
  template_name: string;
  template_version: string;
  status: "running" | "completed" | "cancelled";
  started_at: string | null;
  completed_at: string | null;
  lead_time_seconds: number | null;
}

export interface IExecutiveProcessMetrics {
  running: number;
  completed: number;
  lead_time_p50: number | null;
  lead_time_p90: number | null;
  delayed_steps: IExecutiveDelayedStep[];
  instances: IExecutiveProcessInstance[];
}

/** Payload of `GET /api/orca/workspaces/{slug}/executive/`. */
export interface IExecutiveReport {
  period: TExecutivePeriod;
  period_start: string;
  generated_at: string;
  units: IExecutiveUnitMetrics[];
  processes: IExecutiveProcessMetrics;
}

/** Payload of `GET /api/orca/workspaces/{slug}/executive/drill-down/`. */
export type IExecutiveDrillDown = TPaginatedResponse<IQueueRow[]> & {
  metric: TExecutiveMetric;
  unit_id: string;
  hidden_count: number;
};
