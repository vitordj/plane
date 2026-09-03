# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Recurring processes, projected into Plane.

The templates live outside Plane, in Git, run by a sidecar orchestrator
(FORK.md §1.B, RFC F19). What lives here is the smallest projection that makes
a running instance legible from inside the product: which work items belong to
it, which step each one is, and whether the whole thing is finished.

The distinction matters because the alternative — a workflow engine inside
Plane — is a much larger thing to own, and it would have to be kept in step
with the templates anyway. This way the orchestrator owns the process and
Plane owns the work, and the only thing shared between them is a name and a
version.

Service levels are here too, and also lateral (RFC F22): ``target_date`` on a
work item is a date somebody can edit, which makes it a projection, not a
promise. What the area actually promised is kept where editing the date cannot
touch it.
"""

# Django imports
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel
from .organizational_assignment import AppendOnlyModel


class ServiceLevelSource(models.TextChoices):
    """Where a work item's promised dates came from."""

    UNIT_PROJECT = "unit_project", "The area's policy for this project"
    UNIT = "unit", "The area's own policy"
    PROCESS = "process", "The process template"
    MANUAL = "manual", "Somebody set it by hand"


class ProcessInstanceStatus(models.TextChoices):
    """How one run of a process is going."""

    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class CompletionMode(models.TextChoices):
    """
    Who may declare a step done.

    @description Decided per step in the template, because the answer differs
    inside one process: "the document was uploaded" is a fact a system can
    assert, "the interview went well" is not.
    """

    AUTOMATIC = "automatic", "The system closes it"
    AUTOMATIC_WITH_REVIEW = "automatic_with_review", "The system flags it for review"
    MANUAL = "manual", "Only a person closes it"


class IssueServiceLevel(BaseModel):
    """
    What was promised about a work item, kept where editing a date cannot reach.

    @description Plane's ``target_date`` is a field anybody on the work item
    can change, so it cannot also be the record of what an area committed to.
    This row is that record: the dates in force, the dates originally set, and
    who moved them.

    ``original_assignment_due_at`` and ``original_completion_due_at`` are
    written once and never again — a service level that quietly follows the
    work is not a service level, and the gap between the original and the
    current is the only thing that makes a report about lateness meaningful.

    Attributes:
        issue (Issue): The work item.
        assignment_due_at (datetime): When somebody should have taken it.
        completion_due_at (datetime): When it should be done.
        original_* (datetime): The first values, immutable.
        source (str): Which rule produced them.
        source_version (str): The version of that rule — a policy version or a
            template version — so a promise can be read against what was in
            force when it was made.
        changed_by (User): Who last moved the dates, when a person did.
        change_reason (str): Why.
    """

    issue = models.OneToOneField("db.Issue", on_delete=models.CASCADE, related_name="orca_service_level")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_service_levels")

    assignment_due_at = models.DateTimeField(null=True, blank=True)
    completion_due_at = models.DateTimeField(null=True, blank=True)
    original_assignment_due_at = models.DateTimeField(null=True, blank=True)
    original_completion_due_at = models.DateTimeField(null=True, blank=True)

    source = models.CharField(max_length=16, choices=ServiceLevelSource.choices, default=ServiceLevelSource.UNIT)
    source_version = models.CharField(max_length=64, blank=True, default="")
    changed_by = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="orca_service_level_changes"
    )
    change_reason = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            # "What is late?", which is the only question this table exists for.
            models.Index(fields=["workspace", "completion_due_at"], name="orca_sla_completion_idx"),
        ]
        verbose_name = "Issue Service Level"
        verbose_name_plural = "Issue Service Levels"
        db_table = "orca_issue_service_levels"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # The originals are set from the first values seen and never again.
        # Enforced here rather than by trusting callers: this row's whole value
        # is that one pair of columns cannot move.
        if self._state.adding:
            if self.original_assignment_due_at is None:
                self.original_assignment_due_at = self.assignment_due_at
            if self.original_completion_due_at is None:
                self.original_completion_due_at = self.completion_due_at
        else:
            stored = (
                IssueServiceLevel.objects.filter(pk=self.pk)
                .values("original_assignment_due_at", "original_completion_due_at")
                .first()
            )
            if stored is not None:
                self.original_assignment_due_at = stored["original_assignment_due_at"]
                self.original_completion_due_at = stored["original_completion_due_at"]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.issue_id} due {self.completion_due_at}"


