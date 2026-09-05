# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Who an area's work goes to, and the record of every time that was decided.

Three tables, all sidecar (FORK.md): the policy that says *how* an area hands
work out, the append-only decision log that says *what happened* each time,
and the append-only trail of which area owned a work item.

**Why the decision log is append-only.** An allocation is contested by nature —
somebody was chosen and somebody else was not, a coordinator overrode the
allocator, an item came back to the queue. A row that can be edited afterwards
cannot answer "why does this person have this?" a week later, which is the
only question the table exists to answer. Rows are written once; a change is a
new row pointing at the one it supersedes.

See docs/orca-work-management-rfc.md §5.2.
"""

# Django imports
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel
from .organizational_unit import OrganizationalUnit, OrganizationalUnitProject


class AssignmentMode(models.TextChoices):
    """How an area turns responsibility into an assignee."""

    MANUAL = "manual", "Manual"
    SELF_CLAIM = "self_claim", "Self claim"
    LEAST_LOADED = "least_loaded", "Least loaded"
    EXPLICIT = "explicit", "Explicit"


class RequestedAssignmentMode(models.TextChoices):
    """
    What the caller asked for, which is not the same as what happened.

    @description ``default`` means "whatever the policy says" — recording it
    distinguishes a caller that expressed no preference from one that asked for
    the mode the policy happened to have.
    """

    DEFAULT = "default", "Default"
    EXPLICIT = "explicit", "Explicit"
    MANUAL = "manual", "Manual"
    SELF_CLAIM = "self_claim", "Self claim"
    LEAST_LOADED = "least_loaded", "Least loaded"


class PolicySource(models.TextChoices):
    """Which policy the decision was taken under, most specific first."""

    REQUEST = "request", "Request"
    UNIT_PROJECT = "unit_project", "Unit project"
    UNIT = "unit", "Unit"
    FALLBACK = "fallback", "Fallback"


class DecisionTrigger(models.TextChoices):
    """What set the allocation off."""

    PUBLIC_API = "public_api", "Public API"
    INTERNAL_API = "internal_api", "Internal API"
    UI_CLAIM = "ui_claim", "UI claim"
    UI_COORDINATOR = "ui_coordinator", "UI coordinator"
    REASSIGN = "reassign", "Reassign"
    AVAILABILITY = "availability", "Availability"
    RETURN_TO_QUEUE = "return_to_queue", "Return to queue"
    COMMAND = "command", "Command"


class DecisionOutcome(models.TextChoices):
    """How the allocation ended."""

    ASSIGNED = "assigned", "Assigned"
    QUEUED = "queued", "Queued"
    ALLOCATION_FAILED = "allocation_failed", "Allocation failed"
    REJECTED = "rejected", "Rejected"


class ResponsibilitySource(models.TextChoices):
    """Where a change of responsible area came from."""

    PUBLIC_API = "public_api", "Public API"
    INTERNAL_API = "internal_api", "Internal API"
    UI = "ui", "UI"
    COMMAND = "command", "Command"


class AppendOnlyModel(BaseModel):
    """
    A row that is written once and never changed.

    @description Guards the instance path: ``save()`` on a row that already
    exists raises, and so does a soft delete, since that is a write too. A
    queryset ``update()`` bypasses ``save()`` the way it does for any model —
    the guard makes the append-only rule the obvious one to follow, it is not a
    permission system.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(f"{type(self).__name__} rows are append-only; write a new row instead of editing one")
        super().save(*args, **kwargs)


