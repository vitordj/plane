# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Reconciler for the Orca organizational layer.

The organizational layer (units, memberships, unit-project links) never
replaces Plane's RBAC — it *materializes* native ``ProjectMember`` rows so the
rest of Plane keeps authorizing project access exactly as it does today.

Two invariants govern every write, per FORK.md and the fork's access policy:

1. **The inherited role is a floor, and manual access above it always wins.**
   Two halves, and they are not symmetric:

   *Downwards*, the layer only lowers or removes access when the current
   ``ProjectMember.role`` still equals the role it last wrote
   (``OrganizationalProjectAccessState.last_applied_role``). Drift there means
   someone changed the role by hand, so the layer withdraws its claim instead
   of overwriting it, and any manual ``baseline_role`` it recorded is restored
   rather than dropped.

   *Upwards*, the inherited role is a floor and is re-applied. A member of a
   unit that grants Member on a project is a Member of that project: an admin
   who demotes them to Guest by hand is put back at the next reconcile,
   because the unit still says they belong. That is deliberate — the way to
   take the access away is to remove the person from the unit or change the
   unit-project link, not to fight the reconciler by hand. Manual promotions
   *above* the floor are untouched, since the target is
   ``max(inherited_role, baseline_role)``.
2. **Provenance is explicit.** ``OrganizationalUnitGrant`` records every
   (membership, unit-project) pair that sources access, so removing one unit
   never removes access another unit (or a manual grant) still justifies.