class ProcessInstanceReference(BaseModel):
    """
    One run of a process, as far as Plane needs to know about it.

    @description Identified by where it came from and the id that system uses
    — a client number, a ticket id — because that is the only identifier both
    sides already agree on. The orchestrator can then reconnect to a running
    instance after a restart without keeping its own map.

    Attributes:
        external_source (str): The system that started it.
        external_instance_id (str): Its id for this run.
        template_name (str): Which process.
        template_version (str): Which version of it — mandatory, because a
            template that changed mid-run explains a great deal later.
        status (str): running, completed or cancelled.
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
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "external_source", "external_instance_id"],
                condition=Q(deleted_at__isnull=True),
                name="orca_process_instance_unique",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status"], name="orca_process_status_idx"),
        ]
        verbose_name = "Process Instance Reference"
        verbose_name_plural = "Process Instance References"
        db_table = "orca_process_instances"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.external_source}:{self.external_instance_id}"


class ProcessInstanceItem(BaseModel):
    """
    One work item's place in one run of a process.

    @description A work item belongs to at most one instance, which is why the
    link is unique on the issue rather than a join table: a step that belonged
    to two processes would have two answers to "is this done?".

    Attributes:
        process_instance (ProcessInstanceReference): The run.
        issue (Issue): The work item that is this step.
        step_key (str): The step's key in the template.
        completion_mode (str): Who may declare this step done.
    """

    process_instance = models.ForeignKey(ProcessInstanceReference, on_delete=models.CASCADE, related_name="items")
    issue = models.OneToOneField("db.Issue", on_delete=models.CASCADE, related_name="orca_process_item")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_process_items")

    step_key = models.CharField(max_length=255)
    completion_mode = models.CharField(max_length=24, choices=CompletionMode.choices, default=CompletionMode.MANUAL)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue"],
                condition=Q(deleted_at__isnull=True),
                name="orca_process_item_unique_issue",
            ),
            models.UniqueConstraint(
                fields=["process_instance", "step_key"],
                condition=Q(deleted_at__isnull=True),
                name="orca_process_item_unique_step",
            ),
        ]
        verbose_name = "Process Instance Item"
        verbose_name_plural = "Process Instance Items"
        db_table = "orca_process_instance_items"
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.process_instance_id}:{self.step_key}"


class ProcessCompletionEvent(AppendOnlyModel):
    """
    A step declared done by something other than a person clicking.

    @description Append-only, and it carries the evidence rather than a
    verdict. The first time somebody disputes an automatic closure — and
    somebody will — the useful answer is "this event, from this system, under
    this version of the rule, carrying this", not "the system closed it".

    Attributes:
        issue (Issue): The step.
        source (str): The system that asserted it.
        event_id (str): That system's id for the assertion, so a replay of the
            same event is recognisable.
        rule_version (str): Which version of the closing rule applied.
        evidence (dict): Whatever the caller sent to justify it.
        mode (str): The completion mode that was in force.
    """

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_completion_events")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_completion_events")
    source = models.CharField(max_length=255)
    event_id = models.CharField(max_length=255, blank=True, default="")
    rule_version = models.CharField(max_length=64, blank=True, default="")
    evidence = models.JSONField(default=dict)
    mode = models.CharField(max_length=24, choices=CompletionMode.choices)

    class Meta:
        indexes = [
            models.Index(fields=["issue", "created_at"], name="orca_completion_issue_idx"),
        ]
        verbose_name = "Process Completion Event"
        verbose_name_plural = "Process Completion Events"
        db_table = "orca_process_completion_events"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.issue_id} closed by {self.source}"
