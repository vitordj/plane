/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Lightbulb } from "lucide-react";
// plane imports
import { resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IAssignmentCandidate, IQueueItem } from "@plane/types";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
  item: IQueueItem;
  /** Only a coordinator can act on the suggestion; everyone else just sees it. */
  canAssign: boolean;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Who would take this next, for an item that came back because
 * the person on it became unavailable (item 3.5).
 *
 * Only for those items, and deliberately. An item that has *never* had anybody
 * is a normal queue item and the coordinator's own reading of the queue is
 * better than a suggestion; an item that came back is one where the machine
 * already knows the shape of the answer — same area, same project, one person
 * short — and where the coordinator is doing recovery work they did not plan.
 *
 * The suggestion decides nothing. Accepting it is the ordinary "assign to…"
 * call, with a reason recorded so the decision log says the coordinator took
 * the suggestion rather than choosing independently.
 */
export const QueueItemSuggestion = observer(function QueueItemSuggestion(props: Props) {
  const { workspaceSlug, unitId, item, canAssign } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [suggestion, setSuggestion] = useState<IAssignmentCandidate | null>(null);
  const [isAssigning, setIsAssigning] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await store.fetchCandidates(workspaceSlug, item.project, item.issue_id);
        const best = response.candidates.find((candidate) => candidate.eligible) ?? null;
        if (!cancelled) setSuggestion(best);
      } catch {
        // No suggestion is a perfectly good outcome — the row still shows
        // everything else, and the coordinator assigns from the modal.
        if (!cancelled) setSuggestion(null);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, item.project, item.issue_id, store]);

  if (suggestion === null) return null;

  const handleAccept = async () => {
    setIsAssigning(true);
    try {
      await store.assign(workspaceSlug, item.project, item.issue_id, suggestion.user_id, {
        unitId,
        expectedDecisionId: item.current_assignment_decision,
        reason: "accepted_suggestion",
      });
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.work.toast.assigned`), message: suggestion.display_name });
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${OU}.work.toast.not_assigned`),
        message: t(resolveOrcaErrorKey(error) ?? `${OU}.try_again`),
      });
    } finally {
      setIsAssigning(false);
    }
  };

  return (
    <span className="text-xs text-custom-text-300 flex items-center gap-1">
      <Lightbulb className="size-3" />
      {t(`${OU}.work.suggestion`, { name: suggestion.display_name })}
      {canAssign && (
        <button
          type="button"
          className="text-custom-primary-100 focus-visible:ring-custom-primary-100 rounded underline outline-none focus-visible:ring-2 disabled:opacity-60"
          onClick={handleAccept}
          disabled={isAssigning}
        >
          {t(`${OU}.work.suggestion_accept`)}
        </button>
      )}
    </span>
  );
});
