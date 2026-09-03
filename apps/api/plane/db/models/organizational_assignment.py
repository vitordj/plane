# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
How an area decides who does the work, and the record of every such decision.

Three tables, all sidecar (FORK.md): the policy an area applies, the decision
it produced, and the event that moved responsibility from one area to another.

The last two are append-only on purpose. An audit trail that can be edited
answers "who was this assigned to?" with whatever the last writer preferred,
which is precisely the question a coordinator asks when something went wrong.
"""

# Django imports
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel


class AssignmentMode(models.TextChoices):
    """
    How the executor of a work item is chosen.

    MANUAL — a coordinator assigns it; it waits in the queue until then.
    SELF_CLAIM — any eligible member of the area may take it.
    LEAST_LOADED — the system picks whoever currently carries the least work.
    EXPLICIT — the caller named the person (automation asking for someone
    specific); it never goes through policy resolution, only eligibility.
    """

    MANUAL = "manual", "Manual"
    SELF_CLAIM = "self_claim", "Self claim"
    LEAST_LOADED = "least_loaded", "Least loaded"
    EXPLICIT = "explicit", "Explicit"


class RequestedAssignmentMode(models.TextChoices):
    """
    What the caller asked for, which is not always what happened.

    @description ``DEFAULT`` means "whatever the area's policy says" — worth
    distinguishing from a caller that named a mode, because a policy change
    later should move the first and not the second.
    """

    DEFAULT = "default", "Default"
    MANUAL = "manual", "Manual"
    SELF_CLAIM = "self_claim", "Self claim"
    LEAST_LOADED = "least_loaded", "Least loaded"
    EXPLICIT = "explicit", "Explicit"


class PolicySource(models.TextChoices):
    """Which policy the effective mode came from — request, project, area, or none."""

    REQUEST = "request", "Request"
    UNIT_PROJECT = "unit_project", "Area and project"
    UNIT = "unit", "Area"
    FALLBACK = "fallback", "Fallback"


class DecisionTrigger(models.TextChoices):
    """What set the decision off. Answers "why did this move?"."""

    PUBLIC_API = "public_api", "Public API"
    INTERNAL_API = "internal_api", "Internal API"
    UI_CLAIM = "ui_claim", "Claimed in the app"
    UI_COORDINATOR = "ui_coordinator", "Coordinator in the app"
    REASSIGN = "reassign", "Reassignment"
    AVAILABILITY = "availability", "Availability sweep"
    RETURN_TO_QUEUE = "return_to_queue", "Returned to the queue"
    COMMAND = "command", "Management command"


class DecisionOutcome(models.TextChoices):
    """What the decision produced."""

    ASSIGNED = "assigned", "Assigned"
    QUEUED = "queued", "Queued"
    ALLOCATION_FAILED = "allocation_failed", "Allocation failed"
    REJECTED = "rejected", "Rejected"


class ResponsibilitySource(models.TextChoices):
    """Where a change of responsible area came from."""

    PUBLIC_API = "public_api", "Public API"
    INTERNAL_API = "internal_api", "Internal API"
    UI = "ui", "App"
    COMMAND = "command", "Management command"


class OrganizationalUnitAssignmentPolicy(BaseModel):
    """
    How one area assigns work — for all of it, or for one project.

    @description A policy with no ``unit_project`` is the area's default; one
    with a ``unit_project`` overrides it for that project alone. That is the
    whole hierarchy, deliberately: two levels are enough to say "this area
    works manually, except in the onboarding project, where whoever is free
    takes it", and a third would be a rule nobody could predict the result of.

    ``allowed_modes`` is the list a caller may ask for. A request outside it is
    refused rather than quietly downgraded (invariant I7): an automation that
    asked for automatic allocation and silently got a manual queue would look
    like it worked.
    """

    organizational_unit = models.ForeignKey(
        "db.OrganizationalUnit",
        on_delete=models.CASCADE,
        related_name="assignment_policies",
    )
    # Null means "the area's default policy"; set means "this project only".
    unit_project = models.ForeignKey(
        "db.OrganizationalUnitProject",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assignment_policies",
    )
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="assignment_policies",
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

    # Null means no service level: the queue is watched by people, not clocks.
    assignment_sla_seconds = models.PositiveIntegerField(null=True, blank=True)
    max_open_items_per_member = models.PositiveIntegerField(null=True, blank=True)

    # Where an automatically closed process step lands. Null means "the first
    # state in the project's completed group" — a sensible default that most
    # projects never need to override, and a named one for the projects whose
    # completed group has three states that mean different things.
    completed_state = models.ForeignKey(
        "db.State",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_completing_policies",
    )
    # Where a step closed "with review" lands. Null falls back to leaving the
    # state alone and labelling it, because a project with no review state has
    # nowhere better to put it, and moving it to done would be the one thing
    # "with review" exists to prevent.
    review_state = models.ForeignKey(
        "db.State",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_reviewing_policies",
    )

    is_active = models.BooleanField(default=True)
    # Frozen into every decision, so a decision can be read against the rules
    # that were in force when it was made rather than today's.
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            # Postgres treats NULLs as distinct, so one unique constraint over
            # (unit, unit_project) would not stop two default policies. Two
            # partial constraints say what is actually meant.
            models.UniqueConstraint(
                fields=["organizational_unit"],
                condition=Q(unit_project__isnull=True, deleted_at__isnull=True),
                name="orca_policy_one_default_per_unit",
            ),
            models.UniqueConstraint(
                fields=["organizational_unit", "unit_project"],
                condition=Q(unit_project__isnull=False, deleted_at__isnull=True),
                name="orca_policy_one_per_unit_project",
            ),
        ]
        verbose_name = "Assignment Policy"
        verbose_name_plural = "Assignment Policies"
        db_table = "orca_assignment_policies"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.organizational_unit_id} · {self.default_mode}"

    def clean(self):
        """
        @description Refuse a policy whose default is not among the modes it
        allows: it would reject its own default at resolution time, which is
        the kind of contradiction better caught here than in production.
        @raises ValidationError: When ``default_mode`` is not in ``allowed_modes``.
        """
        super().clean()
        modes = self.allowed_modes or []
        if self.default_mode not in modes:
            raise ValidationError({"allowed_modes": "allowed_modes must contain default_mode"})

    def save(self, *args, **kwargs):
        # An empty allowed_modes would mean "nothing may be asked for", which
        # nobody intends; default it to the mode this policy already applies.
        if not self.allowed_modes:
            self.allowed_modes = [self.default_mode]
        if not self._state.adding:
            self.version = (self.version or 0) + 1
        super().save(*args, **kwargs)


class AppendOnlyModel(BaseModel):
    """
    A row that is written once and never changed.

    @description Both of the audit tables below are only worth reading if
    nothing can rewrite them, so the model refuses the update rather than
    relying on nobody trying. Deleting is left alone: soft deletion is how the
    rest of the schema retires rows, and history is retired with its work item.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(f"{type(self).__name__} rows are append-only; write a new row instead of editing one")
        super().save(*args, **kwargs)


