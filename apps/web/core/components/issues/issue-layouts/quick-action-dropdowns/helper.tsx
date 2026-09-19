/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useCallback } from "react";
import { XCircle, ArchiveRestoreIcon } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { LinkIcon, CopyIcon, NewTabIcon, EditIcon, ArchiveIcon, TrashIcon } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { EIssuesStoreType, TIssue } from "@plane/types";
import type { TContextMenuItem } from "@plane/ui";
import { copyUrlToClipboard, generateWorkItemLink, copyTextToClipboard, htmlToPlainText } from "@plane/utils";
import { IssueService } from "@/services/issue";
import { createCopyMenuWithDuplication } from "./copy-menu-helper";

/**
 * @description The toast shown when a menu item is rendered in a context that
 * does not implement its action. Passed in already translated: this helper is
 * module scope and cannot call t(), while every call site is inside a hook.
 */
export type TUnavailableActionCopy = {
  title: string;
  message: string;
};

// Generic helper function to handle optional function calls gracefully
// Overload for functions without parameters
export function handleOptionalAction(
  optionalFn: (() => void) | (() => Promise<void>) | undefined,
  unavailable: TUnavailableActionCopy
): void;

// Overload for functions with one parameter
export function handleOptionalAction<T>(
  optionalFn: ((param: T) => void) | ((param: T) => Promise<void>) | undefined,
  unavailable: TUnavailableActionCopy,
  param: T
): void;

// Implementation
export function handleOptionalAction<T>(
  optionalFn: (() => void) | (() => Promise<void>) | ((param: T) => void) | ((param: T) => Promise<void>) | undefined,
  unavailable: TUnavailableActionCopy,
  param?: T
): void {
  if (optionalFn) {
    if (param !== undefined) {
      (optionalFn as (param: T) => void | Promise<void>)(param);
    } else {
      (optionalFn as () => void | Promise<void>)();
    }
  } else {
    setToast({
      type: TOAST_TYPE.ERROR,
      title: unavailable.title,
      message: unavailable.message,
    });
  }
}

export interface MenuItemFactoryProps {
  issue: TIssue;
  workspaceSlug?: string;
  projectIdentifier?: string;
  activeLayout?: string;
  isEditingAllowed: boolean;
  isArchivingAllowed?: boolean;
  isDeletingAllowed: boolean;
  isRestoringAllowed?: boolean;
  isInArchivableGroup?: boolean;
  issueTypeDetail?: { is_active?: boolean };
  // Action handlers
  setIssueToEdit: (issue: TIssue | undefined) => void;
  setCreateUpdateIssueModal: (open: boolean) => void;
  setDeleteIssueModal: (open: boolean) => void;
  setArchiveIssueModal?: (open: boolean) => void;
  setDuplicateWorkItemModal?: (open: boolean) => void;
  handleRemoveFromView?: () => void;
  handleRestore?: () => Promise<void>;
  // External handlers
  handleDelete?: () => Promise<void>;
  handleUpdate?: (data: TIssue) => Promise<void>;
  handleArchive?: () => Promise<void>;
  // Context-specific data
  cycleId?: string;
  moduleId?: string;
  storeType?: EIssuesStoreType;
}

