/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { X } from "lucide-react";
// plane imports
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { TOrganizationalUnitMemberRole } from "@plane/types";
import { Avatar, CustomSearchSelect, CustomSelect, Loader } from "@plane/ui";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const UNIT_ROLES: { value: TOrganizationalUnitMemberRole; label: string }[] = [
  { value: "member", label: "Member" },
  { value: "lead", label: "Lead" },
];

/**
 * @description Who belongs to an area. Adding someone here grants them access
 * to every project the area is linked to, at that project's inherited role —
 * which is why only workspace admins can reach this screen.
 */
export const OrganizationalUnitMembersTab = observer(function OrganizationalUnitMembersTab(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const {
    workspace: { workspaceMemberIds, getWorkspaceMemberDetails },
  } = useMember();

  const [isLoading, setIsLoading] = useState(true);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [isAdding, setIsAdding] = useState(false);

  const memberships = store.getMembersByUnitId(unitId);

  useEffect(() => {
    setIsLoading(true);
    store.fetchMembers(workspaceSlug, unitId).finally(() => setIsLoading(false));
  }, [workspaceSlug, unitId, store]);

  // Workspace members not already in this area, keyed by WorkspaceMember id —
  // which is what the API expects, not the user id.
  const addableOptions = useMemo(() => {
    const alreadyIn = new Set(memberships.filter((membership) => membership.is_active).map((m) => m.workspace_member));
    return (workspaceMemberIds ?? [])
      .map((userId) => getWorkspaceMemberDetails(userId))
      .filter((details) => details && !alreadyIn.has(details.id))
      .map((details) => ({
        value: details!.id,
        query: `${details!.member.display_name} ${details!.member.email ?? ""}`,
        content: (
          <div className="flex items-center gap-2">
            <Avatar name={details!.member.display_name} src={details!.member.avatar_url} size="sm" />
            <span className="truncate">{details!.member.display_name}</span>
          </div>
        ),
      }));
  }, [memberships, workspaceMemberIds, getWorkspaceMemberDetails]);

  const handleAdd = async () => {
    if (!selectedMemberId) return;
    setIsAdding(true);
    try {
      await store.addMembers(workspaceSlug, unitId, [selectedMemberId]);
      setSelectedMemberId(null);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Member added",
        message: "They now have access to this area's projects.",
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not added", message: "Try again in a moment." });
    } finally {
      setIsAdding(false);
    }
  };

  const handleRoleChange = async (membershipId: string, role: TOrganizationalUnitMemberRole) => {
    try {
      await store.updateMemberRole(workspaceSlug, unitId, membershipId, role);
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Role unchanged",
        message: "An area can only have one lead at a time.",
      });
    }
  };

  const handleRemove = async (membershipId: string, displayName: string) => {
    try {
      await store.removeMember(workspaceSlug, unitId, membershipId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Member removed",
        message: `${displayName} keeps any project access granted outside this area.`,
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not removed", message: "Try again in a moment." });
    }
  };

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
        <Loader.Item height="40px" />
      </Loader>
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <CustomSearchSelect
          value={selectedMemberId}
          options={addableOptions}
          onChange={(value: string) => setSelectedMemberId(value)}
          label={
            selectedMemberId
              ? (addableOptions.find((option) => option.value === selectedMemberId)?.query ?? "Select a person")
              : "Select a person"
          }
          maxHeight="md"
          noResultsMessage="Everyone in this workspace is already in this area"
        />
        <Button variant="primary" size="sm" onClick={handleAdd} loading={isAdding} disabled={!selectedMemberId}>
          Add to area
        </Button>
      </div>

      {memberships.length === 0 ? (
        <p className="text-sm text-custom-text-300 py-8 text-center">
          Nobody is in this area yet. Add people to give them access to its projects.
        </p>
      ) : (
        <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
          {memberships.map((membership) => (
            <div key={membership.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="flex min-w-0 items-center gap-2">
                <Avatar name={membership.display_name} src={membership.avatar_url} size="md" />
                <div className="min-w-0">
                  <p className="text-sm text-custom-text-100 flex min-w-0 items-center gap-2">
                    <span className="truncate">{membership.display_name}</span>
                    {/* Removing a directory-added person here is undone by the
                        next sync; the badge is the warning before the click. */}
                    {membership.sync_source === "scim" && (
                      <span className="text-custom-text-400 shrink-0 rounded bg-layer-1 px-1.5 py-0.5 text-[10px] tracking-wide uppercase">
                        Directory
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-custom-text-300 truncate">{membership.email}</p>
                </div>
              </div>
              <div className="flex flex-shrink-0 items-center gap-2">
                <CustomSelect
                  value={membership.role}
                  label={UNIT_ROLES.find((role) => role.value === membership.role)?.label ?? "Member"}
                  onChange={(value: TOrganizationalUnitMemberRole) => handleRoleChange(membership.id, value)}
                  buttonClassName="text-xs"
                >
                  {UNIT_ROLES.map((role) => (
                    <CustomSelect.Option key={role.value} value={role.value}>
                      {role.label}
                    </CustomSelect.Option>
                  ))}
                </CustomSelect>
                <button
                  type="button"
                  aria-label={`Remove ${membership.display_name} from this area`}
                  className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 rounded p-1 outline-none focus-visible:ring-2"
                  onClick={() => handleRemove(membership.id, membership.display_name)}
                >
                  <X className="size-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-custom-text-300">
        Leading an area governs the area itself. Project admin is granted separately, on the project.
      </p>
      <p className="text-xs text-custom-text-400">
        People marked <span className="uppercase">Directory</span> come from your identity provider. Removing them here
        lasts only until the next sync — take them out of the group upstream instead. People you add by hand are never
        removed by a sync.
      </p>
    </div>
  );
});
