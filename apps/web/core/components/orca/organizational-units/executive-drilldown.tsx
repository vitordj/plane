/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Link } from "react-router";
import { X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IExecutiveDrilldown } from "@plane/types";
import { Loader } from "@plane/ui";
import { generateWorkItemLink } from "@plane/utils";

type Props = {
  workspaceSlug: string;
  title: string;
  isLoading: boolean;
  data: IExecutiveDrilldown | null;
  onClose: () => void;
};

const OU = "workspace_settings.settings.organizational_units";

/**
 * @description The rows behind one number on the executive page (item 5.3).
 *
 * Deliberately read-only, which is why it is not `queue-list.tsx` with its
 * actions switched off: a director looking at a count is answering "which
 * ones?", and a page that offers to reassign from there would be putting the
 * area's own decisions in the wrong hands. The queue is where work is moved.
 *
 * `hidden` is shown rather than swallowed. Plane's project membership decides
 * who reads titles of real work, and "7 items in projects you cannot see" is
 * an honest sentence where silently returning thirteen of twenty is not.
 */
export const ExecutiveDrilldown = observer(function ExecutiveDrilldown(props: Props) {
  const { workspaceSlug, title, isLoading, data, onClose } = props;
  const { t } = useTranslation();

  return (
    <section className="border-custom-border-200 flex flex-col gap-3 rounded border p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-sm text-custom-text-100 font-medium">{title}</h4>
          {data && (
            <p className="text-xs text-custom-text-300">
              {t(`${OU}.executive.drilldown.showing`, { shown: data.items.length, total: data.total })}
            </p>
          )}
        </div>
        <button
          type="button"
          aria-label={t(`${OU}.executive.drilldown.close`)}
          className="text-custom-text-300 hover:bg-custom-background-80 focus-visible:ring-custom-primary-100 rounded p-1 outline-none focus-visible:ring-2"
          onClick={onClose}
        >
          <X className="size-4" />
        </button>
      </div>

      {isLoading ? (
        <Loader className="flex flex-col gap-2">
          <Loader.Item height="32px" />
          <Loader.Item height="32px" />
        </Loader>
      ) : !data || data.items.length === 0 ? (
        <p className="text-sm text-custom-text-300 border-custom-border-200 rounded border border-dashed px-4 py-6 text-center">
          {t(`${OU}.executive.drilldown.empty`)}
        </p>
      ) : (
        <div className="divide-custom-border-200 border-custom-border-200 divide-y rounded border">
          {data.items.map((item) => (
            <div key={item.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <div className="flex min-w-0 items-center gap-2">
                <span className="text-xs text-custom-text-400 shrink-0">
                  {item.project_identifier}-{item.sequence_id}
                </span>
                <Link
                  to={generateWorkItemLink({
                    workspaceSlug,
                    projectId: item.project,
                    issueId: item.issue_id,
                    projectIdentifier: item.project_identifier,
                    sequenceId: item.sequence_id,
                  })}
                  className="text-sm text-custom-text-100 hover:text-custom-primary-100 truncate"
                >
                  {item.name}
                </Link>
              </div>
              <span className="text-xs text-custom-text-300 shrink-0">
                {item.state_name ?? t(`${OU}.executive.no_value`)}
              </span>
            </div>
          ))}
        </div>
      )}

      {data && data.hidden > 0 && (
        <p className="text-xs text-custom-text-400">{t(`${OU}.executive.drilldown.hidden`, { count: data.hidden })}</p>
      )}
    </section>
  );
});
