/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams as useNextParams } from "next/navigation";
import { useParams as useReactParams } from "react-router";
// types
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IIssueLabel } from "@plane/types";
// ui
import { AlertModalCore } from "@plane/ui";
// hooks
import { useLabel } from "@/hooks/store/use-label";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  data: IIssueLabel | null;
  handleDelete?: (label: IIssueLabel) => Promise<void>;
};

export const DeleteLabelModal = observer(function DeleteLabelModal(props: Props) {
  const { isOpen, onClose, data, handleDelete } = props;
  // router
  const nextParams = useNextParams();
  const reactParams = useReactParams();
  const workspaceSlug = nextParams?.workspaceSlug || reactParams?.workspaceSlug;
  const projectId = nextParams?.projectId || reactParams?.projectId;

  // translation
  const { t } = useTranslation();
  // store hooks
  const { deleteLabel } = useLabel();
  // states
  const [isDeleteLoading, setIsDeleteLoading] = useState(false);

  const handleClose = () => {
    onClose();
    setIsDeleteLoading(false);
  };

  const handleDeletion = async () => {
    if (!workspaceSlug || !data) return;
    if (!projectId && !handleDelete) return;

    setIsDeleteLoading(true);

    try {
      if (handleDelete) {
        await handleDelete(data);
      } else if (projectId) {
        await deleteLabel(workspaceSlug.toString(), projectId.toString(), data.id);
      }
      handleClose();
    } catch (err: any) {
      setIsDeleteLoading(false);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("project_settings.labels.delete.not_deleted"),
        // The server speaks English; prefer the translated line and keep its
        // text only when it says something this message cannot.
        message: err?.error ?? t("project_settings.labels.delete.try_again"),
      });
    }
  };

  return (
    <AlertModalCore
      handleClose={handleClose}
      handleSubmit={handleDeletion}
      isSubmitting={isDeleteLoading}
      isOpen={isOpen}
      title={t("project_settings.labels.delete.title")}
      content={
        <div className="flex flex-col gap-2">
          <span>{t("project_settings.labels.delete.question")}</span>
          <span>{t("project_settings.labels.delete.warning")}</span>
        </div>
      }
      variant="danger"
    />
  );
});
