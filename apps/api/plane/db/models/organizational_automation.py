# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
What lets a robot call the API twice and not do the work twice.

Two tables, both of them about identity rather than behaviour:

``ExternalWorkItemBinding`` is the link between a work item here and the thing
that caused it over there — a CRM record, a step of an onboarding. Without it,
a system that retries after a timeout has no way to ask "did my last call
land?", and the honest answer to a retried creation is a duplicate.

``AutomationOperation`` is the receipt for one call. Every mutation in the
public API carries an ``Idempotency-Key``, and the first call to use a key
writes the receipt with a hash of what it was asked to do and, at the end, the
exact response. A repeat of the same key with the same payload gets that
response back instead of doing anything; the same key with a *different*
payload is a bug in the caller and is refused.
"""

# Django imports
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel


class AutomationOperationType(models.TextChoices):
    """Which mutation a receipt belongs to."""

    CREATE_WORK_ITEM = "create_work_item", "Create work item"
    REASSIGN = "reassign", "Reassign"
    TRANSFER_UNIT = "transfer_unit", "Transfer area"
    COMPLETE = "complete", "Complete"


class AutomationOperationStatus(models.TextChoices):
    """
    How far a call got.

    @description ``FAILED`` is deliberately a resting state with a snapshot:
    a caller that retries a request which failed for a reason of its own —
    a work item in a project the token cannot write to — should get the same
    refusal, not a fresh attempt each time.
    """

    IN_PROGRESS = "in_progress", "In progress"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class ExternalWorkItemBinding(BaseModel):
    """
    The work item that belongs to one external record.

    @description One binding per external record, one binding per work item —
    both enforced, because either of them being loose is how a retry becomes a
    duplicate. The service also fills Plane's native ``external_source`` and
    ``external_id`` on the work item, so the API's own search keeps working.
    """

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_external_bindings")
    external_source = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255)
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="orca_external_bindings")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "external_source", "external_id"],
                condition=Q(deleted_at__isnull=True),
                name="orca_binding_unique_external_ref",
            ),
            models.UniqueConstraint(
                fields=["issue"],
                condition=Q(deleted_at__isnull=True),
                name="orca_binding_unique_issue",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "external_source"], name="orca_binding_source_idx"),
        ]
        verbose_name = "External Work Item Binding"
        verbose_name_plural = "External Work Item Bindings"
        db_table = "orca_external_work_item_bindings"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.external_source}:{self.external_id} -> {self.issue_id}"


class AutomationOperation(BaseModel):
    """
    The receipt for one call to the public API.

    @description The unique constraint on ``(workspace, idempotency_key)``
    carries no ``deleted_at`` condition, unlike everything else in this layer:
    it is what makes two simultaneous calls with the same key race for a row
    instead of both proceeding, and a partial index would leave that race open
    for any key whose earlier receipt was soft-deleted.
    """

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="orca_automation_operations")
    api_token = models.ForeignKey(
        "db.APIToken",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orca_automation_operations",
    )
    idempotency_key = models.CharField(max_length=255)
    # SHA-256 of the canonical payload: same request, same hash, whatever order
    # the caller's JSON serializer happened to use.
    request_hash = models.CharField(max_length=64)
    operation_type = models.CharField(max_length=24, choices=AutomationOperationType.choices)
    status = models.CharField(
        max_length=16,
        choices=AutomationOperationStatus.choices,
        default=AutomationOperationStatus.IN_PROGRESS,
    )
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.SET_NULL, null=True, blank=True, related_name="orca_automation_operations"
    )
    # Exactly what was sent back, so a replay answers identically without
    # re-deciding anything.
    response_snapshot = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "idempotency_key"],
                name="orca_operation_unique_idempotency_key",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status", "created_at"], name="orca_operation_status_idx"),
        ]
        verbose_name = "Automation Operation"
        verbose_name_plural = "Automation Operations"
        db_table = "orca_automation_operations"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.idempotency_key} ({self.status})"
