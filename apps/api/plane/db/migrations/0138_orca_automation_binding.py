# The public API's memory: which work item belongs to which external record,
# and the receipt for every mutation that carries an Idempotency-Key.
# Hand-written (no database in the agent session) — run
# `python3 manage.py makemigrations --check` before merging.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def audit_fields():
    return [
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
    ]


def audit_user_fields(model_name):
    return [
        (
            "created_by",
            models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name=f"{model_name}_created_by",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Created By",
            ),
        ),
        (
            "updated_by",
            models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name=f"{model_name}_updated_by",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Last Modified By",
            ),
        ),
    ]


OPERATION_TYPES = [
    ("create_work_item", "Create work item"),
    ("reassign", "Reassign"),
    ("transfer_unit", "Transfer area"),
    ("complete", "Complete"),
]

OPERATION_STATUSES = [
    ("in_progress", "In progress"),
    ("succeeded", "Succeeded"),
    ("failed", "Failed"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0137_orca_assignment_decision"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalWorkItemBinding",
            fields=audit_fields()
            + [
                ("external_source", models.CharField(max_length=255)),
                ("external_id", models.CharField(max_length=255)),
            ]
            + audit_user_fields("externalworkitembinding")
            + [
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
        migrations.CreateModel(
            name="AutomationOperation",
            fields=audit_fields()
            + [
                ("idempotency_key", models.CharField(max_length=255)),
                ("request_hash", models.CharField(max_length=64)),
                ("operation_type", models.CharField(choices=OPERATION_TYPES, max_length=24)),
                ("status", models.CharField(choices=OPERATION_STATUSES, default="in_progress", max_length=16)),
                ("response_snapshot", models.JSONField(blank=True, default=dict)),
                ("error_code", models.CharField(blank=True, default="", max_length=64)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ]
            + audit_user_fields("automationoperation")
            + [
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
        migrations.AddConstraint(
            model_name="externalworkitembinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("workspace", "external_source", "external_id"),
                name="orca_binding_unique_external_ref",
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
        migrations.AddIndex(
            model_name="automationoperation",
            index=models.Index(fields=["workspace", "status", "created_at"], name="orca_operation_status_idx"),
        ),
    ]
