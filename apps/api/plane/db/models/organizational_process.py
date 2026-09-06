# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Recurring processes, projected into Plane just far enough to be readable.

The templates live outside this codebase — in the orchestrator's repository,
versioned in Git (RFC §7.2, F12, F19). What lives here is the minimum needed
to answer three questions inside Plane, which is exactly the part an external
system cannot answer for a person looking at a work item:

* which instance of which process is this item a step of?
* how does that step finish — by itself, after a review, or only by hand?
* what deadlines did this item start with, and what are they now?

Deliberately not here: the template, its steps, its branching, its schedule.
Modelling those would make Plane a workflow engine, which F12 decided it is
not, and would leave two definitions of the same process to drift apart.
"""

# Django imports
from django.conf import settings
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel
from .organizational_assignment import AppendOnlyModel


class ServiceLevelSource(models.TextChoices):
    """Where a work item's deadlines came from."""

    UNIT_PROJECT = "unit_project", "Area policy for this project"
    UNIT = "unit", "Area policy"
    PROCESS = "process", "Process template"
    MANUAL = "manual", "Set by hand"


class ProcessInstanceStatus(models.TextChoices):
    """Where one run of a process stands."""

    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class CompletionMode(models.TextChoices):
    """
    How a step of a process finishes.

    @description ``AUTOMATIC`` is the orchestrator saying "this is done" and
    the item moving to a completed state. ``AUTOMATIC_WITH_REVIEW`` is the
    same claim, held for a person to confirm — the item stops where a reviewer
    will see it rather than closing itself. ``MANUAL`` refuses the automatic
    call outright: some steps are only ever finished by the person doing them,
    and a robot saying otherwise is how a checklist becomes a lie.
    """

    AUTOMATIC = "automatic", "Automatic"
    AUTOMATIC_WITH_REVIEW = "automatic_with_review", "Automatic, with review"
    MANUAL = "manual", "Manual"


class IssueServiceLevel(BaseModel):
    """
    The deadlines one work item is held to, and the ones it started with.

    @description ``assignment_due_at`` already lives on
    ``IssueOrganizationalUnit`` because the queue reads it on every page. This
    table is the record around it: where the deadline came from, which version
    of that source, who changed it, and — the part that cannot be recovered
    afterwards — what it was originally.

    ``original_*`` is written once and never again. Without it "we always
    deliver in four hours" is unfalsifiable: every breach can be answered by
    moving the deadline, and nothing remembers that it moved.

    Attributes:
        issue (Issue): The work item.
        assignment_due_at (datetime): By when somebody must be on it.
        completion_due_at (datetime): By when it must be done.
        original_assignment_due_at (datetime): The first value, immutable.
        original_completion_due_at (datetime): The first value, immutable.
        source (str): Which rule set these deadlines.
        source_version (str): The policy version or the template version.
        changed_by (User): Who last moved them, when a person did.
        change_reason (str): Why.
    """

    issue = models.OneToOneField("db.Issue", on_delete=models.CASCADE, related_name="orca_service_level")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_service_levels")
    assignment_due_at = models.DateTimeField(null=True, blank=True)
    completion_due_at = models.DateTimeField(null=True, blank=True)
    original_assignment_due_at = models.DateTimeField(null=True, blank=True)
    original_completion_due_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=16, choices=ServiceLevelSource.choices, default=ServiceLevelSource.UNIT)
    source_version = models.CharField(max_length=32, blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_service_level_changes",
    )
    change_reason = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Issue Service Level"
        verbose_name_plural = "Issue Service Levels"
        db_table = "orca_issue_service_levels"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # The originals are the row's memory of what was first promised, so
        # they are filled once, on the way in, and never touched again — not
        # even by a caller that passes them explicitly.
        if self._state.adding:
            self.original_assignment_due_at = self.assignment_due_at
            self.original_completion_due_at = self.completion_due_at
        else:
            stored = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("original_assignment_due_at", "original_completion_due_at")
                .first()
            )
            if stored:
                self.original_assignment_due_at = stored["original_assignment_due_at"]
                self.original_completion_due_at = stored["original_completion_due_at"]
        if not self.workspace_id and self.issue_id:
            self.workspace_id = self.issue.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.issue_id} due {self.completion_due_at} ({self.source})"


