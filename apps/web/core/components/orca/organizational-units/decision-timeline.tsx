/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Loader } from "@plane/ui";
import { calculateTimeAgo } from "@plane/utils";
// hooks
import { useOrganizationalUnit } from "@/hooks/store/use-organizational-unit";

type Props = {
  workspaceSlug: string;
  unitId: string;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description Why the area's work went where it went. Every allocation leaves
 * a record (I5), and the one a decision replaced is shown under it — one level
 * only, because the question a reader has about a reassignment is what it
 * overturned, not the whole history of the item.
 *
 * The candidate snapshot behind each decision is deliberately not shown: it
 * carries the load of everybody the ranking considered, which is a
 * performance-shaped view of a team that a queue screen has no business
 * publishing.
 */
export const DecisionTimeline = observer(function DecisionTimeline(props: Props) {
  const { workspaceSlug, unitId } = props;
  const store = useOrganizationalUnit();
  const { t } = useTranslation();

  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    setIsLoading(true);
    store
      .fetchDecisions(workspaceSlug, unitId)
      .catch(() => undefined)
      .finally(() => setIsLoading(false));
  }, [workspaceSlug, unitId, store]);

  const decisions = store.getDecisionsByUnitId(unitId);

  if (isLoading)
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="36px" />
        <Loader.Item height="36px" />
      </Loader>
    );

  if (decisions.length === 0)
    return <p className="text-sm text-custom-text-300 py-6 text-center">{t(`${OU}.work.decisions_empty`)}</p>;

  return (
    <ol className="flex flex-col gap-3">
      {decisions.map((decision) => (
        <li key={decision.id} className="border-custom-border-200 flex flex-col gap-1 border-l-2 pl-3">
          <p className="text-sm text-custom-text-200">
            {t(`${OU}.work.decision.outcome.${decision.outcome}`)} ·{" "}
            {t(`${OU}.work.decision.mode.${decision.effective_mode}`)}
          </p>
          <p className="text-xs text-custom-text-300">
            {t(`${OU}.work.decision.trigger.${decision.trigger}`)} · {calculateTimeAgo(decision.created_at)}
          </p>
          {decision.reason && <p className="text-xs text-custom-text-300 italic">{decision.reason}</p>}
          {decision.supersedes_detail && (
            <p className="text-xs text-custom-text-400">
              {t(`${OU}.work.decision.replaced`, {
                outcome: t(`${OU}.work.decision.outcome.${decision.supersedes_detail.outcome}`),
                when: calculateTimeAgo(decision.supersedes_detail.created_at),
              })}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
});
