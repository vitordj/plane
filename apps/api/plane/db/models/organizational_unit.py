# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

# Module imports
from .base import BaseModel
from .project import ROLE_CHOICES


class OrganizationalUnitMemberRole(models.TextChoices):
    """Role a person holds inside an organizational unit (not a project role)."""

    LEAD = "lead", "Lead"
    MEMBER = "member", "Member"


class RoutingState(models.TextChoices):
    """
    Where a work item stands between "an area owns this" and "a person is on it".

    @description Responsibility and assignment used to be the same moment: the
    link existed and either somebody was assigned or nobody was, with no way to
    tell "waiting to be picked up" from "tried and found nobody". The queue is
    that missing state, and it is what a coordinator's board reads.
    """

    QUEUED = "queued", "Queued"
    ASSIGNED = "assigned", "Assigned"
    ALLOCATION_FAILED = "allocation_failed", "Allocation failed"
    SUSPENDED = "suspended", "Suspended"


class QueueReason(models.TextChoices):
    """
    Why an item is sitting in the queue.

    @description Without it every queued item looks alike, and the coordinator
    cannot tell the ones nobody has looked at yet from the ones the allocator
    already failed on — which need different actions.
    """

    NEW_ITEM = "new_item", "New item"
    AWAITING_COORDINATOR = "awaiting_coordinator", "Awaiting coordinator"
    AWAITING_CLAIM = "awaiting_claim", "Awaiting claim"
    NO_ELIGIBLE_MEMBER = "no_eligible_member", "No eligible member"
    EXECUTOR_UNAVAILABLE = "executor_unavailable", "Executor unavailable"
    MANUALLY_RETURNED = "manually_returned", "Manually returned"


class GrantSource(models.TextChoices):
    """
    Which kind of tie sources one inherited project access.

    @description A grant used to have exactly one possible origin — a unit
    membership — so the FK to it was the whole answer. Coordination is a second
    origin that is not a membership (a coordinator need not belong to the area,
    RFC §5.2), and the two have to stay tellable apart: removing somebody from
    the coordination must take back only what coordinating gave them, leaving
    whatever their membership still justifies.
    """

    MEMBERSHIP = "membership", "Membership"
    COORDINATOR = "coordinator", "Coordinator"


class AvailabilityReason(models.TextChoices):
    """Why a person is not receiving new work (RFC §5.2)."""

    VACATION = "vacation", "Vacation"
    LEAVE = "leave", "Leave"
    OTHER = "other", "Other"


class AvailabilitySource(models.TextChoices):
    """
    Where an unavailability window came from.

    @description v1 only writes ``manual``. ``hr`` and ``directory`` are
    reserved so a later sync (open question A3) does not need a migration just
    to name the origin.
    """

    MANUAL = "manual", "Manual"
    HR = "hr", "HR"
    DIRECTORY = "directory", "Directory"


class DirectorySyncSource(models.TextChoices):
    """
    Where a row in the organizational layer came from.

    @description The layer's governing invariant is that manual decisions win
    over automated ones. Recording the origin of every unit and membership is
    what lets an external directory add and remove its own rows without ever
    touching a unit or a membership a human created by hand.

    Kept provider-neutral on purpose: ``SCIM`` covers any IdP that provisions
    through the SCIM endpoints (Microsoft Entra ID today), and a future
    pull-based sync would add its own value here rather than a second column.
    """

    MANUAL = "manual", "Manual"
    SCIM = "scim", "SCIM"


class DirectoryIdentityState(models.TextChoices):
    """
    Whether a directory identity could be matched to a workspace member.

    @description A unit never invites anyone (see
    ``OrganizationalUnitMembership``), so an identity the directory pushes for
    somebody who is not yet an active workspace member is not an error: it is
    parked as ``UNRESOLVED`` and reported, and it links itself the moment that
    person becomes a member of the workspace.
    """

    LINKED = "linked", "Linked"
    UNRESOLVED = "unresolved", "Unresolved"


