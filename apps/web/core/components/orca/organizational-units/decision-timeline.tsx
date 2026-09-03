/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IAssignmentDecision } from "@plane/types";
import { Loader } from "@plane/ui";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
  issueId?: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Why the work in this area went where it went. The question a
 * coordinator actually asks about an automatic assignment is "why them and not
 * me?", and the answer is the decision record — the mode that applied, who
 * decided, and what it replaced.
 */
export const DecisionTimeline = observer(function DecisionTimeline(props: Props) {
  const { workspaceSlug, unitId, issueId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const {
    workspace: { getWorkspaceMemberDetails },
  } = useMember();

  const [isLoading, setIsLoading] = useState(true);
  const [decisions, setDecisions] = useState<IAssignmentDecision[]>([]);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    store
      .fetchDecisions(workspaceSlug, unitId, issueId)
      .then((rows) => {
        if (!cancelled) setDecisions(rows ?? []);
      })
      .catch(() => {
        if (!cancelled) setDecisions([]);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceSlug, unitId, issueId, store]);

  const nameOf = (userId: string | null) =>
    userId ? (getWorkspaceMemberDetails(userId)?.member.display_name ?? t(`${OU}.queue.unknown_executor`)) : null;

  if (isLoading) {
    return (
      <Loader className="space-y-2">
        <Loader.Item height="28px" />
        <Loader.Item height="28px" />
      </Loader>
    );
  }

  if (decisions.length === 0) {
    return <p className="text-custom-text-400 text-body-xs-regular py-4">{t(`${OU}.decisions.empty`)}</p>;
  }

  return (
    <ol className="space-y-2">
      {decisions.map((decision) => {
        const chosen = nameOf(decision.chosen_assignee);
        const previous = nameOf(decision.previous_primary_executor);
        const decidedBy = nameOf(decision.decided_by);
        return (
          <li key={decision.id} className="border-custom-border-200 rounded-md border px-3 py-2">
            <p className="text-body-xs-regular text-custom-text-100">
              {t(`${OU}.decisions.outcome.${decision.outcome}`, { name: chosen ?? "" })}
              {previous && (
                <span className="text-custom-text-400"> · {t(`${OU}.decisions.replacing`, { name: previous })}</span>
              )}
            </p>
            <p className="text-custom-text-400 text-body-2xs-regular mt-0.5">
              {t(`${OU}.queue.mode.${decision.effective_mode}`)}
              {" · "}
              {decidedBy ? t(`${OU}.decisions.by`, { name: decidedBy }) : t(`${OU}.decisions.by_system`)}
              {" · "}
              {new Date(decision.created_at).toLocaleString()}
            </p>
            {decision.reason && (
              <p className="text-custom-text-300 text-body-2xs-regular mt-1 italic">{decision.reason}</p>
            )}
          </li>
        );
      })}
    </ol>
  );
});