class AssignmentDecision(AppendOnlyModel):
    """
    One decision about who executes a work item, and everything behind it.

    @description Written on every change of primary executor or routing state
    (invariant I5). It carries the policy and the ranking snapshot, not just
    the answer, because the question a coordinator actually asks is "why them,
    and not me?" — and because a ranking nobody can inspect is one nobody can
    trust.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_assignment_decisions")
    organizational_unit = models.ForeignKey(
        "db.OrganizationalUnit", on_delete=models.CASCADE, related_name="assignment_decisions"
    )
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="orca_assignment_decisions")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_assignment_decisions")

    trigger = models.CharField(max_length=24, choices=DecisionTrigger.choices)
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
    # Which ranking produced this. "lb-1" is the first; Phase 3 makes it "lb-2"
    # when availability joins the rules, and old decisions keep saying "lb-1".
    algorithm_version = models.CharField(max_length=16, default="lb-1")

    outcome = models.CharField(max_length=24, choices=DecisionOutcome.choices)
    # [{user_id, total_open, unit_open, last_auto_at, excluded_reason?}] — ids
    # only, never names: this is an audit record, not a directory.
    candidates_snapshot = models.JSONField(default=list, blank=True)

    chosen_assignee = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="orca_decisions_chosen"
    )
    previous_primary_executor = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="orca_decisions_superseded"
    )
    # Null means the system decided, which is a different thing from unknown.
    decided_by = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="orca_decisions_made"
    )
    supersedes = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by"
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
        return f"{self.issue_id} · {self.effective_mode} · {self.outcome}"


class IssueResponsibilityEvent(AppendOnlyModel):
    """
    A change in which area is responsible for a work item.

    @description Separate from ``AssignmentDecision`` because they answer
    different questions: this one is "whose work is this?", the other is "who
    is doing it?". A transfer between areas is one event and, usually, several
    decisions.

    ``from_unit`` null means the work item had no area before; ``to_unit`` null
    means the area was cleared and the item went back to being an ordinary
    Plane work item.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_responsibility_events")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_responsibility_events")
    from_unit = models.ForeignKey(
        "db.OrganizationalUnit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsibility_events_from",
    )
    to_unit = models.ForeignKey(
        "db.OrganizationalUnit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsibility_events_to",
    )
    actor = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="orca_responsibility_events"
    )
    source = models.CharField(max_length=16, choices=ResponsibilitySource.choices)
    reason = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["issue", "created_at"], name="orca_responsibility_issue_idx"),
        ]
        verbose_name = "Issue Responsibility Event"
        verbose_name_plural = "Issue Responsibility Events"
        db_table = "orca_issue_responsibility_events"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.issue_id}: {self.from_unit_id} -> {self.to_unit_id}"