// Common action handlers hook
export const useIssueActionHandlers = (props: MenuItemFactoryProps) => {
  const { issue, workspaceSlug, projectIdentifier, handleRestore } = props;
  const { t } = useTranslation();

  const Q = "issue.quick_actions";
  const unavailableAction: TUnavailableActionCopy = {
    title: t(`${Q}.unavailable_title`),
    message: t(`${Q}.unavailable_message`),
  };

  const workItemLink = useMemo(
    () =>
      generateWorkItemLink({
        workspaceSlug,
        projectId: issue?.project_id,
        issueId: issue?.id,
        projectIdentifier,
        sequenceId: issue?.sequence_id,
      }),
    [workspaceSlug, projectIdentifier, issue]
  );

  const handleCopyIssueLink = () =>
    copyUrlToClipboard(workItemLink).then(() =>
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${Q}.toast.link_copied`),
        message: t(`${Q}.toast.link_copied_message`),
      })
    );

  const handleOpenInNewTab = () => window.open(workItemLink, "_blank");

  const handleIssueRestore = async () => {
    if (!handleRestore) {
      handleOptionalAction(handleRestore, unavailableAction);
      return;
    }
    try {
      await handleRestore();
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${Q}.toast.restored`),
        message: t(`${Q}.toast.restored_message`),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${Q}.toast.not_restored`),
        message: t(`${Q}.try_again`),
      });
    }
  };

  /**
   * @description Orca Custom Helper: Resolves the issue description HTML and converts it to clean formatted plain text.
   * If the description is not loaded in the lightweight issue object, fetches the full issue from the API.
   * @returns {Promise<string>} Clean plain text description with preserved line breaks.
   */
  const getOrFetchDescription = async (): Promise<string> => {
    if (issue?.description_html !== undefined) {
      return htmlToPlainText(issue.description_html);
    }
    if (!workspaceSlug || !issue?.project_id || !issue?.id) {
      return "";
    }
    try {
      const issueService = new IssueService();
      const fullIssue = await issueService.retrieve(workspaceSlug, issue.project_id, issue.id);
      return htmlToPlainText(fullIssue?.description_html || "");
    } catch (e) {
      console.error("Failed to fetch issue description", e);
      return "";
    }
  };

  /**
   * @description Orca Custom Handler: Copies the issue title to the clipboard.
   */
  const handleCopyIssueTitle = () =>
    copyTextToClipboard(issue?.name || "").then(() =>
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${Q}.toast.title_copied`),
        message: t(`${Q}.toast.title_copied_message`),
      })
    );

  /**
   * @description Orca Custom Handler: Copies the issue description (as plain text) to the clipboard.
   */
  const handleCopyIssueDescription = async () => {
    const descriptionText = await getOrFetchDescription();
    if (descriptionText === "") {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${Q}.toast.no_description`),
        message: t(`${Q}.toast.no_description_message`),
      });
      return;
    }
    return copyTextToClipboard(descriptionText).then(() =>
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${Q}.toast.description_copied`),
        message: t(`${Q}.toast.description_copied_message`),
      })
    );
  };

  /**
   * @description Orca Custom Handler: Smart copy for issue details.
   * Copies title on the first line and formatted description on following lines to clipboard,
   * or just the title if no description exists.
   * @returns {Promise<void>}
   */
  const handleCopyIssueDetails = async () => {
    const titleText = (issue?.name || "").trim();
    const descriptionText = await getOrFetchDescription();
    const textToCopy = descriptionText ? `${titleText}\n\n${descriptionText}` : titleText;
    return copyTextToClipboard(textToCopy).then(() =>
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(descriptionText ? `${Q}.toast.details_copied` : `${Q}.toast.title_copied`),
        message: t(descriptionText ? `${Q}.toast.details_copied_message` : `${Q}.toast.title_copied_message`),
      })
    );
  };

  /**
   * @description Orca Custom Handler: Copies both issue title and description, separated by newlines, to the clipboard.
   */
  const handleCopyIssueTitleAndDescription = handleCopyIssueDetails;

  return {
    workItemLink,
    handleCopyIssueLink,
    handleOpenInNewTab,
    handleIssueRestore,
    handleCopyIssueTitle,
    handleCopyIssueDescription,
    handleCopyIssueTitleAndDescription,
    handleCopyIssueDetails,
  };
};

