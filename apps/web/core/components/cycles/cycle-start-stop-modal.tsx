/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Orca Custom: CycleStartStopModal
 * @description A single shared confirmation modal for both "Start Cycle" and "End Cycle" actions.
 * When ending a cycle that still has incomplete work items, an amber warning callout is shown
 * to notify the user that those items will not be auto-completed.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { AlertTriangle } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { ICycle } from "@plane/types";
import { AlertModalCore } from "@plane/ui";
import { renderFormattedDate } from "@plane/utils";
// hooks
import { useCycle } from "@/hooks/store/use-cycle";
import { useProjectState } from "@/hooks/store/use-project-state";
import { useTimeZoneConverter } from "@/hooks/use-timezone-converter";

type TMode = "start" | "end";

type Props = {
  /** Whether the modal is open */
  isOpen: boolean;
  /** "start" = Start Cycle; "end" = End Cycle */
  mode: TMode;
  /** Full cycle details, used for the name and incomplete items count */
  cycleDetails: ICycle;
  workspaceSlug: string;
  projectId: string;
  /** Callback to close the modal */
  handleClose: () => void;
};

export const CycleStartStopModal = observer(function CycleStartStopModal(props: Props) {
  const { isOpen, mode, cycleDetails, workspaceSlug, projectId, handleClose } = props;
  // translation
  const { t } = useTranslation();
  const SS = "cycle.start_stop";
  // state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [setInProgress, setSetInProgress] = useState<boolean>(true);
  const [markCompleted, setMarkCompleted] = useState<boolean>(true);
  // store
  const { startCycle, endCycle } = useCycle();
  const { getProjectStates } = useProjectState();
  const projectStates = getProjectStates(projectId);
  const hasInProgressState = projectStates?.some((s) => s.group === "started");
  const hasCompletedState = projectStates?.some((s) => s.group === "completed");
  // timezone converter
  const { renderFormattedDateInUserTimezone } = useTimeZoneConverter(projectId);

  const formattedDate =
    renderFormattedDateInUserTimezone(new Date().toISOString()) || renderFormattedDate(new Date()) || "";

  /**
   * Number of work items that are not yet done (not completed or cancelled).
   * Used to show the warning callout when ending a cycle.
   */
  const incompleteCount =
    (cycleDetails.total_issues ?? 0) - ((cycleDetails.completed_issues ?? 0) + (cycleDetails.cancelled_issues ?? 0));

  const handleSubmit = async () => {
    setIsSubmitting(true);
    try {
      if (mode === "start") {
        const willUpdateState = setInProgress && Boolean(hasInProgressState);
        await startCycle(workspaceSlug, projectId, cycleDetails.id, { set_in_progress: willUpdateState });
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${SS}.toast.started`),
          message: t(willUpdateState ? `${SS}.toast.started_moved` : `${SS}.toast.started_message`, {
            name: cycleDetails.name,
          }),
        });
      } else {
        const willMarkCompleted = markCompleted && Boolean(hasCompletedState) && incompleteCount > 0;
        await endCycle(workspaceSlug, projectId, cycleDetails.id, { mark_completed: willMarkCompleted });
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${SS}.toast.completed`),
          message: t(willMarkCompleted ? `${SS}.toast.completed_moved` : `${SS}.toast.completed_message`, {
            name: cycleDetails.name,
          }),
        });
      }
      handleClose();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${SS}.toast.failed`),
        message: t(`${SS}.try_again`),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const modalContent = (
    <div className="flex flex-col gap-3">
      {/* One sentence per mode rather than a verb spliced into a shared frame:
          the cycle name, the date and the verb sit in different places once the
          sentence is translated, so the whole thing has to be one key. */}
      <p className="break-words">
        {t(mode === "start" ? `${SS}.start.question` : `${SS}.end.question`, {
          name: cycleDetails.name,
          date: formattedDate,
        })}
      </p>

      {/* Option to move unstarted items to In Progress when starting a cycle */}
      {mode === "start" && hasInProgressState && (
        <div className="flex items-start gap-2.5 rounded-md border border-subtle bg-surface-2 p-3">
          <input
            type="checkbox"
            id="set_in_progress"
            checked={setInProgress}
            onChange={(e) => setSetInProgress(e.target.checked)}
            className="focus:ring-primary mt-0.5 h-4 w-4 cursor-pointer rounded border-subtle text-primary"
          />
          <label htmlFor="set_in_progress" className="text-xs cursor-pointer select-none">
            <span className="font-medium text-primary">{t(`${SS}.move_unstarted.label`)}</span>
            <p className="mt-0.5 text-secondary">{t(`${SS}.move_unstarted.hint`)}</p>
          </label>
        </div>
      )}

      {/* Warning if project has no In Progress state */}
      {mode === "start" && !hasInProgressState && (
        <div className="bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-400 flex items-start gap-2 rounded-md p-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
          <p className="text-13">
            <span className="font-medium">{t(`${SS}.no_in_progress.title`)}</span> {t(`${SS}.no_in_progress.hint`)}
          </p>
        </div>
      )}

      {/* Option to mark incomplete items as Completed when ending a cycle */}
      {mode === "end" && incompleteCount > 0 && hasCompletedState && (
        <div className="flex items-start gap-2.5 rounded-md border border-subtle bg-surface-2 p-3">
          <input
            type="checkbox"
            id="mark_completed"
            checked={markCompleted}
            onChange={(e) => setMarkCompleted(e.target.checked)}
            className="focus:ring-primary mt-0.5 h-4 w-4 cursor-pointer rounded border-subtle text-primary"
          />
          <label htmlFor="mark_completed" className="text-xs cursor-pointer select-none">
            <span className="font-medium text-primary">{t(`${SS}.mark_completed.label`)}</span>
            <p className="mt-0.5 text-secondary">{t(`${SS}.mark_completed.hint`, { count: incompleteCount })}</p>
          </label>
        </div>
      )}

      {/* Warning if ending cycle with incomplete work items when no Completed state exists */}
      {mode === "end" && incompleteCount > 0 && !hasCompletedState && (
        <div className="bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-400 flex items-start gap-2 rounded-md p-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
          <p className="text-13">
            <span className="font-medium">{t(`${SS}.no_completed.title`, { count: incompleteCount })}</span>{" "}
            {t(`${SS}.no_completed.hint`)}
          </p>
        </div>
      )}
    </div>
  );

  return (
    <AlertModalCore
      isOpen={isOpen}
      handleClose={handleClose}
      handleSubmit={handleSubmit}
      isSubmitting={isSubmitting}
      title={t(mode === "start" ? `${SS}.start.title` : `${SS}.end.title`)}
      content={modalContent}
      variant="primary"
      primaryButtonText={{
        default: t(mode === "start" ? `${SS}.start.title` : `${SS}.end.title`),
        loading: t(mode === "start" ? `${SS}.start.loading` : `${SS}.end.loading`),
      }}
      secondaryButtonText={t("common.cancel")}
    />
  );
});
