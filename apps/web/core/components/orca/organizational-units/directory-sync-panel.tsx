/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { AlertTriangle, Check, Copy, RefreshCw } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel, resolveOrcaErrorKey } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
import type { IDirectoryConnection, IDirectoryIdentity } from "@plane/types";
import { Loader, ToggleSwitch } from "@plane/ui";
import { copyTextToClipboard } from "@plane/utils";
// hooks
import { useUserPermissions } from "@/hooks/store/user";
// services
import { DirectoryService } from "@/services/orca/directory.service";

type Props = {
  workspaceSlug: string;
};

const directoryService = new DirectoryService();

const OU = "workspace_settings.settings.organizational_units";
const DS = `${OU}.directory_sync`;

/**
 * @description Directory (SCIM) provisioning for areas: the tenant URL and
 * bearer token an administrator pastes into the Microsoft Entra ID enterprise
 * application, plus the report of people Entra pushed who are not members of
 * this workspace.
 *
 * The report is the part worth reading. An area never invites anyone, so
 * somebody who is in the Entra group but not in the workspace is skipped
 * deliberately, not lost — inviting them to the workspace is enough, and their
 * area membership appears on the next sync.
 */
export const DirectorySyncPanel = observer(function DirectorySyncPanel(props: Props) {
  const { workspaceSlug } = props;
  const { t } = useTranslation();
  // store hooks
  const { allowPermissions } = useUserPermissions();
  // Every endpoint behind this panel is workspace-admin only, so a member
  // would only ever see a wall of permission errors. Render nothing for them.
  const isAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  const [connection, setConnection] = useState<IDirectoryConnection | null>(null);
  const [unresolved, setUnresolved] = useState<IDirectoryIdentity[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  // Held in state, never re-fetched: the API returns the token exactly once.
  const [issuedToken, setIssuedToken] = useState<string | null>(null);

  /** Copy that reports the outcome instead of failing silently. */
  const copyWithFeedback = async (value: string, label: string) => {
    try {
      await copyTextToClipboard(value);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${DS}.toast.copied_title`),
        message: t(`${DS}.toast.copied`, { label }),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${DS}.toast.not_copied_title`),
        message: t(`${DS}.toast.not_copied`, { label }),
      });
    }
  };

  const formatTimestamp = (value: string | null): string => {
    if (!value) return t(`${DS}.never`);
    return new Date(value).toLocaleString();
  };

  const load = useCallback(async () => {
    if (!isAdmin) {
      setIsLoading(false);
      return;
    }
    try {
      const [nextConnection, nextUnresolved] = await Promise.all([
        directoryService.getConnection(workspaceSlug),
        directoryService.getUnresolvedIdentities(workspaceSlug),
      ]);
      setConnection(nextConnection);
      setUnresolved(nextUnresolved);
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${DS}.toast.load_failed_title`),
        message: t(`${DS}.toast.load_failed`),
      });
    } finally {
      setIsLoading(false);
    }
  }, [workspaceSlug, isAdmin, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateConnection = async (data: Partial<IDirectoryConnection>, successMessage: string) => {
    setIsBusy(true);
    try {
      const next = await directoryService.updateConnection(workspaceSlug, data);
      setConnection(next);
      setToast({ type: TOAST_TYPE.SUCCESS, title: t(`${OU}.toast.saved`), message: successMessage });
    } catch (error) {
      // The API answers with a stable error_code; fall back to this panel's own
      // wording rather than to the English prose the body also carries.
      const message = t(resolveOrcaErrorKey(error) ?? `${DS}.toast.save_failed`);
      setToast({ type: TOAST_TYPE.ERROR, title: t(`${OU}.toast.not_saved`), message });
    } finally {
      setIsBusy(false);
    }
  };

  const handleIssueToken = async () => {
    setIsBusy(true);
    try {
      const next = await directoryService.issueToken(workspaceSlug);
      setConnection(next);
      setIssuedToken(next.token);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${DS}.toast.token_issued_title`),
        message: t(`${DS}.toast.token_issued`),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${DS}.toast.token_not_issued_title`),
        message: t(`${DS}.toast.token_not_issued`),
      });
    } finally {
      setIsBusy(false);
    }
  };

  const handleRevokeToken = async () => {
    setIsBusy(true);
    try {
      await directoryService.revokeToken(workspaceSlug);
      setIssuedToken(null);
      await load();
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${DS}.toast.token_revoked_title`),
        message: t(`${DS}.toast.token_revoked`),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${DS}.toast.token_not_revoked_title`),
        message: t(`${DS}.toast.token_not_revoked`),
      });
    } finally {
      setIsBusy(false);
    }
  };

  const handleResync = async () => {
    setIsBusy(true);
    try {
      const summary = await directoryService.resync(workspaceSlug);
      await load();
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t(`${DS}.toast.resynced_title`),
        message: t(`${DS}.toast.resynced`, {
          added: summary.memberships_created ?? 0,
          withdrawn: summary.memberships_deactivated ?? 0,
        }),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t(`${DS}.toast.not_resynced_title`),
        message: t(`${DS}.toast.not_resynced`),
      });
    } finally {
      setIsBusy(false);
    }
  };

  const lastSync = useMemo(() => connection?.last_sync_summary ?? {}, [connection]);

  if (!isAdmin) return null;

  if (isLoading) {
    return (
      <Loader className="flex flex-col gap-2">
        <Loader.Item height="40px" />
        <Loader.Item height="80px" />
      </Loader>
    );
  }

  if (!connection) return null;

  const tenantUrlLabel = t(`${DS}.tenant_url`);
  const secretTokenLabel = t(`${DS}.secret_token`);

  return (
    <div className="flex flex-col gap-6 border-t border-subtle pt-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h4 className="text-base text-custom-text-100 font-medium">{t(`${DS}.heading`)}</h4>
          <p className="text-sm text-custom-text-300">{t(`${DS}.description`)}</p>
        </div>
        <ToggleSwitch
          value={connection.is_enabled}
          onChange={() =>
            void updateConnection(
              { is_enabled: !connection.is_enabled },
              connection.is_enabled ? t(`${DS}.provisioning_off`) : t(`${DS}.provisioning_on`)
            )
          }
          size="sm"
          disabled={isBusy || !connection.has_token}
        />
      </div>

      {!connection.has_token && (
        <div className="text-sm text-custom-text-300 flex items-start gap-2 rounded-md bg-layer-1 p-3">
          <AlertTriangle className="text-amber-500 mt-0.5 size-4 shrink-0" />
          <span>{t(`${DS}.no_token_warning`)}</span>
        </div>
      )}

      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-sm text-custom-text-200 font-medium">{tenantUrlLabel}</span>
          <div className="flex items-center gap-2">
            <code className="text-xs text-custom-text-200 flex-1 truncate rounded-md bg-layer-1 px-3 py-2">
              {connection.scim_base_url}
            </code>
            <Button
              variant="secondary"
              size="sm"
              prependIcon={<Copy className="size-3.5" />}
              onClick={() => void copyWithFeedback(connection.scim_base_url, tenantUrlLabel)}
            >
              {t(`${DS}.copy`)}
            </Button>
          </div>
          <span className="text-xs text-custom-text-400">{t(`${DS}.tenant_url_hint`)}</span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-sm text-custom-text-200 font-medium">{secretTokenLabel}</span>
          {issuedToken ? (
            <div className="flex items-center gap-2">
              <code className="text-xs text-custom-text-200 flex-1 truncate rounded-md bg-layer-1 px-3 py-2">
                {issuedToken}
              </code>
              <Button
                variant="secondary"
                size="sm"
                prependIcon={<Copy className="size-3.5" />}
                onClick={() => void copyWithFeedback(issuedToken, secretTokenLabel)}
              >
                {t(`${DS}.copy`)}
              </Button>
            </div>
          ) : (
            <span className="text-sm text-custom-text-300">
              {connection.has_token
                ? t(`${DS}.token_installed`, {
                    prefix: connection.token_prefix,
                    last_used: formatTimestamp(connection.token_last_used_at),
                  })
                : t(`${DS}.no_token`)}
            </span>
          )}
          <div className="flex items-center gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={() => void handleIssueToken()} disabled={isBusy}>
              {connection.has_token ? t(`${DS}.rotate_token`) : t(`${DS}.issue_token`)}
            </Button>
            {connection.has_token && (
              <Button variant="error-outline" size="sm" onClick={() => void handleRevokeToken()} disabled={isBusy}>
                {t(`${DS}.revoke`)}
              </Button>
            )}
          </div>
          {connection.has_token && <span className="text-xs text-custom-text-400">{t(`${DS}.rotate_hint`)}</span>}
        </div>
      </div>

      <div className="flex flex-col gap-3 rounded-md bg-layer-1 p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-col">
            <span className="text-sm text-custom-text-200 font-medium">{t(`${DS}.last_sync`)}</span>
            <span className="text-xs text-custom-text-400">{formatTimestamp(connection.last_sync_at)}</span>
          </div>
          <Button
            variant="secondary"
            size="sm"
            prependIcon={<RefreshCw className="size-3.5" />}
            onClick={() => void handleResync()}
            disabled={isBusy}
          >
            {t(`${DS}.resync_now`)}
          </Button>
        </div>
        <div className="text-xs text-custom-text-300 flex flex-wrap gap-x-6 gap-y-1">
          <span>{t(`${DS}.summary_added`, { count: lastSync.memberships_created ?? 0 })}</span>
          <span>{t(`${DS}.summary_restored`, { count: lastSync.memberships_reactivated ?? 0 })}</span>
          <span>{t(`${DS}.summary_withdrawn`, { count: lastSync.memberships_deactivated ?? 0 })}</span>
        </div>
        <span className="text-xs text-custom-text-400">{t(`${DS}.resync_hint`)}</span>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm text-custom-text-200 font-medium">
          {t(`${DS}.unresolved_heading`, { count: unresolved.length })}
        </span>
        {unresolved.length === 0 ? (
          <div className="text-sm text-custom-text-300 flex items-center gap-2">
            <Check className="text-green-500 size-4" />
            {t(`${DS}.unresolved_empty`)}
          </div>
        ) : (
          <>
            <ul className="flex flex-col divide-y divide-subtle rounded-md bg-layer-1">
              {unresolved.map((identity) => (
                <li key={identity.id} className="text-sm flex items-center justify-between gap-4 px-3 py-2">
                  <div className="flex min-w-0 flex-col">
                    <span className="text-custom-text-200 truncate">{identity.display_name || identity.user_name}</span>
                    <span className="text-xs text-custom-text-400 truncate">{identity.user_name}</span>
                  </div>
                  <span className="text-xs text-custom-text-400 shrink-0">
                    {identity.is_active ? t(`${DS}.not_workspace_member`) : t(`${DS}.inactive_in_directory`)}
                  </span>
                </li>
              ))}
            </ul>
            <span className="text-xs text-custom-text-400">{t(`${DS}.unresolved_hint`)}</span>
          </>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex items-start justify-between gap-4">
          <span className="flex flex-col">
            <span className="text-sm text-custom-text-200">{t(`${DS}.auto_create_title`)}</span>
            <span className="text-xs text-custom-text-400">{t(`${DS}.auto_create_hint`)}</span>
          </span>
          <ToggleSwitch
            value={connection.auto_create_units}
            onChange={() =>
              void updateConnection({ auto_create_units: !connection.auto_create_units }, t(`${DS}.auto_create_saved`))
            }
            size="sm"
            disabled={isBusy}
          />
        </div>
        <div className="flex items-start justify-between gap-4">
          <span className="flex flex-col">
            <span className="text-sm text-custom-text-200">{t(`${DS}.deprovision_title`)}</span>
            <span className="text-xs text-custom-text-400">{t(`${DS}.deprovision_hint`)}</span>
          </span>
          <ToggleSwitch
            value={connection.deprovision_removes_membership}
            onChange={() =>
              void updateConnection(
                { deprovision_removes_membership: !connection.deprovision_removes_membership },
                t(`${DS}.deprovision_saved`)
              )
            }
            size="sm"
            disabled={isBusy}
          />
        </div>
      </div>
    </div>
  );
});
