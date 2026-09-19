/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore, EModalWidth } from "@plane/ui";
// hooks
import { useIssuesStore } from "@/hooks/use-issue-layout-store";

type Props = {
  isOpen: boolean;
  issueIds: string[];
  onClose: () => void;
  onSuccess: () => void;
};

export const BulkArchiveConfirmModal = observer(function BulkArchiveConfirmModal(props: Props) {
  const { isOpen, issueIds, onClose, onSuccess } = props;
  // translation
  const { t } = useTranslation();
  const K = "issue.bulk_operations.archive_modal";
  // router
  const { workspaceSlug, projectId } = useParams();
  // store
  const {
    issues: { archiveBulkIssues },
  } = useIssuesStore();
  // state
  const [isArchiving, setIsArchiving] = useState(false);

  const handleArchive = async () => {
    if (!workspaceSlug || !projectId || issueIds.length === 0) return;
    setIsArchiving(true);
    try {
      if (archiveBulkIssues) {
        await archiveBulkIssues(workspaceSlug.toString(), projectId.toString(), issueIds);
      }
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${K}.toast.archived_title`),
        message: t(`${K}.toast.archived`, { count: issueIds.length }),
      });
      onSuccess();
      onClose();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${K}.toast.not_archived`),
        message: t("issue.bulk_operations.try_again"),
      });
    } finally {
      setIsArchiving(false);
    }
  };

  return (
    <AlertModalCore
      isOpen={isOpen}
      handleClose={onClose}
      handleSubmit={handleArchive}
      isSubmitting={isArchiving}
      title={t(`${K}.title`)}
      variant="primary"
      width={EModalWidth.SM}
      primaryButtonText={{
        loading: t(`${K}.loading`),
        default: t(`${K}.confirm`, { count: issueIds.length }),
      }}
      content={t(`${K}.content`, { count: issueIds.length })}
    />
  );
});
