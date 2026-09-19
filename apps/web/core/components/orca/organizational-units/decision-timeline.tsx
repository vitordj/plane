/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Link } from "react-router";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Loader } from "@plane/ui";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";
import { useMember } from "@/hooks/store/use-member";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The area's allocation log, newest first. Coordinator-only
 * on the server; a 403 here is an empty state rather than an error, because
 * a member looking at Work has no other reason to see who was passed over.
 */
export const DecisionTimeline = observer(function DecisionTimeline(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();
  const {
    workspace: { getWorkspaceMemberDetails },
  } = useMember();

  const [isLoading, setIsLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const decisions = store.getDecisionsByUnitId(unitId);

  useEffect(() => {
    setIsLoading(true);
    setForbidden(false);
    store
      .fetchDecisions(workspaceSlug, unitId)
      .catch(() => {
        // Coordinator-only on the server. A 403, a network failure and an
        // empty log are the same thing to a member looking at Work: they
        // have no other reason to see who was passed over.
        setForbidden(true);
      })
      .finally(() => setIsLoading(false));
  }, [workspaceSlug, unitId, store]);

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="48px" />
        <Loader.Item height="48px" />
      </Loader>
    );

  if (forbidden || decisions.length === 0)
    return <p className="text-sm text-custom-text-300 py-8 text-center">{t(`${OU}.work.empty_decisions`)}</p>;

  return (
    <ol className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
      {decisions.map((decision) => {
        const assignee = decision.chosen_assignee ? getWorkspaceMemberDetails(decision.chosen_assignee) : undefined;
        const workItemLink = `/${workspaceSlug}/projects/${decision.issue.project_id}/issues/${decision.issue.id}`;
        return (
          <li key={decision.id} className="flex flex-col gap-1 px-4 py-3">
            <div className="flex min-w-0 items-center gap-2">
              <Link to={workItemLink} className="text-sm text-custom-text-100 hover:text-custom-primary-100 truncate">
                {decision.issue.name}
              </Link>
              <span className="text-xs text-custom-text-400 shrink-0">
                {t(`${OU}.work.decision_outcome.${decision.outcome}`, {
                  defaultValue: decision.outcome,
                })}
              </span>
            </div>
            <div className="text-xs text-custom-text-300 flex flex-wrap items-center gap-x-3 gap-y-1">
              {assignee?.member?.display_name && <span>{assignee.member.display_name}</span>}
              {decision.reason && <span className="truncate">{decision.reason}</span>}
              <time dateTime={decision.created_at}>{new Date(decision.created_at).toLocaleString()}</time>
              {decision.supersedes && <span>{t(`${OU}.work.decision_supersedes`)}</span>}
            </div>
          </li>
        );
      })}
    </ol>
  );
});
