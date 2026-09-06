# The public automation API's two tables: the identity map that turns an
# outside system's key back into the same work item, and the receipt that lets
# a retry replay its original answer instead of doing the work twice.
#
# Also adds AssignmentDecision.automation_operation, which 0137 deliberately
# left out: the FK could not point at a table that did not exist yet (D0.4
# records the deferral). Adding it here closes the RFC §5.2 shape.
#
# Written by hand (the agent session has no database); confirm with
# `python3 apps/api/manage.py makemigrations --check --dry-run`.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0137_orca_assignment_decision"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AutomationOperation",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=255)),
                ("request_hash", models.CharField(max_length=64)),
                (
                    "operation_type",
                    models.CharField(
                        choices=[
                            ("create_work_item", "Create work item"),
                            ("reassign", "Reassign"),
                            ("transfer_unit", "Transfer unit"),
                            ("complete", "Complete"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("in_progress", "In progress"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="in_progress",
                        max_length=12,
                    ),
                ),
                ("response_snapshot", models.JSONField(blank=True, default=dict)),
                ("error_code", models.CharField(blank=True, default="", max_length=64)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "api_token",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_automation_operations",
                        to="db.apitoken",
                    ),
                ),
                (
                    "issue",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_automation_operations",
                        to="db.issue",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_automation_operations",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Automation Operation",
                "verbose_name_plural": "Automation Operations",
                "db_table": "orca_automation_operations",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ExternalWorkItemBinding",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("external_source", models.CharField(max_length=255)),
                ("external_id", models.CharField(max_length=255)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_external_bindings",
                        to="db.issue",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_external_bindings",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "External Work Item Binding",
                "verbose_name_plural": "External Work Item Bindings",
                "db_table": "orca_external_work_item_bindings",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddField(
            model_name="assignmentdecision",
            name="automation_operation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assignment_decisions",
                to="db.automationoperation",
            ),
        ),
        migrations.AddIndex(
            model_name="automationoperation",
            index=models.Index(fields=["workspace", "status", "created_at"], name="orca_operation_status_idx"),
        ),
        migrations.AddIndex(
            model_name="externalworkitembinding",
            index=models.Index(fields=["workspace", "external_source"], name="orca_binding_source_idx"),
        ),
        migrations.AddConstraint(
            model_name="automationoperation",
            constraint=models.UniqueConstraint(
                fields=("workspace", "idempotency_key"),
                name="orca_operation_unique_idempotency_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="externalworkitembinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("workspace", "external_source", "external_id"),
                name="orca_binding_unique_external_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="externalworkitembinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("issue",),
                name="orca_binding_unique_issue",
            ),
        ),
    ]
