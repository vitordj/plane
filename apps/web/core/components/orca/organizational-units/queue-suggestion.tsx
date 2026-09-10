/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Sparkles } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IAssignmentCandidate, IQueueRow } from "@plane/types";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
  row: IQueueRow;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Who would take this work item next, on the rows where somebody
 * has to decide: the ones the availability sweep handed back because their
 * executor went away.
 *
 * A suggestion rather than an automatic reassignment, and the difference is
 * the whole design: a holiday that silently moves three work items surprises
 * three people, while a holiday that asks costs one click. Accepting it is the
 * ordinary "assign to" action, recorded with `accepted_suggestion` as the
 * reason so the timeline can tell the two apart later.
 */
export const QueueSuggestion = observer(function QueueSuggestion(props: Props) {
  const { workspaceSlug, unitId, row } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [candidate, setCandidate] = useState<IAssignmentCandidate | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    store
      .fetchCandidates(workspaceSlug, row.project.id, row.issue_id)
      .then((result) => {
        if (!cancelled) setCandidate(result.candidates?.[0] ?? null);
        return undefined;
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, row.project.id, row.issue_id, store]);

  if (!candidate) return null;

  const handleAccept = async () => {
    setIsSaving(true);
    try {
      await store.assign(workspaceSlug, unitId, row, candidate.user_id, { reason: "accepted_suggestion" });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${OU}.work.toast.assigned_title`),
        message: t(`${OU}.work.toast.assigned`, { name: candidate.display_name ?? "" }),
      });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_assigned`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <button
      type="button"
      disabled={isSaving}
      onClick={handleAccept}
      className="text-custom-text-300 hover:text-custom-text-100 flex items-center gap-1 text-[11px]"
    >
      <Sparkles className="size-3" />
      {t(`${OU}.work.suggestion`, { name: candidate.display_name ?? "" })}
    </button>
  );
});