class OrganizationalUnitAssignmentPolicy(BaseModel):
    """
    How one area hands work out, optionally overridden per project.

    @description A row with ``unit_project`` null is the area's default; a row
    with it set applies to that one project. ``version`` is frozen into every
    decision taken under the policy, so a decision stays readable after the
    policy is edited — otherwise the log would silently describe today's rules
    for yesterday's choices.

    Attributes:
        organizational_unit (OrganizationalUnit): The area.
        unit_project (OrganizationalUnitProject): The project this overrides,
            or ``None`` for the area's default policy.
        default_mode (str): Mode used when the caller expresses no preference.
        allowed_modes (list): Modes a caller may ask for; must contain
            ``default_mode``.
        assignment_sla_seconds (int): Default assignment SLA, in seconds.
        max_open_items_per_member (int): Hard cap used by ``least_loaded``.
        version (int): Incremented on every save.
    """

    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.CASCADE,
        related_name="assignment_policies",
    )
    unit_project = models.ForeignKey(
        OrganizationalUnitProject,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assignment_policies",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="organizational_assignment_policies",
    )
    default_mode = models.CharField(
        max_length=16,
        choices=[
            (AssignmentMode.MANUAL, AssignmentMode.MANUAL.label),
            (AssignmentMode.SELF_CLAIM, AssignmentMode.SELF_CLAIM.label),
            (AssignmentMode.LEAST_LOADED, AssignmentMode.LEAST_LOADED.label),
        ],
        default=AssignmentMode.MANUAL,
    )
    allowed_modes = models.JSONField(default=list)
    assignment_sla_seconds = models.PositiveIntegerField(null=True, blank=True)
    max_open_items_per_member = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            # Postgres treats NULLs as distinct, so one constraint over
            # (unit, unit_project) would let an area collect any number of
            # default policies. Two partial constraints, one per case.
            models.UniqueConstraint(
                fields=["organizational_unit"],
                condition=Q(deleted_at__isnull=True, unit_project__isnull=True),
                name="orca_policy_unique_unit_default",
            ),
            models.UniqueConstraint(
                fields=["organizational_unit", "unit_project"],
                condition=Q(deleted_at__isnull=True, unit_project__isnull=False),
                name="orca_policy_unique_unit_project",
            ),
        ]
        verbose_name = "Organizational Unit Assignment Policy"
        verbose_name_plural = "Organizational Unit Assignment Policies"
        db_table = "organizational_unit_assignment_policies"
        ordering = ("-created_at",)

    def clean(self):
        """
        @description A policy whose ``allowed_modes`` does not contain its own
        ``default_mode`` rejects the very mode it falls back to, so every
        allocation under it would fail. Caught here rather than at allocation
        time, where it would look like a bug in the allocator.
        @raises ValidationError: When the two fields contradict each other.
        """
        modes = self.allowed_modes or []
        if not isinstance(modes, list):
            raise ValidationError({"allowed_modes": "allowed_modes must be a list of assignment modes"})

        unknown = [mode for mode in modes if mode not in AssignmentMode.values]
        if unknown:
            raise ValidationError({"allowed_modes": f"unknown assignment modes: {', '.join(sorted(unknown))}"})

        if modes and self.default_mode not in modes:
            raise ValidationError({"allowed_modes": "allowed_modes must contain default_mode"})

    def save(self, *args, **kwargs):
        if not self.allowed_modes:
            self.allowed_modes = [str(self.default_mode)]
        if not self._state.adding:
            self.version = (self.version or 1) + 1
        if self.unit_project_id and not self.organizational_unit_id:
            self.organizational_unit_id = self.unit_project.organizational_unit_id
        if not self.workspace_id and self.organizational_unit_id:
            self.workspace_id = self.organizational_unit.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        scope = self.unit_project_id or "default"
        return f"{self.organizational_unit_id}/{scope}: {self.default_mode} v{self.version}"


class AssignmentDecision(AppendOnlyModel):
    """
    One allocation, with everything needed to explain it afterwards.

    @description The candidate snapshot is what makes the decision auditable
    without re-running the ranking against data that has since changed: it
    records the numbers the choice was made on, and why each person who was
    passed over was passed over. Only user ids go in it — no names, no emails.

    Attributes:
        candidates_snapshot (list): ``{user_id, total_open, unit_open,
            last_auto_at, excluded_reason?}`` per candidate considered.
        supersedes (AssignmentDecision): The decision this one replaces.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_assignment_decisions")
    organizational_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.CASCADE,
        related_name="assignment_decisions",
    )
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="orca_assignment_decisions")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_assignment_decisions")

    trigger = models.CharField(max_length=20, choices=DecisionTrigger.choices)
    requested_mode = models.CharField(max_length=16, choices=RequestedAssignmentMode.choices, null=True, blank=True)
    effective_mode = models.CharField(max_length=16, choices=AssignmentMode.choices)
    policy_source = models.CharField(max_length=16, choices=PolicySource.choices)
    policy = models.ForeignKey(
        OrganizationalUnitAssignmentPolicy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decisions",
    )
    policy_version = models.PositiveIntegerField(null=True, blank=True)
    # The ranking that produced this decision. Bumped when the algorithm
    # changes, so old decisions are not read as if they used today's rules.
    algorithm_version = models.CharField(max_length=16, default="lb-1")
    outcome = models.CharField(max_length=20, choices=DecisionOutcome.choices)
    candidates_snapshot = models.JSONField(default=list)
    chosen_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_assignment_decisions_won",
    )
    previous_primary_executor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_assignment_decisions_lost",
    )
    # Null means the system decided, which is a different fact from "we do not
    # know who decided" and is why the column is nullable rather than blank.
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_assignment_decisions_made",
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
    )
    reason = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["issue", "created_at"], name="orca_decision_issue_idx"),
            models.Index(fields=["organizational_unit", "created_at"], name="orca_decision_unit_idx"),
        ]
        verbose_name = "Assignment Decision"
        verbose_name_plural = "Assignment Decisions"
        db_table = "orca_assignment_decisions"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.issue_id}: {self.effective_mode} -> {self.outcome}"


class IssueResponsibilityEvent(AppendOnlyModel):
    """
    A change in which area owns a work item.

    @description Separate from ``AssignmentDecision`` because they answer
    different questions: this one is "whose work is this", the other is "who is
    doing it". A transfer between areas produces one of each. ``from_unit``
    null is the first assignment of an area; ``to_unit`` null is its removal.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_responsibility_events")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_responsibility_events")
    from_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsibility_events_from",
    )
    to_unit = models.ForeignKey(
        OrganizationalUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsibility_events_to",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_responsibility_events",
    )
    source = models.CharField(max_length=16, choices=ResponsibilitySource.choices)
    reason = models.TextField(blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["issue", "created_at"], name="orca_resp_event_issue_idx")]
        verbose_name = "Issue Responsibility Event"
        verbose_name_plural = "Issue Responsibility Events"
        db_table = "orca_issue_responsibility_events"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.issue_id}: {self.from_unit_id} -> {self.to_unit_id}"
