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

export const BulkDeleteConfirmModal = observer(function BulkDeleteConfirmModal(props: Props) {
  const { isOpen, issueIds, onClose, onSuccess } = props;
  // translation
  const { t } = useTranslation();
  const K = "issue.bulk_operations.delete_modal";
  // router
  const { workspaceSlug, projectId } = useParams();
  // store
  const {
    issues: { removeBulkIssues },
  } = useIssuesStore();
  // state
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    if (!workspaceSlug || !projectId || issueIds.length === 0) return;
    setIsDeleting(true);
    try {
      await removeBulkIssues(workspaceSlug.toString(), projectId.toString(), issueIds);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${K}.toast.deleted_title`),
        message: t(`${K}.toast.deleted`, { count: issueIds.length }),
      });
      onSuccess();
      onClose();
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${K}.toast.not_deleted`),
        message: t("issue.bulk_operations.try_again"),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <AlertModalCore
      isOpen={isOpen}
      handleClose={onClose}
      handleSubmit={handleDelete}
      isSubmitting={isDeleting}
      title={t(`${K}.title`)}
      variant="danger"
      width={EModalWidth.SM}
      primaryButtonText={{
        loading: t(`${K}.loading`),
        default: t(`${K}.confirm`, { count: issueIds.length }),
      }}
      content={t(`${K}.content`, { count: issueIds.length })}
    />
  );
});
