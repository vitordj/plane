/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IAssignmentCandidate, IQueueItem } from "@plane/types";
import { Avatar, EModalPosition, EModalWidth, Loader, ModalCore } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  unitId: string;
  item: IQueueItem | null;
  onClose: () => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * Reasons the ranking gives for leaving somebody out, and the one it falls
 * back to. Spelled out rather than interpolated straight into a key, so a
 * reason the server adds later renders a sentence instead of a raw key.
 */
const EXCLUDED_REASONS = [
  "already_assigned",
  "not_a_project_member",
  "project_role_too_low",
  "bot",
  // `at_max_open_items` is what lb-1 called the policy ceiling; lb-2 tells the
  // area's ceiling and a person's own apart, and old decisions still carry the
  // old name.
  "at_max_open_items",
  "unavailable",
  "opted_out",
  "member_limit",
  "policy_limit",
] as const;

const excludedReasonKey = (reason: string): string =>
  `${OU}.work.excluded.${(EXCLUDED_REASONS as readonly string[]).includes(reason) ? reason : "other"}`;

/**
 * @description Choose who takes this item, from the ranking the allocator
 * itself would use — least total open work first. The people it cannot choose
 * are listed too, greyed out and with the reason, because "why is she not in
 * this list?" is the first thing a coordinator asks and the answer is usually
 * that somebody is not in the project rather than that the area is empty.
 *
 * The list decides nothing: the ranking is recomputed under a row lock when
 * the assignment is made, so a list left open on screen can never hand the
 * item to somebody who has meanwhile become ineligible.
 */
export const AssignMemberModal = observer(function AssignMemberModal(props: Props) {
  const { isOpen, workspaceSlug, unitId, item, onClose } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [candidates, setCandidates] = useState<IAssignmentCandidate[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [assigningTo, setAssigningTo] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !item) return;
    let cancelled = false;
    setIsLoading(true);
    const load = async () => {
      try {
        const response = await store.fetchCandidates(workspaceSlug, item.project, item.issue_id);
        if (!cancelled) setCandidates(response.candidates);
      } catch {
        // A ranking that could not be read is an empty list with an empty
        // state, not a broken modal: the coordinator can close it and retry.
        if (!cancelled) setCandidates([]);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [isOpen, item, workspaceSlug, store]);

  const handleAssign = async (candidate: IAssignmentCandidate) => {
    if (!item) return;
    setAssigningTo(candidate.user_id);
    try {
      await store.assign(workspaceSlug, item.project, item.issue_id, candidate.user_id, {
        unitId,
        // What the row was showing when the coordinator opened this modal: if
        // somebody else moved the item since, the server refuses rather than
        // overwriting a decision this person never saw.
        expectedDecisionId: item.current_assignment_decision,
      });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.assigned`),
        message: candidate.display_name,
      });
      onClose();
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_assigned`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setAssigningTo(null);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.XL}>
      <div className="flex flex-col gap-4 p-5">
        <div>
          <h3 className="text-lg text-custom-text-100 font-medium">{t(`${OU}.work.assign_modal.title`)}</h3>
          {item && (
            <p className="text-sm text-custom-text-300 truncate">
              {item.project_identifier}-{item.sequence_id} · {item.name}
            </p>
          )}
        </div>

        {isLoading ? (
          <Loader className="flex flex-col gap-2">
            <Loader.Item height="44px" />
            <Loader.Item height="44px" />
            <Loader.Item height="44px" />
          </Loader>
        ) : candidates.length === 0 ? (
          <p className="text-sm text-custom-text-300 py-6 text-center">{t(`${OU}.work.assign_modal.empty`)}</p>
        ) : (
          <div className="divide-custom-border-200 border-custom-border-200 max-h-80 divide-y overflow-y-auto rounded border">
            {candidates.map((candidate) => (
              <div key={candidate.user_id} className="flex items-center justify-between gap-3 px-3 py-2">
                <div className="flex min-w-0 items-center gap-2">
                  <Avatar name={candidate.display_name} src={candidate.avatar_url} size="md" />
                  <div className="min-w-0">
                    <p className="text-sm text-custom-text-100 truncate">{candidate.display_name}</p>
                    <p className="text-xs text-custom-text-300">
                      {candidate.eligible
                        ? t(`${OU}.work.assign_modal.load`, {
                            total: candidate.total_open,
                            unit: candidate.unit_open,
                          })
                        : t(excludedReasonKey(candidate.excluded_reason))}
                    </p>
                  </div>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!candidate.eligible}
                  loading={assigningTo === candidate.user_id}
                  onClick={() => handleAssign(candidate)}
                >
                  {t(`${OU}.work.assign_modal.confirm`)}
                </Button>
              </div>
            ))}
          </div>
        )}

        <div className="flex justify-end">
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
