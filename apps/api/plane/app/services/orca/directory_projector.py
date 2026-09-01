# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Projector from the directory mirror onto the Orca organizational layer.

The SCIM endpoints only ever write the mirror
(``plane.db.models.organizational_directory``): identities, group memberships,
and the binding between a directory group and a unit. Nothing there grants
access. This module is the single place that turns that mirror into real
``OrganizationalUnitMembership`` rows, which the reconciler then materializes
into native ``ProjectMember`` rows.

Three rules govern every write here:

1. **The layer only touches what it created.** A membership is projected with
   ``sync_source = scim`` and only such rows are ever deactivated. A membership
   an admin added by hand keeps ``sync_source = manual`` and survives the
   person being removed from the directory group — the same shape as the
   reconciler's "manual access always wins" guard one layer down.
2. **A unit never invites anyone.** An identity that does not resolve to an
   active workspace member produces no membership at all. It stays in the
   mirror, is reported as unresolved, and becomes a membership by itself once
   that person joins the workspace and the projector runs again.
3. **Reconciliation is explicit.** Like the rest of the layer there are no
   Django signals: every projection ends by calling into the reconciler, so
   the caller can see (and test) exactly what a directory write changed.
"""

# Python imports
from dataclasses import dataclass, field

# Django imports
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.db.models import (
    DirectoryIdentityState,
    DirectorySyncSource,
    OrganizationalDirectoryConnection,
    OrganizationalDirectoryGroupMembership,
    OrganizationalDirectoryIdentity,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    WorkspaceMember,
)

from .org_unit_reconciler import dispatch_reconciliation


@dataclass
class ProjectionResult:
    """
    What one projection pass changed.

    @description Returned to the SCIM views so a provisioning response can be
    logged with its real effect, and to the settings endpoint so an admin can
    see what a manual resync did.
    """

    memberships_created: int = 0
    memberships_reactivated: int = 0
    memberships_deactivated: int = 0
    identities_linked: int = 0
    identities_unresolved: int = 0
    unresolved_user_names: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "memberships_created": self.memberships_created,
            "memberships_reactivated": self.memberships_reactivated,
            "memberships_deactivated": self.memberships_deactivated,
            "identities_linked": self.identities_linked,
            "identities_unresolved": self.identities_unresolved,
            "unresolved_user_names": self.unresolved_user_names,
        }

    def merge(self, other: "ProjectionResult") -> "ProjectionResult":
        """Fold another pass into this one, for callers projecting several units."""
        self.memberships_created += other.memberships_created
        self.memberships_reactivated += other.memberships_reactivated
        self.memberships_deactivated += other.memberships_deactivated
        self.identities_linked += other.identities_linked
        self.identities_unresolved += other.identities_unresolved
        for user_name in other.unresolved_user_names:
            if user_name not in self.unresolved_user_names:
                self.unresolved_user_names.append(user_name)
        return self


def match_workspace_member(workspace_id, email: str, user_name: str = ""):
    """
    Find the active workspace member a directory identity refers to.

    @description Email is the join key: it is the one attribute Entra and Plane
    both hold for the same human, and it is what every SCIM deployment maps
    first. ``userName`` is tried as a fallback because Entra sends the UPN
    there, which for most tenants is also the mailbox address.

    Matching is case-insensitive and restricted to *active* members: someone
    who was removed from the workspace must not silently regain access because
    the directory still lists them.

    @param workspace_id: Workspace the identity was pushed into.
    @param email: Primary email from the SCIM resource.
    @param user_name: SCIM ``userName``, used when the email does not match.
    @returns: The ``WorkspaceMember``, or ``None`` when nobody matches.
    """
    for candidate in (email, user_name):
        if not candidate:
            continue
        member = (
            WorkspaceMember.objects.filter(
                workspace_id=workspace_id,
                is_active=True,
                member__email__iexact=candidate,
            )
            .select_related("member")
            .first()
        )
        if member is not None:
            return member
    return None


def resolve_identity(identity: OrganizationalDirectoryIdentity) -> bool:
    """
    Point a directory identity at its workspace member, if one exists.

    @description Runs on every SCIM write and on demand, so an identity that
    arrived before the person had a Plane account resolves itself later without
    the directory pushing anything again.

    @param identity: The mirrored SCIM user.
    @returns: ``True`` when the identity is linked after this call.
    """
    member = match_workspace_member(identity.workspace_id, identity.email, identity.user_name)
    new_state = DirectoryIdentityState.LINKED if member else DirectoryIdentityState.UNRESOLVED
    if identity.workspace_member_id != (member.id if member else None) or identity.state != new_state:
        identity.workspace_member = member
        identity.state = new_state
        identity.save(update_fields=["workspace_member", "state", "updated_at"])
    return member is not None


def _desired_member_ids(unit: OrganizationalUnit) -> set:
    """
    Workspace member ids the directory currently asserts for a unit.

    @description Only identities that are both active in the directory and
    linked to an active workspace member can source a membership — the other
    two states are exactly what the unresolved report is for.
    """
    return set(
        OrganizationalDirectoryGroupMembership.objects.filter(
            organizational_unit_id=unit.id,
            identity__is_active=True,
            identity__state=DirectoryIdentityState.LINKED,
            identity__workspace_member__isnull=False,
            identity__workspace_member__is_active=True,
        ).values_list("identity__workspace_member_id", flat=True)
    )


def _unresolved_for_unit(unit: OrganizationalUnit) -> list:
    """User names the directory put in a group that Plane could not resolve."""
    return list(
        OrganizationalDirectoryGroupMembership.objects.filter(organizational_unit_id=unit.id)
        .exclude(
            identity__state=DirectoryIdentityState.LINKED,
            identity__is_active=True,
            identity__workspace_member__is_active=True,
        )
        .values_list("identity__user_name", flat=True)
    )


def directory_withdraws_membership(workspace_id) -> bool:
    """
    Whether the directory is allowed to withdraw the memberships it created.

    @description Some rollouts want provisioning to be purely additive while
    the directory data is still being cleaned up — a half-populated group
    should not strip people of access on its first sync. Turning
    ``deprovision_removes_membership`` off makes the projector add only; the
    memberships it already created stay until an admin removes them or the
    setting is turned back on.

    @param workspace_id: The workspace being projected.
    @returns: ``True`` when withdrawal is allowed (the default).
    """
    connection = OrganizationalDirectoryConnection.objects.filter(workspace_id=workspace_id).first()
    return True if connection is None else connection.deprovision_removes_membership


@transaction.atomic
def project_unit(unit: OrganizationalUnit, reconcile: bool = True) -> ProjectionResult:
    """
    Make the unit's memberships match what the directory asserts for its group.

    @description Additive for people the directory added, subtractive only for
    memberships this layer itself created. A membership held manually is left
    exactly as it is even when the person is absent from the directory group,
    and a person the directory adds who already holds a manual membership keeps
    that manual provenance — so a later directory removal cannot revoke it.

    @param unit: The unit bound to a directory group.
    @param reconcile: Whether to hand the affected members to the reconciler.
        The SCIM views pass ``True``; bulk callers projecting many units pass
        ``False`` and reconcile once at the end.
    @returns: Counters describing what changed.
    """
    result = ProjectionResult()
    desired = _desired_member_ids(unit)
    touched_member_ids = set()

    existing = {
        membership.workspace_member_id: membership
        for membership in OrganizationalUnitMembership.objects.select_for_update().filter(
            organizational_unit_id=unit.id
        )
    }

    # Additive pass: everyone the directory asserts should hold a membership.
    for workspace_member_id in desired:
        membership = existing.get(workspace_member_id)
        if membership is None:
            OrganizationalUnitMembership.objects.create(
                organizational_unit=unit,
                workspace_member_id=workspace_member_id,
                workspace_id=unit.workspace_id,
                sync_source=DirectorySyncSource.SCIM,
            )
            result.memberships_created += 1
            touched_member_ids.add(workspace_member_id)
        elif not membership.is_active:
            # Reactivating keeps whatever provenance the row already had: a
            # manual membership that was switched off stays manual.
            membership.is_active = True
            membership.save(update_fields=["is_active", "updated_at"])
            result.memberships_reactivated += 1
            touched_member_ids.add(workspace_member_id)

    # Subtractive pass: withdraw only what this layer put there, and only
    # when the workspace allows the directory to take access away at all.
    if directory_withdraws_membership(unit.workspace_id):
        for workspace_member_id, membership in existing.items():
            if workspace_member_id in desired:
                continue
            if membership.sync_source != DirectorySyncSource.SCIM or not membership.is_active:
                continue
            membership.is_active = False
            membership.save(update_fields=["is_active", "updated_at"])
            result.memberships_deactivated += 1
            touched_member_ids.add(workspace_member_id)

    unresolved = _unresolved_for_unit(unit)
    result.identities_unresolved = len(unresolved)
    result.unresolved_user_names = list(dict.fromkeys(unresolved))
    result.identities_linked = len(desired)

    unit.directory_synced_at = timezone.now()
    unit.save(update_fields=["directory_synced_at", "updated_at"])

    if reconcile and touched_member_ids:
        # Scope the reconciliation to the unit's own projects, the same way
        # ``reconcile_membership`` does: it keeps the fan-out estimate honest,
        # so a large group change is queued instead of running in-request.
        project_ids = list(
            OrganizationalUnitProject.objects.filter(organizational_unit_id=unit.id).values_list(
                "project_id", flat=True
            )
        )
        dispatch_reconciliation(
            unit.workspace_id,
            member_ids=list(touched_member_ids),
            project_ids=project_ids or None,
        )
    return result


def project_identity(identity: OrganizationalDirectoryIdentity, reconcile: bool = True) -> ProjectionResult:
    """
    Re-project every unit the identity belongs to.

    @description Called when a single person changes — deactivated in the
    directory, renamed, or newly resolvable — which can add or withdraw their
    membership in several units at once.

    @param identity: The mirrored SCIM user that changed.
    @param reconcile: Whether to reconcile project access afterwards.
    @returns: Counters describing what changed across those units.
    """
    result = ProjectionResult()
    unit_ids = OrganizationalDirectoryGroupMembership.objects.filter(identity_id=identity.id).values_list(
        "organizational_unit_id", flat=True
    )
    for unit in OrganizationalUnit.objects.filter(id__in=list(unit_ids)):
        result.merge(project_unit(unit, reconcile=reconcile))
    return result


def project_workspace(workspace_id, reconcile: bool = True) -> ProjectionResult:
    """
    Re-resolve every identity in a workspace and re-project every bound unit.

    @description The repair path. It is what the settings screen's "resync"
    button and the periodic task call, and it is how identities that arrived
    for people who were not yet workspace members turn into real memberships
    once they join. Idempotent: running it twice changes nothing the second
    time.

    @param workspace_id: The workspace to repair.
    @param reconcile: Whether to reconcile project access afterwards.
    @returns: Counters describing what changed.
    """
    result = ProjectionResult()
    for identity in OrganizationalDirectoryIdentity.objects.filter(workspace_id=workspace_id).select_related(
        "workspace_member"
    ):
        resolve_identity(identity)

    for unit in OrganizationalUnit.objects.filter(workspace_id=workspace_id).exclude(external_id=""):
        result.merge(project_unit(unit, reconcile=reconcile))
    return result


def unresolved_identities(workspace_id):
    """
    Directory identities that could not be turned into workspace access.

    @description Powers the report an admin reads after a provisioning cycle:
    these are the people Entra pushed who are not active members of the
    workspace, so the layer deliberately did nothing for them.

    @param workspace_id: The workspace to report on.
    @returns: Queryset of unresolved identities, newest directory contact first.
    """
    return (
        OrganizationalDirectoryIdentity.objects.filter(workspace_id=workspace_id)
        .exclude(state=DirectoryIdentityState.LINKED, workspace_member__is_active=True)
        .order_by("-last_seen_at", "user_name")
    )