class ProcessInstanceReference(BaseModel):
    """
    One run of a process, as much of it as Plane needs to know.

    @description The orchestrator owns the template and the run; this is the
    projection that lets a person inside Plane see that four work items are
    four steps of the same onboarding rather than four unrelated items.

    ``template_version`` is mandatory in the API for a reason worth stating:
    a process whose definition changed halfway through has instances that ran
    under two different rules, and an instance that cannot say which one ran
    it is one nobody can audit afterwards.

    Attributes:
        workspace (Workspace): Where it runs.
        external_source (str): The system that owns the process.
        external_instance_id (str): Its id for this run.
        template_name (str), template_version (str): What ran, and which version.
        status (str): ``running``, ``completed`` or ``cancelled``.
        started_at, completed_at (datetime): When.
    """

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_process_instances")
    external_source = models.CharField(max_length=255)
    external_instance_id = models.CharField(max_length=255)
    template_name = models.CharField(max_length=255)
    template_version = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=ProcessInstanceStatus.choices, default=ProcessInstanceStatus.RUNNING
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ["workspace", "external_source", "external_instance_id", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "external_source", "external_instance_id"],
                condition=Q(deleted_at__isnull=True),
                name="orca_process_instance_unique_external",
            )
        ]
        indexes = [models.Index(fields=["workspace", "status"], name="orca_process_status_idx")]
        verbose_name = "Process Instance Reference"
        verbose_name_plural = "Process Instance References"
        db_table = "orca_process_instance_references"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.external_source}:{self.external_instance_id} ({self.template_name} v{self.template_version})"


class ProcessInstanceItem(BaseModel):
    """
    One step of one process instance, bound to the work item that carries it.

    @description One work item is a step of at most one instance: an item that
    belonged to two processes would have two completion rules and no way to
    choose between them.

    Attributes:
        process_instance (ProcessInstanceReference): The run.
        issue (Issue): The work item that is this step.
        step_key (str): The template's name for it.
        completion_mode (str): How it finishes.
    """

    process_instance = models.ForeignKey(ProcessInstanceReference, on_delete=models.CASCADE, related_name="items")
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_process_items")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_process_items")
    step_key = models.CharField(max_length=255)
    completion_mode = models.CharField(max_length=24, choices=CompletionMode.choices, default=CompletionMode.MANUAL)

    class Meta:
        unique_together = ["issue", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["issue"],
                condition=Q(deleted_at__isnull=True),
                name="orca_process_item_unique_issue",
            )
        ]
        verbose_name = "Process Instance Item"
        verbose_name_plural = "Process Instance Items"
        db_table = "orca_process_instance_items"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        if not self.workspace_id and self.issue_id:
            self.workspace_id = self.issue.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.process_instance_id}/{self.step_key} -> {self.issue_id}"


class ProcessCompletionEvent(AppendOnlyModel):
    """
    A claim that a step is finished, and what backed it.

    @description Append-only, like the decision log and for the same reason:
    "who said this was done, and on what evidence?" is asked after the fact,
    and a row that can be edited answers nothing.

    Not an ``AssignmentDecision``: completing a step decides nothing about who
    does the work. Putting it in the same table would make "how often does the
    area reassign?" unanswerable without filtering out rows that are not
    assignments at all.

    Attributes:
        issue (Issue): The step that was completed.
        source (str): The system that claimed it.
        event_id (str): That system's id for the claim, so a replay is visible.
        rule_version (str): Which version of the rule decided it.
        evidence (dict): Whatever the caller sent to justify it. Kept verbatim.
        mode (str): The completion mode in force when the claim arrived.
        applied (bool): Whether the item actually moved.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_completion_events")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_completion_events")
    source = models.CharField(max_length=255, blank=True, default="")
    event_id = models.CharField(max_length=255, blank=True, default="")
    rule_version = models.CharField(max_length=64, blank=True, default="")
    evidence = models.JSONField(default=dict, blank=True)
    mode = models.CharField(max_length=24, choices=CompletionMode.choices, default=CompletionMode.MANUAL)
    applied = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["issue", "created_at"], name="orca_completion_issue_idx"),
            models.Index(fields=["workspace", "created_at"], name="orca_completion_workspace_idx"),
        ]
        verbose_name = "Process Completion Event"
        verbose_name_plural = "Process Completion Events"
        db_table = "orca_process_completion_events"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.issue_id} completed via {self.source or 'unknown'} ({self.mode})"