class OrganizationalUnit(BaseModel):
    """
    Relational sidecar table representing an organizational unit (an "area",
    squad, committee, or similar grouping) inside a workspace.

    Designed in compliance with FORK.md: the organizational layer never
    modifies core Plane tables. Units grant project access exclusively by
    materializing native ``ProjectMember`` rows through the reconciler
    (see ``plane.app.services.orca.org_unit_reconciler``).

    Attributes:
        workspace (Workspace): Workspace the unit belongs to.
        name (str): Display name of the unit.
        slug (str): URL-safe identifier, unique per workspace.
        description (str): Optional free-form description.
        logo_props (dict): UI logo/icon properties, mirroring core models.
        is_active (bool): Inactive units are ignored by the reconciler.
        sync_source (str): ``manual`` or ``scim`` — who created the unit.
        external_id (str): Identifier of the directory group this unit mirrors
            (the SCIM ``externalId``, which for Entra is the group objectId).
            Empty for units that are not bound to a directory group.
        directory_synced_at (datetime): Last time the directory wrote to it.
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="organizational_units",
    )
    name = models.CharField(max_length=255, verbose_name="Organizational Unit Name")
    slug = models.SlugField(max_length=100, verbose_name="Organizational Unit Slug")
    description = models.TextField(blank=True, default="")
    logo_props = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    # Directory binding. A unit may mirror one directory group; the binding is
    # what lets SCIM address the unit by the IdP's own identifier instead of
    # ours. An admin can also pre-create a unit and bind it by setting this.
    sync_source = models.CharField(
        max_length=10,
        choices=DirectorySyncSource.choices,
        default=DirectorySyncSource.MANUAL,
    )
    external_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    directory_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["workspace", "slug", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "slug"],
                condition=Q(deleted_at__isnull=True),
                name="org_unit_unique_workspace_slug_when_deleted_at_null",
            ),
            # Two units may not mirror the same directory group: the SCIM
            # Group endpoint resolves a group to exactly one unit. Empty
            # external ids are excluded so unbound units stay unconstrained.
            models.UniqueConstraint(
                fields=["workspace", "external_id"],
                condition=Q(deleted_at__isnull=True) & ~Q(external_id=""),
                name="org_unit_unique_workspace_external_id_when_bound",
            ),
        ]
        verbose_name = "Organizational Unit"
        verbose_name_plural = "Organizational Units"
        db_table = "organizational_units"
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} <{self.workspace_id}>"


class OrganizationalUnitMembership(BaseModel):
    """
    Relation between a workspace member and an organizational unit.

    The FK points at ``WorkspaceMember`` (not ``User``) on purpose: unit
    membership is only meaningful for people who already belong to the
    workspace, and the v1 scope explicitly excludes inviting new users
    through a unit.

    Attributes:
        organizational_unit (OrganizationalUnit): The unit.
        workspace_member (WorkspaceMember): The workspace member.
        workspace (Workspace): Denormalized from the unit for cheap querying.
        role (str): ``lead`` or ``member``. A unit has at most one active lead.
        is_active (bool): Inactive memberships stop granting project access.
        sync_source (str): ``manual`` or ``scim``. The directory only ever
            deactivates memberships it created itself — a membership an admin
            added by hand survives the person leaving the directory group,
            mirroring the "manual access always wins" rule the reconciler
            applies one layer down at the ``ProjectMember`` level.
    """

    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    workspace_member = models.ForeignKey(
        "db.WorkspaceMember",
        on_delete=models.CASCADE,
        related_name="organizational_unit_memberships",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="organizational_unit_memberships",
    )
    role = models.CharField(
        max_length=10,
        choices=OrganizationalUnitMemberRole.choices,
        default=OrganizationalUnitMemberRole.MEMBER,
    )
    is_active = models.BooleanField(default=True)
    sync_source = models.CharField(
        max_length=10,
        choices=DirectorySyncSource.choices,
        default=DirectorySyncSource.MANUAL,
    )

    class Meta:
        unique_together = ["organizational_unit", "workspace_member", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organizational_unit", "workspace_member"],
                condition=Q(deleted_at__isnull=True),
                name="org_unit_membership_unique_unit_member_when_deleted_at_null",
            ),
            models.UniqueConstraint(
                fields=["organizational_unit"],
                condition=Q(role="lead", is_active=True, deleted_at__isnull=True),
                name="org_unit_membership_single_active_lead_per_unit",
            ),
        ]
        verbose_name = "Organizational Unit Membership"
        verbose_name_plural = "Organizational Unit Memberships"
        db_table = "organizational_unit_memberships"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # A bare FK cannot stop a membership from pointing at a WorkspaceMember
        # of a different workspace, so the cross-workspace guard lives here.
        if self.workspace_member.workspace_id != self.organizational_unit.workspace_id:
            raise ValidationError("Workspace member and organizational unit belong to different workspaces")
        self.workspace_id = self.organizational_unit.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.workspace_member_id} in {self.organizational_unit_id} ({self.role})"


class OrganizationalUnitProject(BaseModel):
    """
    Link between an organizational unit and a project, carrying the project
    role every unit member inherits on that project.

    The unit lead inherits the same ``default_role`` as everyone else —
    project Admin is only ever granted explicitly through the native
    ``ProjectMember`` flow, never implied by unit leadership.

    Attributes:
        organizational_unit (OrganizationalUnit): The unit.
        project (Project): The linked project.
        workspace (Workspace): Denormalized from the unit for cheap querying.
        default_role (int): Inherited project role (native 20/15/5 choices).
    """

    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.CASCADE,
        related_name="unit_projects",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="organizational_unit_links",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="organizational_unit_projects",
    )
    default_role = models.PositiveSmallIntegerField(choices=ROLE_CHOICES, default=15)

    class Meta:
        unique_together = ["organizational_unit", "project", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organizational_unit", "project"],
                condition=Q(deleted_at__isnull=True),
                name="org_unit_project_unique_unit_project_when_deleted_at_null",
            )
        ]
        verbose_name = "Organizational Unit Project"
        verbose_name_plural = "Organizational Unit Projects"
        db_table = "organizational_unit_projects"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # Same cross-workspace guard as memberships: a unit may only be linked
        # to projects of its own workspace.
        if self.project.workspace_id != self.organizational_unit.workspace_id:
            raise ValidationError("Project and organizational unit belong to different workspaces")
        self.workspace_id = self.organizational_unit.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.organizational_unit_id} -> {self.project_id} ({self.default_role})"


class OrganizationalUnitCoordinator(BaseModel):
    """
    Who answers for an area's work — as opposed to who does it.

    Deliberately not a third value of ``OrganizationalUnitMemberRole``. A
    coordinator is not required to belong to the area (RFC §5.2): the person who
    hands work out may sit outside the team doing it, and modelling that as a
    membership would grant them the area's inherited access as a side effect of
    the role rather than as a decision. Kept as its own row, coordination can be
    given and taken back on its own, and the grant ledger can say which of the
    two ties is paying for a given project access.

    Attributes:
        organizational_unit (OrganizationalUnit): The area being coordinated.
        workspace_member (WorkspaceMember): The coordinator.
        workspace (Workspace): Denormalized from the unit for cheap querying.
        is_active (bool): Inactive rows stop sourcing access, like memberships.
    """

    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.CASCADE,
        related_name="coordinators",
    )
    workspace_member = models.ForeignKey(
        "db.WorkspaceMember",
        on_delete=models.CASCADE,
        related_name="coordinated_units",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="organizational_unit_coordinators",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ["organizational_unit", "workspace_member", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organizational_unit", "workspace_member"],
                condition=Q(deleted_at__isnull=True),
                name="org_unit_coordinator_unique_unit_member_when_deleted_at_null",
            )
        ]
        verbose_name = "Organizational Unit Coordinator"
        verbose_name_plural = "Organizational Unit Coordinators"
        db_table = "organizational_unit_coordinators"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # Same cross-workspace guard the memberships carry: a bare FK cannot
        # stop a coordinator row from pointing at a WorkspaceMember of another
        # tenant, and this row grants project access.
        if self.workspace_member.workspace_id != self.organizational_unit.workspace_id:
            raise ValidationError("Workspace member and organizational unit belong to different workspaces")
        self.workspace_id = self.organizational_unit.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.workspace_member_id} coordinates {self.organizational_unit_id}"


class OrganizationalUnitGrant(BaseModel):
    """
    Provenance ledger: one row per (source, unit-project) pair that
    sources inherited project access.

    Grants record *why* a person has inherited access to a project. They never
    store the final applied role for the person — that aggregate lives in
    ``OrganizationalProjectAccessState`` — because one person can hold several
    grants for the same project through different units.

    Attributes:
        organizational_unit (OrganizationalUnit): Denormalized source unit.
        membership (OrganizationalUnitMembership): Source membership, when the
            grant comes from belonging to the area. ``None`` on a coordinator
            grant — a coordinator need not be a member, so there is no
            membership row for such a grant to point at.
        coordinator (OrganizationalUnitCoordinator): Source coordination, when
            the grant comes from answering for the area instead.
        grant_source (str): Which of the two FKs above is the source. Redundant
            with them by construction, and kept anyway: it is what lets a query
            filter or group by origin without a two-column ``IS NULL`` dance,
            and the CHECKs below make the redundancy impossible to break.
        unit_project (OrganizationalUnitProject): Source unit-project link.
        workspace_member (WorkspaceMember): The person receiving access.
        project (Project): The target project.
        workspace (Workspace): Denormalized for cheap querying.
        granted_role (int): Role this source contributes (20/15/5).
        is_active (bool): Revoked grants stay for audit with ``revoked_at`` set.
        revoked_at (datetime): When the grant stopped sourcing access.
    """

    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.CASCADE,
        related_name="grants",
    )
    membership = models.ForeignKey(
        OrganizationalUnitMembership,
        on_delete=models.CASCADE,
        related_name="grants",
        null=True,
        blank=True,
    )
    coordinator = models.ForeignKey(
        OrganizationalUnitCoordinator,
        on_delete=models.CASCADE,
        related_name="grants",
        null=True,
        blank=True,
    )
    grant_source = models.CharField(
        max_length=16,
        choices=GrantSource.choices,
        default=GrantSource.MEMBERSHIP,
    )
    unit_project = models.ForeignKey(
        OrganizationalUnitProject,
        on_delete=models.CASCADE,
        related_name="grants",
    )
    workspace_member = models.ForeignKey(
        "db.WorkspaceMember",
        on_delete=models.CASCADE,
        related_name="organizational_unit_grants",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="organizational_unit_grants",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="organizational_unit_grants",
    )
    granted_role = models.PositiveSmallIntegerField(choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["membership", "unit_project", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "unit_project"],
                condition=Q(deleted_at__isnull=True),
                name="org_unit_grant_unique_membership_unit_project_when_deleted_at_null",
            ),
            # The coordinator half of the same rule. A separate constraint
            # rather than a wider one over both columns, because a partial
            # unique index over two nullable columns treats every NULL as
            # distinct and would police neither.
            models.UniqueConstraint(
                fields=["coordinator", "unit_project"],
                condition=Q(deleted_at__isnull=True),
                name="org_unit_grant_unique_coordinator_unit_project_when_deleted_at_null",
            ),
            # A grant with neither source is provenance that explains nothing;
            # one with both would be revoked twice over when either tie ends.
            models.CheckConstraint(
                condition=(
                    Q(membership__isnull=False, coordinator__isnull=True)
                    | Q(membership__isnull=True, coordinator__isnull=False)
                ),
                name="org_unit_grant_exactly_one_source",
            ),
            # ``grant_source`` is what queries read; the FKs are what the
            # reconciler revokes by. They must never disagree.
            models.CheckConstraint(
                condition=(
                    Q(grant_source=GrantSource.MEMBERSHIP, membership__isnull=False)
                    | Q(grant_source=GrantSource.COORDINATOR, coordinator__isnull=False)
                ),
                name="org_unit_grant_source_matches_relation",
            ),
        ]
        verbose_name = "Organizational Unit Grant"
        verbose_name_plural = "Organizational Unit Grants"
        db_table = "organizational_unit_grants"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.workspace_member_id} -> {self.project_id} via {self.organizational_unit_id}"


class OrganizationalProjectAccessState(BaseModel):
    """
    Aggregate state of what the organizational layer has applied to the
    native ``ProjectMember`` for one (workspace member, project) pair.

    This table is what makes rollback safe. The reconciler may only reduce or
    remove access when the current ``ProjectMember.role`` still equals
    ``last_applied_role`` — i.e. when there is evidence the current value is
    still the one the organizational layer wrote. Any drift (an admin manually
    promoting or demoting someone) is treated as manual and left untouched.

    Attributes:
        workspace_member (WorkspaceMember): The person.
        project (Project): The project.
        workspace (Workspace): Denormalized for cheap querying.
        project_member (ProjectMember): The native row being managed.
        baseline_role (int): Manual role that existed before the first
            organizational write; ``None`` when the person was not a project
            member at that time.
        last_applied_role (int): Last role written by the reconciler;
            ``None`` after the layer fully withdrew.
        created_by_org_layer (bool): Whether the ``ProjectMember`` row was
            created (not merely updated) by the organizational layer.
        last_reconciled_at (datetime): Last time the reconciler touched this pair.
    """

    workspace_member = models.ForeignKey(
        "db.WorkspaceMember",
        on_delete=models.CASCADE,
        related_name="organizational_access_states",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="organizational_access_states",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="organizational_access_states",
    )
    project_member = models.ForeignKey(
        "db.ProjectMember",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organizational_access_states",
    )
    baseline_role = models.PositiveSmallIntegerField(choices=ROLE_CHOICES, null=True, blank=True)
    last_applied_role = models.PositiveSmallIntegerField(choices=ROLE_CHOICES, null=True, blank=True)
    created_by_org_layer = models.BooleanField(default=False)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["workspace_member", "project", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace_member", "project"],
                condition=Q(deleted_at__isnull=True),
                name="org_access_state_unique_member_project_when_deleted_at_null",
            )
        ]
        verbose_name = "Organizational Project Access State"
        verbose_name_plural = "Organizational Project Access States"
        db_table = "organizational_project_access_states"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.workspace_member_id} @ {self.project_id} (applied={self.last_applied_role})"


class IssueOrganizationalUnit(BaseModel):
    """
    Sidecar linking a work item to the organizational unit responsible for it.

    Per FORK.md the core ``Issue`` model gains no column: the responsible unit
    lives here. Plane still requires an assignee to be a person who is an
    active project member, so this link marks a unit as *responsible*, and the
    assignment engine turns that into a real assignee.

    Attributes:
        issue (Issue): The work item.
        organizational_unit (OrganizationalUnit): The responsible unit.
        project (Project): Denormalized from the issue for cheap querying.
        workspace (Workspace): Denormalized for cheap querying.
    """

    # Deliberately a ForeignKey and not a OneToOneField. A OneToOneField is a
    # ForeignKey(unique=True), and that unique index covers every row in the
    # table — soft-deleted ones included. Since clearing the responsible unit
    # is a soft delete (``deleted_at`` set, row kept for history), a
    # OneToOneField makes set -> clear -> set fail on a unique violation
    # against the row the user already cleared. The partial constraint below
    # is the real rule: at most one *live* link per work item, with the
    # cleared ones preserved as history.
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="organizational_unit_links",
    )
    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.CASCADE,
        related_name="issue_links",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="issue_organizational_units",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="issue_organizational_units",
    )

    # --- routing state --------------------------------------------------
    # An item owned by an area is either waiting for a person or has one. The
    # two CHECKs below keep those two facts from drifting apart in the
    # database; the third fact — that the executor is also a live
    # ``IssueAssignee`` — cannot be expressed as a CHECK and is a service
    # invariant (RFC §6.1), verified by test and by audit command.
    routing_state = models.CharField(
        # 32, not the 16 the shortest state would suggest: "allocation_failed"
        # is 17 characters, and Postgres rejects the write rather than
        # truncating it.
        max_length=32,
        choices=RoutingState.choices,
        default=RoutingState.QUEUED,
    )
    queue_reason = models.CharField(
        max_length=32,
        choices=QueueReason.choices,
        blank=True,
        default="",
    )
    queued_at = models.DateTimeField(null=True, blank=True)
    # Effective assignment SLA for this item (RFC §6.6).
    assignment_due_at = models.DateTimeField(null=True, blank=True)
    # When the area was last told this item is waiting past its deadline. The
    # sweep reads it to avoid alerting the same people about the same item every
    # fifteen minutes; ``None`` means nobody has been told yet.
    last_alerted_at = models.DateTimeField(null=True, blank=True)
    # SET_NULL, not CASCADE: deleting a person must not delete the record that
    # their area owned the work.
    primary_executor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_primary_executions",
    )
    # The decision currently in force. Referenced by name to keep the import
    # one-way: the decision log knows about the link's models, not the other
    # way round. Added in migration 0137, after the log's table exists.
    current_assignment_decision = models.ForeignKey(
        "db.AssignmentDecision",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_for_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue"],
                condition=Q(deleted_at__isnull=True),
                name="issue_org_unit_unique_issue_when_deleted_at_null",
            ),
            # "assigned" without an executor is a lie the queue view would
            # believe; an executor in any other state is a leftover the load
            # count would keep charging to that person.
            models.CheckConstraint(
                condition=~Q(routing_state=RoutingState.ASSIGNED) | Q(primary_executor__isnull=False),
                name="issue_org_unit_assigned_requires_executor",
            ),
            models.CheckConstraint(
                condition=Q(routing_state=RoutingState.ASSIGNED) | Q(primary_executor__isnull=True),
                name="issue_org_unit_executor_only_when_assigned",
            ),
        ]
        indexes = [
            # The coordinator's queue: one area's items in one state.
            models.Index(
                fields=["workspace", "organizational_unit", "routing_state"],
                name="issue_org_unit_queue_idx",
            ),
            # Load per person, which the ranking reads for every candidate.
            models.Index(fields=["primary_executor", "routing_state"], name="issue_org_unit_load_idx"),
        ]
        verbose_name = "Issue Organizational Unit"
        verbose_name_plural = "Issue Organizational Units"
        db_table = "issue_organizational_units"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # Keep the responsible unit and the work item inside one workspace.
        if self.issue.workspace_id != self.organizational_unit.workspace_id:
            raise ValidationError("Issue and organizational unit belong to different workspaces")
        self.project_id = self.issue.project_id
        self.workspace_id = self.issue.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.issue_id} -> {self.organizational_unit_id}"


class WorkspaceMemberAvailability(BaseModel):
    """
    A window during which a workspace member is not available for new work.

    @description Half-open ``[unavailable_from, unavailable_until)``. A null
    ``unavailable_until`` is unbounded: they stay unavailable from ``from``
    onwards until a later window (or a delete) says otherwise. Overlapping
    windows are allowed — any one covering ``now`` is enough. The ranking
    (item 3.2) reads this through ``is_available``; this table does not
    itself reassign anyone.

    Attributes:
        workspace_member (WorkspaceMember): The person.
        workspace (Workspace): Denormalized from the member for cheap querying.
        unavailable_from (datetime): Inclusive start of the window.
        unavailable_until (datetime): Exclusive end; null means open-ended.
        reason (str): ``vacation``, ``leave``, or ``other``.
        source (str): ``manual`` in v1; ``hr`` / ``directory`` reserved.
        external_id (str): The originating system's key, when ``source`` is
            not ``manual``. Blank for windows a person typed themselves.
    """

    workspace_member = models.ForeignKey(
        "db.WorkspaceMember",
        on_delete=models.CASCADE,
        related_name="orca_availability_windows",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="orca_availability_windows",
    )
    unavailable_from = models.DateTimeField()
    unavailable_until = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(
        max_length=16,
        choices=AvailabilityReason.choices,
        default=AvailabilityReason.OTHER,
    )
    source = models.CharField(
        max_length=16,
        choices=AvailabilitySource.choices,
        default=AvailabilitySource.MANUAL,
    )
    external_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            # RFC §5.2: a closed window has to run forwards. Open-ended
            # (until IS NULL) is the "indefinite leave" case and is allowed.
            models.CheckConstraint(
                condition=Q(unavailable_until__isnull=True) | Q(unavailable_until__gt=F("unavailable_from")),
                name="orca_availability_until_after_from",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workspace_member", "unavailable_from"],
                name="orca_avail_member_from_idx",
            ),
        ]
        verbose_name = "Workspace Member Availability"
        verbose_name_plural = "Workspace Member Availabilities"
        db_table = "orca_workspace_member_availability"
        ordering = ("-unavailable_from",)

    def save(self, *args, **kwargs):
        # The CHECK below is the real guard; this is the same rule in a form
        # the API can turn into a 400 instead of an IntegrityError.
        if self.unavailable_until is not None and self.unavailable_until <= self.unavailable_from:
            raise ValidationError("unavailable_until must be after unavailable_from")
        member_workspace_id = self.workspace_member.workspace_id
        if self.workspace_id and self.workspace_id != member_workspace_id:
            raise ValidationError("Availability window and workspace member belong to different workspaces")
        self.workspace_id = member_workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.workspace_member_id}: {self.unavailable_from} -> {self.unavailable_until}"


class MembershipAllocationSettings(BaseModel):
    """
    Per-membership knobs for whether, and how much, new work this person takes.

    @description ``accepts_new_work`` is the "I am not taking more from this
    area" toggle. ``max_open_items`` is an optional personal cap, tighter than
    the area's ``policy.max_open_items_per_member`` when both are set. Missing
    row means the defaults: they accept work, no personal cap. OneToOne on
    the membership so two settings rows cannot disagree about the same person
    in the same area.

    Attributes:
        membership (OrganizationalUnitMembership): The membership these knobs
            apply to.
        accepts_new_work (bool): False opts this membership out of ranking.
        max_open_items (int): Personal cap on open items; null means none.
    """

    membership = models.OneToOneField(
        OrganizationalUnitMembership,
        on_delete=models.CASCADE,
        related_name="allocation_settings",
    )
    accepts_new_work = models.BooleanField(default=True)
    max_open_items = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Membership Allocation Settings"
        verbose_name_plural = "Membership Allocation Settings"
        db_table = "orca_membership_allocation_settings"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.membership_id}: accepts={self.accepts_new_work} cap={self.max_open_items}"
