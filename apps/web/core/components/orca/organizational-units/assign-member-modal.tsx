/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { Search } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import { Avatar, EModalPosition, EModalWidth, Input, Loader, ModalCore } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  unitId: string;
  /** What the modal's subtitle shows for the item being handed out. */
  subtitle?: string;
  /**
   * Does the actual assignment. The modal has no opinion on what that call
   * updates — a queue row, a routing state, anything else with an
   * `executor_id` to send — so the caller supplies it and the modal only
   * drives the picking and the toast.
   */
  onAssign: (userId: string) => Promise<unknown>;
  onClose: () => void;
};

const OU = "workspace_settings.settings.organizational_units";

/** One person as the picker shows them: who they are, and how much they hold. */
type TCandidate = {
  /** The user id, which is what `reassign/` allocates work to. */
  userId: string;
  displayName: string;
  email: string;
  avatarUrl: string;
  openIssues: number;
};

/**
 * @description Chooses who takes a queued work item.
 *
 * Candidates are the area's own members with the open-work count the
 * `workload/` endpoint already reports (decision M5) — no separate ranking
 * call. The list is sorted by that count so the least loaded person is the
 * easiest to click, but the sort is a hint to a human, not the engine's
 * ranking: `least_loaded` stays a decision the service makes.
 *
 * Whether the chosen person may actually hold this work is not decided here.
 * The area's membership does not imply access to every project it covers, so
 * `reassign/` answers `ORG_EXECUTOR_NOT_ELIGIBLE` and the toast says so —
 * filtering the list here would hide the reason.
 */
export const AssignMemberModal = observer(function AssignMemberModal(props: Props) {
  const { isOpen, workspaceSlug, unitId, subtitle, onAssign, onClose } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [assigningUserId, setAssigningUserId] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setSearch("");
    setIsLoading(true);
    // The workload rows carry WorkspaceMember ids; the memberships carry both
    // those and the user id the assignment actually needs, so the two are
    // fetched together and joined below.
    Promise.all([store.fetchWorkload(workspaceSlug, unitId), store.fetchMembers(workspaceSlug, unitId)])
      .catch(() => undefined)
      .finally(() => setIsLoading(false));
  }, [isOpen, workspaceSlug, unitId, store]);

  // Read out of the store during render, so the observer tracks both
  // collections and the memo below re-runs when either one arrives.
  const workload = store.getWorkloadByUnitId(unitId);
  const memberships = store.getMembersByUnitId(unitId);

  const candidates = useMemo<TCandidate[]>(() => {
    const loadByWorkspaceMember = new Map(workload.map((entry) => [entry.workspace_member_id, entry.open_issues]));
    return memberships
      .filter((membership) => membership.is_active)
      .map((membership) => ({
        userId: membership.member_id,
        displayName: membership.display_name,
        email: membership.email ?? "",
        avatarUrl: membership.avatar_url,
        openIssues: loadByWorkspaceMember.get(membership.workspace_member) ?? 0,
      }))
      .toSorted((a, b) => a.openIssues - b.openIssues || a.displayName.localeCompare(b.displayName));
  }, [workload, memberships]);

  const visibleCandidates = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return candidates;
    return candidates.filter(
      (candidate) => candidate.displayName.toLowerCase().includes(term) || candidate.email.toLowerCase().includes(term)
    );
  }, [candidates, search]);

  const handleAssign = async (candidate: TCandidate) => {
    setAssigningUserId(candidate.userId);
    try {
      await onAssign(candidate.userId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.assigned_title`),
        message: t(`${OU}.work.toast.assigned`, { name: candidate.displayName }),
      });
      onClose();
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_assigned`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setAssigningUserId(null);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <div className="flex flex-col gap-1">
          <h3 className="text-lg text-custom-text-100 font-medium">{t(`${OU}.work.assign_modal.title`)}</h3>
          {subtitle && <p className="text-sm text-custom-text-300 truncate">{subtitle}</p>}
        </div>

        <div className="relative">
          <Search className="text-custom-text-400 pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t(`${OU}.work.assign_modal.search`)}
            className="pl-9"
          />
        </div>

        {isLoading ? (
          <Loader className="flex flex-col gap-2">
            <Loader.Item height="44px" />
            <Loader.Item height="44px" />
            <Loader.Item height="44px" />
          </Loader>
        ) : visibleCandidates.length === 0 ? (
          <p className="text-sm text-custom-text-300 py-8 text-center">{t(`${OU}.work.assign_modal.no_candidates`)}</p>
        ) : (
          <div className="divide-custom-border-200 border-custom-border-200 max-h-80 divide-y overflow-y-auto rounded border">
            {visibleCandidates.map((candidate) => (
              <button
                key={candidate.userId}
                type="button"
                disabled={assigningUserId !== null}
                onClick={() => handleAssign(candidate)}
                className="hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left outline-none focus-visible:ring-2 disabled:opacity-60"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Avatar name={candidate.displayName} src={candidate.avatarUrl} size="md" />
                  <span className="min-w-0">
                    <span className="text-sm text-custom-text-100 block truncate">{candidate.displayName}</span>
                    <span className="text-xs text-custom-text-300 block truncate">{candidate.email}</span>
                  </span>
                </span>
                <span className="text-xs text-custom-text-300 shrink-0">
                  {assigningUserId === candidate.userId
                    ? t(`${OU}.work.assign_modal.assigning`)
                    : t(`${OU}.work.assign_modal.open_items`, { count: candidate.openIssues })}
                </span>
              </button>
            ))}
          </div>
        )}

        <div className="flex justify-end pt-1">
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
