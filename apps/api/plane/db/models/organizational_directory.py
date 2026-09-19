# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Directory (SCIM) sidecar tables for the Orca organizational layer.

Microsoft Entra ID provisions users and groups into Plane by pushing SCIM 2.0
resources. These tables are the faithful mirror of what the directory pushed;
nothing here grants access on its own. Access flows in three deliberate steps,
each one testable in isolation:

```
Entra  ──SCIM──▶  directory mirror   ──projector──▶  organizational layer  ──reconciler──▶  ProjectMember
                  (this module)                      (organizational_unit)                  (core, untouched)
```

Keeping the mirror separate from the organizational layer is what lets the
fork honour two rules at once:

* **A unit never invites anyone.** The directory may legitimately contain
  people who have no Plane account or are not members of this workspace. Their
  identities and group memberships are still recorded here — marked
  ``unresolved`` and surfaced in the report — and they turn into real unit
  memberships by themselves the day those people join the workspace.
* **Manual decisions win.** The projector only ever removes rows the directory
  itself created (``sync_source = scim``), so a membership an admin added by
  hand is never collateral damage of a group change upstream.

Per FORK.md no core table is touched: these are relational sidecars under the
fork's own ``organizational_*`` prefix, served from the ``/api/orca/`` namespace.
"""

# Python imports
import hashlib
import secrets

# Django imports
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel
from .organizational_unit import DirectoryIdentityState

# Bearer tokens are shown once and stored only as a digest, like any other
# credential. 32 bytes of urlsafe entropy is ~43 characters.
DIRECTORY_TOKEN_BYTES = 32
DIRECTORY_TOKEN_PREFIX_LENGTH = 8


def generate_directory_token() -> str:
    """
    Mint a SCIM bearer token.

    @description Returned in the clear exactly once, when an admin issues or
    rotates it; only its digest is persisted.

    @returns: The token to paste into the Entra provisioning form.
    """
    return secrets.token_urlsafe(DIRECTORY_TOKEN_BYTES)


def hash_directory_token(token: str) -> str:
    """
    Digest a SCIM bearer token for storage and comparison.

    @description SHA-256 rather than a password hasher on purpose: the token is
    high-entropy machine-generated material, and every SCIM request has to
    verify it, so the comparison must stay cheap and constant-time.

    @param token: The token in the clear.
    @returns: Hex digest stored in ``token_hash``.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class OrganizationalDirectoryConnection(BaseModel):
    """
    Per-workspace configuration of the external directory connection.

    Attributes:
        workspace (Workspace): The workspace being provisioned into.
        provider (str): Directory product, informational (``entra``).
        is_enabled (bool): SCIM requests are rejected while this is off, so an
            admin can cut the directory off without deleting the token.
        tenant_id (str): Entra tenant id, recorded for operators to verify
            which directory a workspace is wired to.
        token_hash (str): SHA-256 of the bearer token. Empty until issued.
        token_prefix (str): First characters of the token, so the UI can show
            which credential is installed without being able to reveal it.
        token_issued_at (datetime): When the current token was minted.
        token_last_used_at (datetime): Last authenticated SCIM request.
        auto_create_units (bool): Whether a pushed group with no bound unit
            creates one. Off means the directory may only fill units an admin
            created and bound by hand.
        deprovision_removes_membership (bool): Whether a person going inactive
            in the directory deactivates the unit memberships it created.
        last_sync_at (datetime): Last time a SCIM write changed anything.
        last_sync_summary (dict): Counters from that write, for the UI.
    """

    workspace = models.OneToOneField(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="directory_connection",
    )
    provider = models.CharField(max_length=30, default="entra")
    is_enabled = models.BooleanField(default=False)
    tenant_id = models.CharField(max_length=255, blank=True, default="")
    token_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    token_prefix = models.CharField(max_length=12, blank=True, default="")
    token_issued_at = models.DateTimeField(null=True, blank=True)
    token_last_used_at = models.DateTimeField(null=True, blank=True)
    auto_create_units = models.BooleanField(default=True)
    deprovision_removes_membership = models.BooleanField(default=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_summary = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Organizational Directory Connection"
        verbose_name_plural = "Organizational Directory Connections"
        db_table = "organizational_directory_connections"
        ordering = ("-created_at",)

    def issue_token(self) -> str:
        """
        Replace the stored credential with a freshly minted one.

        @description Rotation is destructive by design — the previous token
        stops authenticating the moment this returns, so Entra has to be
        updated with the new one.

        @returns: The new token in the clear; it cannot be read back later.
        """
        from django.utils import timezone

        token = generate_directory_token()
        self.token_hash = hash_directory_token(token)
        self.token_prefix = token[:DIRECTORY_TOKEN_PREFIX_LENGTH]
        self.token_issued_at = timezone.now()
        self.token_last_used_at = None
        return token

    def __str__(self):
        return f"{self.provider} directory <{self.workspace_id}>"


class OrganizationalDirectoryIdentity(BaseModel):
    """
    One SCIM ``User`` resource as the directory pushed it, plus the workspace
    member it resolves to.

    The row is the stable anchor for a person across SCIM calls: Entra
    addresses users by the id Plane hands back at creation, and group
    membership operations reference that same id. Because the identity — not
    the workspace member — is what SCIM addresses, a group can legitimately
    contain someone Plane cannot resolve yet.

    Attributes:
        workspace (Workspace): The workspace this identity was pushed into.
        external_id (str): The directory's own immutable id (SCIM
            ``externalId``; the Entra user objectId).
        user_name (str): SCIM ``userName``, normally the UPN.
        email (str): Primary email, the value matched against workspace members.
        display_name (str): Human-readable name from the directory.
        is_active (bool): SCIM ``active``. Deprovisioning sets this false.
        workspace_member (WorkspaceMember): The resolved member, or ``None``.
        state (str): ``linked`` or ``unresolved``.
        last_seen_at (datetime): Last SCIM write that touched this identity.
        raw_payload (dict): Last resource received, kept for troubleshooting.
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="directory_identities",
    )
    external_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    user_name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, blank=True, default="", db_index=True)
    display_name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    workspace_member = models.ForeignKey(
        "db.WorkspaceMember",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="directory_identities",
    )
    state = models.CharField(
        max_length=12,
        choices=DirectoryIdentityState.choices,
        default=DirectoryIdentityState.UNRESOLVED,
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user_name"],
                condition=Q(deleted_at__isnull=True),
                name="org_directory_identity_unique_workspace_user_name",
            ),
            models.UniqueConstraint(
                fields=["workspace", "external_id"],
                condition=Q(deleted_at__isnull=True) & ~Q(external_id=""),
                name="org_directory_identity_unique_workspace_external_id",
            ),
        ]
        verbose_name = "Organizational Directory Identity"
        verbose_name_plural = "Organizational Directory Identities"
        db_table = "organizational_directory_identities"
        ordering = ("user_name",)

    def __str__(self):
        return f"{self.user_name} <{self.workspace_id}> ({self.state})"


class OrganizationalDirectoryGroupMembership(BaseModel):
    """
    The directory's assertion that an identity belongs to a group.

    Kept apart from ``OrganizationalUnitMembership`` because the two answer
    different questions. This table answers "what did Entra say?" and holds
    every member of the group, resolvable or not. The organizational
    membership answers "who does Plane grant access to?" and only ever
    contains people who are active members of the workspace.

    The projector maps the first onto the second; this row therefore survives
    a person leaving and rejoining the workspace, and the unit membership
    reappears from it without Entra having to push anything again.

    Attributes:
        organizational_unit (OrganizationalUnit): The unit bound to the group.
        identity (OrganizationalDirectoryIdentity): The group member.
        workspace (Workspace): Denormalized for cheap querying.
    """

    organizational_unit = models.ForeignKey(
        "db.OrganizationalUnit",
        on_delete=models.CASCADE,
        related_name="directory_group_memberships",
    )
    identity = models.ForeignKey(
        OrganizationalDirectoryIdentity,
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="directory_group_memberships",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organizational_unit", "identity"],
                condition=Q(deleted_at__isnull=True),
                name="org_directory_group_membership_unique_unit_identity",
            )
        ]
        verbose_name = "Organizational Directory Group Membership"
        verbose_name_plural = "Organizational Directory Group Memberships"
        db_table = "organizational_directory_group_memberships"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # Same cross-workspace guard the organizational layer applies: a group
        # membership may only join rows that live in one workspace.
        if self.identity.workspace_id != self.organizational_unit.workspace_id:
            raise ValidationError("Directory identity and organizational unit belong to different workspaces")
        self.workspace_id = self.organizational_unit.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.identity_id} in {self.organizational_unit_id}"
