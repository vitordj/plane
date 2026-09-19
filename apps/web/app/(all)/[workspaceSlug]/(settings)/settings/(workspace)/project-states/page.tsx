/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState, useMemo } from "react";
import { observer } from "mobx-react";
import { useParams } from "react-router";
// components
import { PageHead } from "@/components/core/page-title";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { ToggleSwitch, AlertModalCore } from "@plane/ui";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { GroupList } from "@/components/project-states";
import { useTranslation } from "@plane/i18n";
// hooks
import { useCustomProjectState } from "@/hooks/store/use-custom-project-state";
import { ProjectStatesWorkspaceSettingsHeader } from "./header";

const ProjectStatesPage = observer(function ProjectStatesPage() {
  const { workspaceSlug } = useParams();
  const store = useCustomProjectState();
  const { t } = useTranslation();

  const PS = "workspace_settings.settings.project_states";

  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!workspaceSlug) return;
    Promise.all([store.fetchSettings(workspaceSlug), store.fetchStates(workspaceSlug)]).finally(() => {
      setIsLoading(false);
    });
  }, [workspaceSlug, store]);

  const [isConfirmationModalOpen, setIsConfirmationModalOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleToggle = () => {
    setIsConfirmationModalOpen(true);
  };

  const handleConfirmToggle = async () => {
    if (!workspaceSlug || !store.settings) return;
    setIsSubmitting(true);
    const nextValue = !store.settings.is_enabled;
    try {
      await store.updateSettings(workspaceSlug, {
        is_enabled: nextValue,
      });
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(nextValue ? `${PS}.toast.enabled` : `${PS}.toast.disabled`),
        message: t(nextValue ? `${PS}.confirm.enable_content` : `${PS}.confirm.disable_content`),
      });
    } catch (_e) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${PS}.toast.not_saved`),
        message: t(`${PS}.try_again`),
      });
    } finally {
      setIsSubmitting(false);
      setIsConfirmationModalOpen(false);
    }
  };

  const mappedStates = useMemo(() => {
    return (
      store.states?.map((st) => ({
        id: st.id,
        name: st.name,
        description: st.description || "",
        color: st.color,
        group: st.group as any,
        default: st.default,
        sequence: st.sequence,
        project_id: "",
        workspace_id: "",
        order: st.sequence,
      })) || []
    );
  }, [store.states]);

  const groupedStates = useMemo(() => {
    const groups: Record<string, any[]> = {
      backlog: [],
      unstarted: [],
      started: [],
      completed: [],
      cancelled: [],
    };
    mappedStates.forEach((state) => {
      if (groups[state.group]) {
        groups[state.group].push(state);
      }
    });
    return groups;
  }, [mappedStates]);

  const stateOperationsCallbacks = useMemo(
    () => ({
      createState: async (data: any) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        const res = await store.createState(workspaceSlug, {
          name: data.name,
          description: data.description || "",
          color: data.color,
          group: data.group,
          default: data.default || false,
        });
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${PS}.toast.state_created`),
        });
        return {
          id: res.id,
          name: res.name,
          description: res.description || "",
          color: res.color,
          group: res.group as any,
          default: res.default,
          sequence: res.sequence,
          project_id: "",
          workspace_id: "",
          order: res.sequence,
        };
      },
      updateState: async (stateId: string, data: any) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        const res = await store.updateState(workspaceSlug, stateId, {
          name: data.name,
          description: data.description || "",
          color: data.color,
          group: data.group,
          default: data.default || false,
        });
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${PS}.toast.state_updated`),
        });
        return {
          id: res.id,
          name: res.name,
          description: res.description || "",
          color: res.color,
          group: res.group as any,
          default: res.default,
          sequence: res.sequence,
          project_id: "",
          workspace_id: "",
          order: res.sequence,
        };
      },
      deleteState: async (stateId: string) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        await store.deleteState(workspaceSlug, stateId);
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${PS}.toast.state_deleted`),
        });
      },
      markStateAsDefault: async (stateId: string) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        await store.updateState(workspaceSlug, stateId, { default: true });
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t(`${PS}.toast.default_set`),
        });
      },
      moveStatePosition: async (stateId: string, data: any) => {
        if (!workspaceSlug) throw new Error("Workspace slug is required");
        await store.updateState(workspaceSlug, stateId, {
          sequence: data.sequence,
        });
      },
    }),
    [workspaceSlug, store, t]
  );

  if (isLoading) {
    return (
      <SettingsContentWrapper header={<ProjectStatesWorkspaceSettingsHeader />}>
        <div className="text-custom-text-300 flex h-full w-full items-center justify-center py-20">
          {t(`${PS}.loading`)}
        </div>
      </SettingsContentWrapper>
    );
  }

  return (
    <SettingsContentWrapper header={<ProjectStatesWorkspaceSettingsHeader />}>
      <PageHead title={t(`${PS}.list_heading`)} />
      <div className="flex max-w-4xl flex-col gap-6 p-6">
        <div className="flex items-center justify-between border-b border-subtle pb-6">
          <div>
            <h3 className="text-xl text-custom-text-100 font-medium">{t(`${PS}.heading`)}</h3>
            <p className="text-sm text-custom-text-300">{t(`${PS}.description`)}</p>
          </div>
          <ToggleSwitch value={store.settings?.is_enabled || false} onChange={handleToggle} size="lg" />
        </div>

        {store.settings?.is_enabled && (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <h4 className="text-lg text-custom-text-200 font-medium font-semibold">{t(`${PS}.list_heading`)}</h4>
            </div>

            <div className="mt-2">
              <GroupList
                groupedStates={groupedStates}
                stateOperationsCallbacks={stateOperationsCallbacks}
                isEditable={true}
                shouldTrackEvents={false}
              />
            </div>
          </div>
        )}
      </div>
      {isConfirmationModalOpen && (
        <AlertModalCore
          isOpen={isConfirmationModalOpen}
          handleClose={() => setIsConfirmationModalOpen(false)}
          handleSubmit={handleConfirmToggle}
          isSubmitting={isSubmitting}
          title={t(store.settings?.is_enabled ? `${PS}.confirm.disable_title` : `${PS}.confirm.enable_title`)}
          content={t(store.settings?.is_enabled ? `${PS}.confirm.disable_content` : `${PS}.confirm.enable_content`)}
          variant="primary"
          primaryButtonText={{
            loading: t("common.updating"),
            default: t("common.confirm"),
          }}
        />
      )}
    </SettingsContentWrapper>
  );
});

export default ProjectStatesPage;
