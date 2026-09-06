# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What an outside system asked for, and which work item it got.

Two tables, both sidecar (FORK.md), both there because the public automation
API talks to callers that retry.

``ExternalWorkItemBinding`` is the identity map: an external system knows its
own key for a piece of work, and this is what turns that key back into the same
``Issue`` every time. Without it a webhook delivered twice creates two work
items and nobody can tell they are the same thing.

``AutomationOperation`` is the receipt: one row per ``Idempotency-Key``,
carrying the hash of the payload it was created for and the response that was
sent back. A retry finds the row and replays the recorded answer instead of
allocating the work a second time. It is also what makes a *changed* payload
under a reused key detectable, which is the failure the caller most needs to
hear about — silently treating it as a replay would drop the new request on the
floor.

Neither table is soft-deleted in practice: a receipt that can disappear is not
a receipt. The binding keeps ``deleted_at`` in its uniqueness conditions
because it inherits ``BaseModel`` and a workspace-level cleanup could still
retire one; the operation's uniqueness is unconditional, so a key is spent for
good.

See docs/orca-work-management-rfc.md §5.2, §6.7 and §7.
"""

# Django imports
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel


class AutomationOperationType(models.TextChoices):
    """Which mutation an operation stands for."""

    CREATE_WORK_ITEM = "create_work_item", "Create work item"
    REASSIGN = "reassign", "Reassign"
    TRANSFER_UNIT = "transfer_unit", "Transfer unit"
    COMPLETE = "complete", "Complete"


class AutomationOperationStatus(models.TextChoices):
    """
    Where an operation got to.

    @description ``in_progress`` is not only a transient state: a row left in it
    is how a crash mid-operation becomes visible. Section 6.7 treats one older
    than sixty seconds as abandoned and lets the next caller take it over,
    which is why the state has to be recorded rather than inferred.
    """

    IN_PROGRESS = "in_progress", "In progress"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class ExternalWorkItemBinding(BaseModel):
    """
    The link between an outside system's key and one work item.

    @description Two uniqueness rules, and they are not the same rule twice.
    ``(workspace, external_source, external_id)`` is what makes a redelivered
    event find the work item it already created. ``(issue)`` is what stops two
    external keys from claiming the same work item, which would make
    "which external thing is this?" unanswerable.

    Attributes:
        workspace (Workspace): Denormalized, as in every Orca table, so a
            uniqueness rule can be scoped to a workspace without a join.
        external_source (str): The calling system, e.g. ``espo-onboarding``.
        external_id (str): That system's key for this piece of work.
        issue (Issue): The work item the key resolves to.
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="orca_external_bindings",
    )
    external_source = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255)
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="orca_external_bindings",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "external_source", "external_id"],
                condition=Q(deleted_at__isnull=True),
                name="orca_binding_unique_external_key",
            ),
            models.UniqueConstraint(
                fields=["issue"],
                condition=Q(deleted_at__isnull=True),
                name="orca_binding_unique_issue",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "external_source"], name="orca_binding_source_idx")]
        verbose_name = "External Work Item Binding"
        verbose_name_plural = "External Work Item Bindings"
        db_table = "orca_external_work_item_bindings"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        if not self.workspace_id and self.issue_id:
            self.workspace_id = self.issue.workspace_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.external_source}:{self.external_id} -> {self.issue_id}"


class AutomationOperation(BaseModel):
    """
    One ``Idempotency-Key``, and the answer it is entitled to.

    @description ``request_hash`` is what separates a retry from a different
    request wearing the same key. A caller that reuses a key with a changed
    payload is not retrying — it has a bug, or two events collided on one key —
    and section 6.7 answers it with a conflict rather than the old response.

    ``response_snapshot`` is deliberately the whole body that was sent, not a
    pointer to current state. A replay must answer what the first call
    answered: if a person reassigned the item in between, the replay still
    reports the original allocation, and a caller that wants today's state
    asks for it with a ``GET``. Anything else would let a retry appear to
    undo somebody's decision.

    Attributes:
        api_token (APIToken): Who called. Null once a token is deleted — the
            receipt outlives the credential.
        idempotency_key (str): The caller's key, unique per workspace.
        request_hash (str): SHA-256 of the canonical payload, 64 hex chars.
        issue (Issue): The work item the operation ended up touching, if any.
        response_snapshot (dict): The body returned to the caller.
        error_code (str): Symbolic Orca error name when ``status`` is failed.
        completed_at (datetime): When it stopped being in progress.
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="orca_automation_operations",
    )
    api_token = models.ForeignKey(
        "db.APIToken",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_automation_operations",
    )
    idempotency_key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    operation_type = models.CharField(max_length=20, choices=AutomationOperationType.choices)
    status = models.CharField(
        max_length=12,
        choices=AutomationOperationStatus.choices,
        default=AutomationOperationStatus.IN_PROGRESS,
    )
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_automation_operations",
    )
    response_snapshot = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # No `deleted_at` condition, unlike the binding above and unlike
            # every other Orca table. A spent idempotency key must stay spent:
            # if soft-deleting a receipt freed the key, a replay arriving after
            # the cleanup would execute the operation a second time, which is
            # the one thing this table exists to prevent.
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"],
                name="orca_operation_unique_idempotency_key",
            )
        ]
        indexes = [models.Index(fields=["workspace", "status", "created_at"], name="orca_operation_status_idx")]
        verbose_name = "Automation Operation"
        verbose_name_plural = "Automation Operations"
        db_table = "orca_automation_operations"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.operation_type}:{self.idempotency_key} ({self.status})"
