/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Orca (fork): the API's error codes, mapped to catalogue keys.
 *
 * The organizational layer answers a failure with `{ error, error_code,
 * error_message }`: English prose for API clients and logs, and a stable
 * number the interface can translate. Putting `error` straight into a toast —
 * which is what the panels used to do — showed an English sentence to a
 * workspace working in another language.
 *
 * Numbers are the fork's own 4900 band and must match
 * `plane/utils/orca_error_codes.py`. `test_orca_error_codes.py` parses both
 * files and fails when they disagree.
 */
export const ORCA_ERROR_CODE_KEYS: Record<number, string> = {
  // organizational units
  4900: "workspace_settings.settings.organizational_units.errors.unit_not_found",
  4901: "workspace_settings.settings.organizational_units.errors.name_required",
  4902: "workspace_settings.settings.organizational_units.errors.slug_taken",
  4903: "workspace_settings.settings.organizational_units.errors.members_not_in_workspace",
  4904: "workspace_settings.settings.organizational_units.errors.membership_not_found",
  4905: "workspace_settings.settings.organizational_units.errors.lead_already_set",
  4906: "workspace_settings.settings.organizational_units.errors.unit_not_in_workspace",
  // units linked to projects
  4907: "workspace_settings.settings.organizational_units.errors.project_required",
  4908: "workspace_settings.settings.organizational_units.errors.invalid_role",
  4909: "workspace_settings.settings.organizational_units.errors.project_not_in_workspace",
  4910: "workspace_settings.settings.organizational_units.errors.link_not_found",
  // work items
  4911: "workspace_settings.settings.organizational_units.errors.work_item_not_found",
  4912: "workspace_settings.settings.organizational_units.errors.work_item_has_no_unit",
  4913: "workspace_settings.settings.organizational_units.errors.invalid_assignment_mode",
  4916: "workspace_settings.settings.organizational_units.errors.unit_not_covering_project",
  // assignment service
  4917: "workspace_settings.settings.organizational_units.errors.assignment_mode_not_allowed",
  4918: "workspace_settings.settings.organizational_units.errors.executor_not_eligible",
  4919: "workspace_settings.settings.organizational_units.errors.work_item_already_claimed",
  4920: "workspace_settings.settings.organizational_units.errors.decision_stale",
  4921: "workspace_settings.settings.organizational_units.errors.invalid_routing_transition",
  // directory provisioning
  4914: "workspace_settings.settings.organizational_units.errors.directory_workspace_not_found",
  4915: "workspace_settings.settings.organizational_units.errors.directory_token_required",
  // public automation API
  4922: "workspace_settings.settings.organizational_units.errors.public_api_disabled",
  4923: "workspace_settings.settings.organizational_units.errors.idempotency_key_required",
  4924: "workspace_settings.settings.organizational_units.errors.idempotency_payload_mismatch",
  4925: "workspace_settings.settings.organizational_units.errors.operation_in_progress",
  4926: "workspace_settings.settings.organizational_units.errors.external_binding_conflict",
  4927: "workspace_settings.settings.organizational_units.errors.assignees_not_allowed_here",
  4928: "workspace_settings.settings.organizational_units.errors.if_match_required",
  4929: "workspace_settings.settings.organizational_units.errors.process_projection_disabled",
  4930: "workspace_settings.settings.organizational_units.errors.completion_manual_only",
  4931: "workspace_settings.settings.organizational_units.errors.internal_error",
};

/** The shape the Orca API returns on failure. Every field may be absent. */
export type TOrcaApiError = {
  error?: string;
  error_code?: number;
  error_message?: string;
};

/**
 * @description Finds the catalogue key for a rejected Orca request.
 *
 * Deliberately returns `undefined` rather than a fallback key: the caller
 * knows which generic message suits its screen, and a shared one would read
 * as boilerplate in every panel.
 * @param error Whatever the service layer threw. Anything that is not a
 * recognized Orca error body yields `undefined`.
 * @returns A catalogue key, or `undefined` when the code is absent or unknown.
 */
export function resolveOrcaErrorKey(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const code = (error as TOrcaApiError).error_code;
  if (typeof code !== "number") return undefined;
  return ORCA_ERROR_CODE_KEYS[code];
}