export const useMenuItemFactory = (props: MenuItemFactoryProps) => {
  const { t } = useTranslation();

  const Q = "issue.quick_actions";
  const unavailableAction: TUnavailableActionCopy = {
    title: t(`${Q}.unavailable_title`),
    message: t(`${Q}.unavailable_message`),
  };
  const actionHandlers = useIssueActionHandlers(props);

  const {
    issue,
    activeLayout = "",
    isEditingAllowed,
    isArchivingAllowed = false,
    isDeletingAllowed,
    isRestoringAllowed = false,
    isInArchivableGroup = false,
    issueTypeDetail,
    setIssueToEdit,
    setCreateUpdateIssueModal,
    setDeleteIssueModal,
    setArchiveIssueModal,
    setDuplicateWorkItemModal,
    handleRemoveFromView,
  } = props;

  const createEditMenuItem = (customEditAction?: () => void): TContextMenuItem => ({
    key: "edit",
    title: t("common.actions.edit"),
    icon: EditIcon,
    action:
      customEditAction ||
      (() => {
        setIssueToEdit(issue);
        setCreateUpdateIssueModal(true);
      }),
    shouldRender: isEditingAllowed,
  });

  const createCopyMenuItem = (workspaceSlug?: string): TContextMenuItem => {
    const baseItem = {
      key: "make-a-copy",
      title: t("common.actions.make_a_copy"),
      icon: CopyIcon,
      action: () => {
        setCreateUpdateIssueModal(true);
      },
      shouldRender: isEditingAllowed && (issueTypeDetail?.is_active ?? true),
    };

    return createCopyMenuWithDuplication({
      baseItem,
      activeLayout,
      setCreateUpdateIssueModal,
      setDuplicateWorkItemModal,
      workspaceSlug,
    });
  };

  const createOpenInNewTabMenuItem = (): TContextMenuItem => ({
    key: "open-in-new-tab",
    title: t("common.actions.open_in_new_tab"),
    icon: NewTabIcon,
    action: actionHandlers.handleOpenInNewTab,
  });

  const createCopyLinkMenuItem = (): TContextMenuItem => ({
    key: "copy-link",
    title: t("common.actions.copy_link"),
    icon: LinkIcon,
    action: actionHandlers.handleCopyIssueLink,
  });

  /**
   * @description Orca Custom Menu Item: Returns a smart copy details menu item.
   * Copies the title and description (if available) directly to the clipboard without a dropdown submenu.
   */
  const createCopyDetailsMenuItem = (): TContextMenuItem => ({
    key: "copy-details",
    title: t("common.actions.copy_details"),
    icon: CopyIcon,
    action: actionHandlers.handleCopyIssueDetails,
    shouldRender: true,
  });

  const createRemoveFromCycleMenuItem = (): TContextMenuItem => ({
    key: "remove-from-cycle",
    title: t(`${Q}.remove_from_cycle`),
    icon: XCircle,
    action: () => handleOptionalAction(handleRemoveFromView, unavailableAction),
    shouldRender: isEditingAllowed,
  });

  const createRemoveFromModuleMenuItem = (): TContextMenuItem => ({
    key: "remove-from-module",
    title: t(`${Q}.remove_from_module`),
    icon: XCircle,
    action: () => handleOptionalAction(handleRemoveFromView, unavailableAction),
    shouldRender: isEditingAllowed,
  });

  const createArchiveMenuItem = (): TContextMenuItem => ({
    key: "archive",
    title: t("common.actions.archive"),
    description: isInArchivableGroup ? undefined : t("issue.archive.description"),
    icon: ArchiveIcon,
    className: "items-start",
    iconClassName: "mt-1",
    action: () => handleOptionalAction(setArchiveIssueModal, unavailableAction, true),
    disabled: !isInArchivableGroup,
    shouldRender: isArchivingAllowed,
  });

  const createRestoreMenuItem = (): TContextMenuItem => ({
    key: "restore",
    title: t("common.actions.restore"),
    icon: ArchiveRestoreIcon,
    action: actionHandlers.handleIssueRestore,
    shouldRender: isRestoringAllowed,
  });

  const createDeleteMenuItem = (): TContextMenuItem => ({
    key: "delete",
    title: t("common.actions.delete"),
    icon: TrashIcon,
    action: () => {
      setDeleteIssueModal(true);
    },
    shouldRender: isDeletingAllowed,
  });

  return {
    ...actionHandlers,
    createEditMenuItem,
    createCopyMenuItem,
    createOpenInNewTabMenuItem,
    createCopyLinkMenuItem,
    createCopyDetailsMenuItem,
    createCopySubmenuItem: createCopyDetailsMenuItem,
    createRemoveFromCycleMenuItem,
    createRemoveFromModuleMenuItem,
    createArchiveMenuItem,
    createRestoreMenuItem,
    createDeleteMenuItem,
  };
};

// Predefined menu item sets for different contexts
export const useProjectIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createEditMenuItem(),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    [factory]
  );
};

export const useWorkItemDetailMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createCopyMenuItem(props.workspaceSlug),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createRestoreMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
    [factory, props.workspaceSlug]
  );
};

export const useAllIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createEditMenuItem(),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    [factory]
  );
};

export const useCycleIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const { setIssueToEdit, issue, cycleId, setCreateUpdateIssueModal } = props;
  const factory = useMenuItemFactory(props);

  const customEditAction = useCallback(() => {
    setIssueToEdit({
      ...issue,
      cycle_id: cycleId ?? null,
    });
    setCreateUpdateIssueModal(true);
  }, [setIssueToEdit, issue, cycleId, setCreateUpdateIssueModal]);

  return useMemo(
    () => [
      factory.createEditMenuItem(customEditAction),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createRemoveFromCycleMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
    [factory, customEditAction]
  );
};

export const useModuleIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const { setIssueToEdit, issue, moduleId, setCreateUpdateIssueModal } = props;
  const factory = useMenuItemFactory(props);

  const customEditAction = useCallback(() => {
    setIssueToEdit({
      ...issue,
      module_ids: moduleId ? [moduleId] : [],
    });
    setCreateUpdateIssueModal(true);
  }, [setIssueToEdit, issue, moduleId, setCreateUpdateIssueModal]);

  return useMemo(
    () => [
      factory.createEditMenuItem(customEditAction),
      factory.createCopyMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createRemoveFromModuleMenuItem(),
      factory.createArchiveMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
    [factory, customEditAction]
  );
};

export const useArchivedIssueMenuItems = (props: MenuItemFactoryProps): TContextMenuItem[] => {
  const factory = useMenuItemFactory(props);

  return useMemo(
    () => [
      factory.createRestoreMenuItem(),
      factory.createOpenInNewTabMenuItem(),
      factory.createCopyLinkMenuItem(),
      factory.createCopyDetailsMenuItem(),
      factory.createDeleteMenuItem(),
    ],
    [factory]
  );
};