Reconciliation is always called explicitly — from the API service layer, a
Celery task, or a management command. No Django signals, so the behavior stays
predictable and testable.
"""

# Python imports
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Django imports
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

# Module imports
from plane.db.models import (
    OrganizationalUnitCoordinator,
    OrganizationalProjectAccessState,
    OrganizationalUnit,
    OrganizationalUnitGrant,
    OrganizationalUnitMembership,
    OrganizationalUnitProject,
    Project,
    ProjectMember,
    WorkspaceMember,
)

# Actions reported by the planner/reconciler for a single (member, project) pair.
ACTION_NONE = "none"
ACTION_CREATE = "create"
ACTION_REACTIVATE = "reactivate"
ACTION_ELEVATE = "elevate"
ACTION_LOWER = "lower"
ACTION_RESTORE_BASELINE = "restore_baseline"
ACTION_DEACTIVATE = "deactivate"
ACTION_SKIP_MANUAL_DRIFT = "skip_manual_drift"

ROLE_ADMIN = 20
ROLE_MEMBER = 15
ROLE_GUEST = 5


@dataclass
class AccessChange:
    """
    One planned or applied change to a person's access on a project.

    @description Returned by both the read-only planner (``plan_access``) and
    the writer (``reconcile_access``) so the ``effective-access`` endpoint and
    the management command can render the same shape without ever writing.
    """

    workspace_member_id: str
    project_id: str
    current_role: Optional[int]
    desired_role: Optional[int]
    action: str
    sources: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "workspace_member_id": str(self.workspace_member_id),
            "project_id": str(self.project_id),
            "current_role": self.current_role,
            "desired_role": self.desired_role,
            "action": self.action,
            "sources": self.sources,
        }


def cap_role_to_workspace_role(role: int, workspace_role: int) -> int:
    """
    Clamp an inherited project role to what the workspace role permits.

    @description Mirrors the guards in ``ProjectMemberViewSet.create``: a
    workspace Guest can never hold a higher project role, and a workspace Admin
    is never added below Admin. Without this, the reconciler could write states
    the native member API itself rejects.

    @param role: Role the organizational layer wants to grant.
    @param workspace_role: The person's role in the workspace.
    @returns: The role that may actually be written to ``ProjectMember``.
    """
    if workspace_role == ROLE_GUEST:
        return ROLE_GUEST
    if workspace_role == ROLE_ADMIN:
        return ROLE_ADMIN
    return role


def _max_edges() -> int:
    """Fan-out threshold above which reconciliation is handed to Celery."""
    return int(getattr(settings, "ORCA_ORG_SYNC_MAX_EDGES", 100))


# What a coordinator inherits on the projects their area covers. Member, not
# whatever the area grants its members: a coordinator needs to see and move the
# work, not to administer the project.
COORDINATOR_ROLE = 15

SOURCE_MEMBERSHIP = "membership"
SOURCE_COORDINATOR = "coordinator"


@dataclass(frozen=True)
class AccessSource:
    """
    One reason somebody has inherited access to a project.

    @description Belonging to an area and coordinating one are different
    reasons with different consequences — losing one must not take away what
    the other gave — so they are two kinds of source rather than one lumped
    together.
    """

    kind: str
    source_id: object
    workspace_member_id: object
    organizational_unit_id: object
    unit_name: str
    role: int


def _active_sources(workspace_id, member_ids=None, project_ids=None):
    """
    Every (membership, unit-project) pair that currently sources inherited
    access in a workspace, optionally narrowed to some members or projects.

    @description Only active memberships of active units linked to live,
    non-archived projects contribute. The workspace member must also still be
    active in the workspace.
    """
    queryset = (
        OrganizationalUnitProject.objects.filter(
            workspace_id=workspace_id,
            organizational_unit__is_active=True,
            project__archived_at__isnull=True,
        )
        .select_related("organizational_unit", "project")
        .prefetch_related("organizational_unit__memberships")
    )
    if project_ids is not None:
        queryset = queryset.filter(project_id__in=project_ids)

    unit_projects = list(queryset)
    if not unit_projects:
        return []

    membership_filter = Q(
        organizational_unit_id__in={up.organizational_unit_id for up in unit_projects},
        is_active=True,
        workspace_member__is_active=True,
    )
    if member_ids is not None:
        membership_filter &= Q(workspace_member_id__in=member_ids)

    memberships = list(
        OrganizationalUnitMembership.objects.filter(membership_filter).select_related("workspace_member")
    )
    memberships_by_unit: dict = {}
    for membership in memberships:
        memberships_by_unit.setdefault(membership.organizational_unit_id, []).append(membership)

    coordinator_filter = Q(
        organizational_unit_id__in={up.organizational_unit_id for up in unit_projects},
        is_active=True,
        workspace_member__is_active=True,
    )
    if member_ids is not None:
        coordinator_filter &= Q(workspace_member_id__in=member_ids)

    coordinators_by_unit: dict = {}
    for coordinator in OrganizationalUnitCoordinator.objects.filter(coordinator_filter).select_related(
        "workspace_member", "organizational_unit"
    ):
        coordinators_by_unit.setdefault(coordinator.organizational_unit_id, []).append(coordinator)

    sources = []
    for unit_project in unit_projects:
        unit_name = unit_project.organizational_unit.name
        for membership in memberships_by_unit.get(unit_project.organizational_unit_id, []):
            sources.append(
                (
                    AccessSource(
                        kind=SOURCE_MEMBERSHIP,
                        source_id=membership.id,
                        workspace_member_id=membership.workspace_member_id,
                        organizational_unit_id=unit_project.organizational_unit_id,
                        unit_name=unit_name,
                        role=unit_project.default_role,
                    ),
                    unit_project,
                )
            )
        for coordinator in coordinators_by_unit.get(unit_project.organizational_unit_id, []):
            sources.append(
                (
                    AccessSource(
                        kind=SOURCE_COORDINATOR,
                        source_id=coordinator.id,
                        workspace_member_id=coordinator.workspace_member_id,
                        organizational_unit_id=unit_project.organizational_unit_id,
                        unit_name=unit_name,
                        role=COORDINATOR_ROLE,
                    ),
                    unit_project,
                )
            )
    return sources


def _group_sources(sources) -> dict:
    """Index active sources by (workspace_member_id, project_id)."""
    grouped: dict = {}
    for source, unit_project in sources:
        key = (source.workspace_member_id, unit_project.project_id)
        grouped.setdefault(key, []).append((source, unit_project))
    return grouped


def _describe_sources(pairs) -> list[dict]:
    """Human-readable provenance for the effective-access response."""
    return [
        {
            "organizational_unit_id": str(source.organizational_unit_id),
            "organizational_unit_name": source.unit_name,
            "membership_id": str(source.source_id) if source.kind == SOURCE_MEMBERSHIP else None,
            "coordinator_id": str(source.source_id) if source.kind == SOURCE_COORDINATOR else None,
            "source": source.kind,
            "role": source.role,
        }
        for source, unit_project in pairs
    ]


def _decide(
    current_member: Optional[ProjectMember],
    state: Optional[OrganizationalProjectAccessState],
    inherited_role: Optional[int],
) -> tuple[str, Optional[int]]:
    """
    Decide what should happen to one (member, project) pair.

    @description Pure decision function shared by the planner and the writer,
    so a dry-run and a real run can never disagree. It encodes the asymmetry
    described in the module docstring: the inherited role is a floor, so the
    layer raises to it freely — re-reverting a manual demotion below it — but
    lowers or withdraws only when the current role is still the one this layer
    last applied.

    @param current_member: The native ``ProjectMember`` row, if any.
    @param state: The aggregate state row, if the layer has acted before.
    @param inherited_role: Highest role inherited from active units, already
        capped to the workspace role; ``None`` when no unit sources access.
    @returns: Tuple of (action, role to write). The role is ``None`` when
        nothing is written or the member is deactivated.
    """
    baseline = state.baseline_role if state else None
    last_applied = state.last_applied_role if state else None
    is_active_member = bool(current_member and current_member.is_active)
    current_role = current_member.role if is_active_member else None

    # No unit sources access any more: withdraw what this layer added.
    if inherited_role is None:
        if last_applied is None or not is_active_member:
            return ACTION_NONE, None
        if current_role != last_applied:
            # Someone changed the role by hand — the pair is manual now.
            return ACTION_SKIP_MANUAL_DRIFT, None
        if baseline is not None:
            return ACTION_RESTORE_BASELINE, baseline
        return ACTION_DEACTIVATE, None

    # A unit sources access. The inherited role is a floor and a manual
    # promotion above it is never lost, so the target is the stronger of the
    # two. A manual demotion *below* the floor is deliberately undone: the
    # unit still says this person belongs, and the way to withdraw that is to
    # change the unit, not the ProjectMember row.
    target = max(inherited_role, baseline or 0)

    if current_member is None:
        return ACTION_CREATE, target
    if not is_active_member:
        return ACTION_REACTIVATE, target
    if target > current_role:
        return ACTION_ELEVATE, target
    if target < current_role:
        if current_role != last_applied:
            return ACTION_SKIP_MANUAL_DRIFT, None
        return ACTION_LOWER, target
    return ACTION_NONE, target


def _collect_context(workspace_id, member_ids=None, project_ids=None):
    """Load sources, existing project members and access states for a scope."""
    sources = _active_sources(workspace_id, member_ids=member_ids, project_ids=project_ids)
    grouped = _group_sources(sources)

    # Pairs already touched by this layer must be revisited even when no unit
    # sources them any more — that is exactly how access gets withdrawn.
    state_filter = Q(workspace_id=workspace_id, last_applied_role__isnull=False)
    if member_ids is not None:
        state_filter &= Q(workspace_member_id__in=member_ids)
    if project_ids is not None:
        state_filter &= Q(project_id__in=project_ids)
    states = list(OrganizationalProjectAccessState.objects.filter(state_filter))

    keys = set(grouped.keys()) | {(state.workspace_member_id, state.project_id) for state in states}
    return grouped, states, keys


def _member_user_map(workspace_member_ids) -> dict:
    """Map workspace member ids to their user ids and workspace roles."""
    return {
        workspace_member.id: workspace_member
        for workspace_member in WorkspaceMember.objects.filter(id__in=workspace_member_ids)
    }


def plan_access(workspace_id, member_ids=None, project_ids=None) -> list[AccessChange]:
    """
    Compute what reconciliation *would* do, writing nothing.

    @description Backs the strictly read-only ``effective-access`` endpoint and
    the management command's default dry-run. Uses the same decision function
    as ``reconcile_access``, so the preview matches the write.

    @param workspace_id: Workspace being inspected.
    @param member_ids: Optional workspace member ids to narrow the scope.
    @param project_ids: Optional project ids to narrow the scope.
    @returns: One ``AccessChange`` per affected (member, project) pair.
    """
    grouped, states, keys = _collect_context(workspace_id, member_ids, project_ids)
    if not keys:
        return []

    workspace_members = _member_user_map({key[0] for key in keys})
    states_by_key = {(state.workspace_member_id, state.project_id): state for state in states}
    project_members = {
        (project_member.member_id, project_member.project_id): project_member
        for project_member in ProjectMember.objects.filter(
            project_id__in={key[1] for key in keys},
            member_id__in={workspace_members[key[0]].member_id for key in keys if key[0] in workspace_members},
        )
    }

    changes = []
    for workspace_member_id, project_id in sorted(keys, key=lambda key: (str(key[0]), str(key[1]))):
        workspace_member = workspace_members.get(workspace_member_id)
        if workspace_member is None:
            continue
        pairs = grouped.get((workspace_member_id, project_id), [])
        inherited = (
            cap_role_to_workspace_role(max(source.role for source, _ in pairs), workspace_member.role)
            if pairs
            else None
        )
        project_member = project_members.get((workspace_member.member_id, project_id))
        state = states_by_key.get((workspace_member_id, project_id))
        action, role = _decide(project_member, state, inherited)
        changes.append(
            AccessChange(
                workspace_member_id=workspace_member_id,
                project_id=project_id,
                current_role=(project_member.role if project_member and project_member.is_active else None),
                desired_role=role,
                action=action,
                sources=_describe_sources(pairs),
            )
        )
    return changes


def _sync_grants(workspace_id, grouped, keys) -> None:
    """
    Bring the provenance ledger in line with the currently active sources.

    @description Creates a grant per live (membership, unit-project) pair and
    revokes grants whose source disappeared, keeping revoked rows for audit.
    """
    existing = {
        ((grant.coordinator_id or grant.membership_id), grant.unit_project_id): grant
        for grant in OrganizationalUnitGrant.objects.filter(
            workspace_id=workspace_id,
            workspace_member_id__in={key[0] for key in keys},
            project_id__in={key[1] for key in keys},
        )
    }
    live_keys = set()
    to_create = []
    to_update = []

    for (workspace_member_id, project_id), pairs in grouped.items():
        for source, unit_project in pairs:
            live_keys.add((source.source_id, unit_project.id))
            grant = existing.get((source.source_id, unit_project.id))
            if grant is None:
                to_create.append(
                    OrganizationalUnitGrant(
                        organizational_unit_id=unit_project.organizational_unit_id,
                        membership_id=source.source_id if source.kind == SOURCE_MEMBERSHIP else None,
                        coordinator_id=source.source_id if source.kind == SOURCE_COORDINATOR else None,
                        grant_source=source.kind,
                        unit_project_id=unit_project.id,
                        workspace_member_id=workspace_member_id,
                        project_id=project_id,
                        workspace_id=workspace_id,
                        granted_role=source.role,
                        is_active=True,
                    )
                )
            elif not grant.is_active or grant.granted_role != source.role:
                grant.is_active = True
                grant.granted_role = source.role
                grant.revoked_at = None
                to_update.append(grant)

    for key, grant in existing.items():
        if key not in live_keys and grant.is_active:
            grant.is_active = False
            grant.revoked_at = timezone.now()
            to_update.append(grant)

    if to_create:
        OrganizationalUnitGrant.objects.bulk_create(to_create, batch_size=100, ignore_conflicts=True)
    if to_update:
        OrganizationalUnitGrant.objects.bulk_update(
            to_update, ["is_active", "granted_role", "revoked_at"], batch_size=100
        )


def _apply_change(workspace_member, project_id, project_member, state, action, role, workspace_id):
    """Write one decided change to the native ``ProjectMember`` and the state row."""
    now = timezone.now()

    if state is None:
        state = OrganizationalProjectAccessState(
            workspace_member_id=workspace_member.id,
            project_id=project_id,
            workspace_id=workspace_id,
        )

    if action == ACTION_CREATE:
        project_member = ProjectMember.objects.create(
            project_id=project_id,
            member_id=workspace_member.member_id,
            workspace_id=workspace_id,
            role=role,
            is_active=True,
        )
        state.baseline_role = None
        state.created_by_org_layer = True
        state.last_applied_role = role
        state.project_member = project_member

    elif action == ACTION_REACTIVATE:
        # The person held no active access before, so nothing manual is lost.
        project_member.is_active = True
        project_member.role = role
        project_member.save()
        state.baseline_role = None
        state.created_by_org_layer = True
        state.last_applied_role = role
        state.project_member = project_member

    elif action in (ACTION_ELEVATE, ACTION_LOWER):
        if state.last_applied_role is None and action == ACTION_ELEVATE:
            # First write over pre-existing manual access: remember it so the
            # role can be restored when the unit source goes away.
            state.baseline_role = project_member.role
            state.created_by_org_layer = False
        project_member.role = role
        project_member.save()
        state.last_applied_role = role
        state.project_member = project_member

    elif action == ACTION_RESTORE_BASELINE:
        project_member.role = role
        project_member.save()
        state.last_applied_role = None
        state.project_member = project_member

    elif action == ACTION_DEACTIVATE:
        project_member.is_active = False
        project_member.save()
        state.last_applied_role = None
        state.created_by_org_layer = False
        state.project_member = project_member

    elif action == ACTION_SKIP_MANUAL_DRIFT:
        # The current role is no longer ours; relinquish the claim so future
        # runs treat this access as manual.
        state.last_applied_role = None
        state.created_by_org_layer = False

    elif action == ACTION_NONE:
        if role is not None and state.last_applied_role is None and project_member is not None:
            # Inherited role already matches a manual role: record it as the
            # baseline without touching the member.
            state.baseline_role = project_member.role
            state.created_by_org_layer = False
            state.last_applied_role = role
            state.project_member = project_member

    state.last_reconciled_at = now
    state.save()
    return state


def reconcile_access(workspace_id, member_ids=None, project_ids=None) -> list[AccessChange]:
    """
    Reconcile inherited access for a scope and write the result.

    @description Idempotent and safe to retry: running it twice over the same
    scope produces no second set of changes. Rows are locked with
    ``select_for_update`` so two concurrent mutations cannot interleave into
    conflicting decisions.

    @param workspace_id: Workspace being reconciled.
    @param member_ids: Optional workspace member ids to narrow the scope.
    @param project_ids: Optional project ids to narrow the scope.
    @returns: The changes that were applied (``ACTION_NONE`` entries included).
    """
    with transaction.atomic():
        grouped, states, keys = _collect_context(workspace_id, member_ids, project_ids)
        if not keys:
            return []

        workspace_members = _member_user_map({key[0] for key in keys})
        user_ids = {workspace_member.member_id for workspace_member in workspace_members.values()}
        project_id_set = {key[1] for key in keys}

        # Lock the native rows this run may write before reading their state.
        locked_members = {
            (project_member.member_id, project_member.project_id): project_member
            for project_member in ProjectMember.objects.select_for_update()
            .filter(project_id__in=project_id_set, member_id__in=user_ids)
            .order_by("id")
        }
        states_by_key = {
            (state.workspace_member_id, state.project_id): state
            for state in OrganizationalProjectAccessState.objects.select_for_update()
            .filter(workspace_id=workspace_id, project_id__in=project_id_set)
            .order_by("id")
        }

        _sync_grants(workspace_id, grouped, keys)

        changes = []
        for workspace_member_id, project_id in sorted(keys, key=lambda key: (str(key[0]), str(key[1]))):
            workspace_member = workspace_members.get(workspace_member_id)
            if workspace_member is None:
                continue
            pairs = grouped.get((workspace_member_id, project_id), [])
            inherited = (
                cap_role_to_workspace_role(max(source.role for source, _ in pairs), workspace_member.role)
                if pairs
                else None
            )
            project_member = locked_members.get((workspace_member.member_id, project_id))
            state = states_by_key.get((workspace_member_id, project_id))
            action, role = _decide(project_member, state, inherited)
            current_role = project_member.role if project_member and project_member.is_active else None

            if action != ACTION_NONE or (role is not None and (state is None or state.last_applied_role is None)):
                _apply_change(
                    workspace_member,
                    project_id,
                    project_member,
                    state,
                    action,
                    role,
                    workspace_id,
                )

            changes.append(
                AccessChange(
                    workspace_member_id=workspace_member_id,
                    project_id=project_id,
                    current_role=current_role,
                    desired_role=role,
                    action=action,
                    sources=_describe_sources(pairs),
                )
            )
        return changes


def _affected_edges(member_ids: Iterable, project_ids: Iterable) -> int:
    """Rough fan-out estimate: people impacted × projects impacted."""
    members = len(list(member_ids)) or 1
    projects = len(list(project_ids)) or 1
    return members * projects


def dispatch_reconciliation(workspace_id, member_ids=None, project_ids=None, force_sync=False):
    """
    Run reconciliation inline for small fan-outs, hand large ones to Celery.

    @description Small mutations (adding one person, linking one project) stay
    synchronous so the API response reflects the final state. Anything wider
    than ``ORCA_ORG_SYNC_MAX_EDGES`` edges is queued after commit, so the
    request does not block on a large rewrite.

    @param force_sync: Run inline regardless of size (used by tests and the
        management command, which are already running outside a request).
    @returns: The applied changes when run inline, otherwise ``None``.
    """
    member_list = list(member_ids) if member_ids is not None else None
    project_list = list(project_ids) if project_ids is not None else None

    if force_sync or _affected_edges(member_list or [], project_list or []) <= _max_edges():
        return reconcile_access(workspace_id, member_list, project_list)

    # Imported lazily: the task module imports this module for the actual work.
    from plane.bgtasks.organizational_unit_task import reconcile_organizational_access

    transaction.on_commit(
        lambda: reconcile_organizational_access.delay(
            str(workspace_id),
            [str(member_id) for member_id in member_list] if member_list else None,
            [str(project_id) for project_id in project_list] if project_list else None,
        )
    )
    return None


def reconcile_membership(membership: OrganizationalUnitMembership, force_sync=False):
    """Reconcile every project reachable from one unit membership."""
    project_ids = list(
        OrganizationalUnitProject.objects.filter(organizational_unit_id=membership.organizational_unit_id).values_list(
            "project_id", flat=True
        )
    )
    return dispatch_reconciliation(
        membership.workspace_id,
        member_ids=[membership.workspace_member_id],
        project_ids=project_ids or None,
        force_sync=force_sync,
    )


def reconcile_unit_project(unit_project: OrganizationalUnitProject, force_sync=False):
    """Reconcile every member of the unit against one linked project."""
    member_ids = list(
        OrganizationalUnitMembership.objects.filter(
            organizational_unit_id=unit_project.organizational_unit_id
        ).values_list("workspace_member_id", flat=True)
    )
    return dispatch_reconciliation(
        unit_project.workspace_id,
        member_ids=member_ids or None,
        project_ids=[unit_project.project_id],
        force_sync=force_sync,
    )


def reconcile_unit(unit: OrganizationalUnit, force_sync=False):
    """Reconcile the full cross product of one unit's members and projects."""
    member_ids = list(
        OrganizationalUnitMembership.objects.filter(organizational_unit_id=unit.id).values_list(
            "workspace_member_id", flat=True
        )
    )
    project_ids = list(
        OrganizationalUnitProject.objects.filter(organizational_unit_id=unit.id).values_list("project_id", flat=True)
    )
    return dispatch_reconciliation(
        unit.workspace_id,
        member_ids=member_ids or None,
        project_ids=project_ids or None,
        force_sync=force_sync,
    )


def reconcile_workspace(workspace_id, apply=False) -> list[AccessChange]:
    """
    Reconcile (or preview) an entire workspace.

    @description Used by the ``reconcile_organizational_access`` management
    command, which previews by default and only writes with ``--apply``.
    """
    if apply:
        return reconcile_access(workspace_id)
    return plan_access(workspace_id)


def project_ids_for_unit(unit_id) -> list:
    """Project ids currently linked to a unit."""
    return list(
        OrganizationalUnitProject.objects.filter(organizational_unit_id=unit_id).values_list("project_id", flat=True)
    )


def projects_in_workspace(workspace_id) -> list:
    """Live project ids of a workspace, used by the workspace-wide command."""
    return list(
        Project.objects.filter(workspace_id=workspace_id, archived_at__isnull=True).values_list("id", flat=True)
    )
