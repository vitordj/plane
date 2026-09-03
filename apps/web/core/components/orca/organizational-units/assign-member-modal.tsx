/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IAssignmentCandidate, IUnitQueueRow } from "@plane/types";
import { Avatar, Loader, ModalCore, EModalWidth } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
  unitId: string;
  row: IUnitQueueRow | null;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Pick who takes a work item. Shows each candidate's open work
 * beside their name: a coordinator choosing without it is guessing, and the
 * automatic ranking's own choice becomes something they can agree with or
 * override on purpose.
 *
 * People the area cannot hand work to are listed too, greyed, with the reason
 * — "nobody is available" is much easier to act on when you can see it is
 * because everyone is at their limit.
 */
export const AssignMemberModal = observer(function AssignMemberModal(props: Props) {
  const { isOpen, onClose, workspaceSlug, unitId, row } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [candidates, setCandidates] = useState<IAssignmentCandidate[]>([]);
  const [excluded, setExcluded] = useState<IAssignmentCandidate[]>([]);

  useEffect(() => {
    if (!isOpen || !row) return;
    let cancelled = false;
    setIsLoading(true);
    store
      .fetchCandidates(workspaceSlug, row.project_id, row.issue_id)
      .then((result) => {
        if (cancelled) return;
        setCandidates(result.candidates ?? []);
        setExcluded(result.excluded ?? []);
      })
      .catch(() => {
        if (!cancelled) setCandidates([]);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, row, workspaceSlug, store]);

  const handlePick = async (candidate: IAssignmentCandidate) => {
    if (!row) return;
    setIsSaving(true);
    try {
      // The decision the coordinator was looking at travels with the request,
      // so if somebody moved the work while this modal was open the server
      // refuses instead of undoing them.
      await store.assignTo(workspaceSlug, unitId, row.project_id, row.issue_id, candidate.user_id, row.current_decision);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.queue.toast.assigned`) });
      onClose();
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.queue.toast.not_assigned`) });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} width={EModalWidth.LG}>
      <div className="p-5">
        <h3 className="text-lg text-custom-text-100 font-medium">{t(`${OU}.queue.assign_modal.title`)}</h3>
        {row && <p className="text-custom-text-300 text-body-xs-regular mt-1 truncate">{row.name}</p>}

        {isLoading ? (
          <Loader className="mt-4 space-y-2">
            <Loader.Item height="36px" />
            <Loader.Item height="36px" />
          </Loader>
        ) : (
          <div className="mt-4 space-y-1">
            {candidates.length === 0 && (
              <p className="text-custom-text-400 text-body-xs-regular py-4 text-center">
                {t(`${OU}.queue.assign_modal.nobody`)}
              </p>
            )}
            {candidates.map((candidate) => (
              <button
                key={candidate.user_id}
                type="button"
                disabled={isSaving}
                onClick={() => handlePick(candidate)}
                className="hover:bg-custom-background-80 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left"
              >
                <Avatar name={candidate.display_name ?? ""} src={candidate.avatar_url ?? ""} size="sm" />
                <span className="text-body-xs-regular text-custom-text-100 flex-1 truncate">
                  {candidate.display_name}
                </span>
                <span className="text-custom-text-400 text-body-2xs-regular">
                  {t(`${OU}.queue.assign_modal.open_items`, { count: candidate.total_open })}
                </span>
              </button>
            ))}

            {excluded.length > 0 && (
              <div className="border-custom-border-200 mt-3 border-t pt-3">
                <p className="text-custom-text-400 text-body-2xs-medium mb-1">
                  {t(`${OU}.queue.assign_modal.excluded`)}
                </p>
                {excluded.map((candidate) => (
                  <div key={candidate.user_id} className="flex items-center gap-2 px-2 py-1">
                    <Avatar name={candidate.display_name ?? ""} src={candidate.avatar_url ?? ""} size="sm" />
                    <span className="text-body-xs-regular text-custom-text-400 flex-1 truncate">
                      {candidate.display_name}
                    </span>
                    <span className="text-custom-text-400 text-body-2xs-regular">
                      {t(`${OU}.queue.excluded_reason.${candidate.excluded_reason ?? "policy_limit"}`)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="mt-5 flex justify-end">
          <Button variant="neutral-primary" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
