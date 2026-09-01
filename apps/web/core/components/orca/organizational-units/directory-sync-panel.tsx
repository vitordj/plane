/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
import { AlertTriangle, Check, Copy, RefreshCw } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
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

/** Copy that reports the outcome instead of failing silently. */
const copyWithFeedback = async (value: string, label: string) => {
  try {
    await copyTextToClipboard(value);
    setToast({ type: TOAST_TYPE.SUCCESS, title: "Copied", message: `${label} copied to your clipboard.` });
  } catch {
    setToast({ type: TOAST_TYPE.ERROR, title: "Could not copy", message: `Select and copy the ${label} manually.` });
  }
};

const formatTimestamp = (value: string | null): string => {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
};

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
        title: "Could not load the directory connection",
        message: "Please try again in a moment.",
      });
    } finally {
      setIsLoading(false);
    }
  }, [workspaceSlug, isAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateConnection = async (data: Partial<IDirectoryConnection>, successMessage: string) => {
    setIsBusy(true);
    try {
      const next = await directoryService.updateConnection(workspaceSlug, data);
      setConnection(next);
      setToast({ type: TOAST_TYPE.SUCCESS, title: "Saved", message: successMessage });
    } catch (error) {
      const message = (error as { error?: string })?.error ?? "The change could not be saved. Please try again.";
      setToast({ type: TOAST_TYPE.ERROR, title: "Not saved", message });
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
        title: "Token issued",
        message: "Copy it now — it is shown only this once.",
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not issued", message: "The token could not be created." });
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
        title: "Token revoked",
        message: "Provisioning is switched off until a new token is issued.",
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not revoked", message: "The token could not be revoked." });
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
        title: "Resynced",
        message: `${summary.memberships_created ?? 0} added, ${summary.memberships_deactivated ?? 0} withdrawn.`,
      });
    } catch {
      setToast({ type: TOAST_TYPE.ERROR, title: "Not resynced", message: "The resync could not be completed." });
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

  return (
    <div className="flex flex-col gap-6 border-t border-subtle pt-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h4 className="text-base font-medium text-custom-text-100">Directory sync</h4>
          <p className="text-sm text-custom-text-300">
            Let Microsoft Entra ID keep area membership up to date. Entra decides who belongs to an area; which
            projects an area grants, and at which role, stays set here.
          </p>
        </div>
        <ToggleSwitch
          value={connection.is_enabled}
          onChange={() =>
            void updateConnection(
              { is_enabled: !connection.is_enabled },
              connection.is_enabled ? "Provisioning is off." : "Provisioning is on."
            )
          }
          size="sm"
          disabled={isBusy || !connection.has_token}
        />
      </div>

      {!connection.has_token && (
        <div className="flex items-start gap-2 rounded-md bg-layer-1 p-3 text-sm text-custom-text-300">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
          <span>Issue a token before switching provisioning on — Entra cannot authenticate without one.</span>
        </div>
      )}

      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium text-custom-text-200">Tenant URL</span>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate rounded-md bg-layer-1 px-3 py-2 text-xs text-custom-text-200">
              {connection.scim_base_url}
            </code>
            <Button
              variant="secondary"
              size="sm"
              prependIcon={<Copy className="size-3.5" />}
              onClick={() => void copyWithFeedback(connection.scim_base_url, "Tenant URL")}
            >
              Copy
            </Button>
          </div>
          <span className="text-xs text-custom-text-400">
            Paste this into Provisioning → Admin Credentials in the Entra enterprise application.
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium text-custom-text-200">Secret token</span>
          {issuedToken ? (
            <div className="flex items-center gap-2">
              <code className="flex-1 truncate rounded-md bg-layer-1 px-3 py-2 text-xs text-custom-text-200">
                {issuedToken}
              </code>
              <Button
                variant="secondary"
                size="sm"
                prependIcon={<Copy className="size-3.5" />}
                onClick={() => void copyWithFeedback(issuedToken, "Secret token")}
              >
                Copy
              </Button>
            </div>
          ) : (
            <span className="text-sm text-custom-text-300">
              {connection.has_token
                ? `A token starting ${connection.token_prefix}… is installed. Last used ${formatTimestamp(
                    connection.token_last_used_at
                  )}.`
                : "No token is installed."}
            </span>
          )}
          <div className="flex items-center gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={() => void handleIssueToken()} disabled={isBusy}>
              {connection.has_token ? "Rotate token" : "Issue token"}
            </Button>
            {connection.has_token && (
              <Button variant="error-outline" size="sm" onClick={() => void handleRevokeToken()} disabled={isBusy}>
                Revoke
              </Button>
            )}
          </div>
          {connection.has_token && (
            <span className="text-xs text-custom-text-400">
              Rotating takes effect immediately, so provisioning fails until Entra is given the new token.
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-3 rounded-md bg-layer-1 p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-col">
            <span className="text-sm font-medium text-custom-text-200">Last sync</span>
            <span className="text-xs text-custom-text-400">{formatTimestamp(connection.last_sync_at)}</span>
          </div>
          <Button
            variant="secondary"
            size="sm"
            prependIcon={<RefreshCw className="size-3.5" />}
            onClick={() => void handleResync()}
            disabled={isBusy}
          >
            Resync now
          </Button>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-custom-text-300">
          <span>{lastSync.memberships_created ?? 0} added</span>
          <span>{lastSync.memberships_reactivated ?? 0} restored</span>
          <span>{lastSync.memberships_deactivated ?? 0} withdrawn</span>
        </div>
        <span className="text-xs text-custom-text-400">
          Resync replays what the directory already sent — it does not call Entra. Use it after inviting somebody the
          directory had already pushed.
        </span>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-custom-text-200">
          Pushed by the directory, not in this workspace ({unresolved.length})
        </span>
        {unresolved.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-custom-text-300">
            <Check className="size-4 text-green-500" />
            Everyone the directory sent is an active member of this workspace.
          </div>
        ) : (
          <>
            <ul className="flex flex-col divide-y divide-subtle rounded-md bg-layer-1">
              {unresolved.map((identity) => (
                <li key={identity.id} className="flex items-center justify-between gap-4 px-3 py-2 text-sm">
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate text-custom-text-200">{identity.display_name || identity.user_name}</span>
                    <span className="truncate text-xs text-custom-text-400">{identity.user_name}</span>
                  </div>
                  <span className="shrink-0 text-xs text-custom-text-400">
                    {identity.is_active ? "Not a workspace member" : "Inactive in the directory"}
                  </span>
                </li>
              ))}
            </ul>
            <span className="text-xs text-custom-text-400">
              Invite these people to the workspace and they join their areas automatically, or remove them from the
              group in Entra.
            </span>
          </>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <label className="flex items-start justify-between gap-4">
          <span className="flex flex-col">
            <span className="text-sm text-custom-text-200">Create areas for new groups</span>
            <span className="text-xs text-custom-text-400">
              When off, the directory can only fill areas you created and named to match.
            </span>
          </span>
          <ToggleSwitch
            value={connection.auto_create_units}
            onChange={() =>
              void updateConnection(
                { auto_create_units: !connection.auto_create_units },
                "Saved how new directory groups are handled."
              )
            }
            size="sm"
            disabled={isBusy}
          />
        </label>
        <label className="flex items-start justify-between gap-4">
          <span className="flex flex-col">
            <span className="text-sm text-custom-text-200">Let the directory remove people</span>
            <span className="text-xs text-custom-text-400">
              When off, sync only adds. People you added by hand are never removed either way.
            </span>
          </span>
          <ToggleSwitch
            value={connection.deprovision_removes_membership}
            onChange={() =>
              void updateConnection(
                { deprovision_removes_membership: !connection.deprovision_removes_membership },
                "Saved how removals from the directory are handled."
              )
            }
            size="sm"
            disabled={isBusy}
          />
        </label>
      </div>
    </div>
  );
});
