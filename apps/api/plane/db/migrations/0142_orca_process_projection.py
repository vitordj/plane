# Processes projected into Plane (Phase 4): the service level a work item
# carries, the run of a process it belongs to, and the record of a step closed
# by something other than a person. Templates stay outside the product.
# Hand-written; run `python3 manage.py makemigrations --check` before merging.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def audit_fields():
    """
    Fresh field instances for the columns every ``BaseModel`` carries.

    @description A function rather than a module-level list: a Django field
    instance is bound to the model it is added to, so sharing one list across
    four ``CreateModel`` operations would hand the same objects to four models.
    """
    return [
        ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
        ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
        (
            "id",
            models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True),
        ),
        ("deleted_at", models.DateTimeField(blank=True, null=True)),
    ]


def audit_relations():
    return [
        (
            "created_by",
            models.ForeignKey(
                blank=True,
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
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="%(class)s_updated_by",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Last Modified By",
            ),
        ),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0141_orca_availability"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationalunitassignmentpolicy",
            name="completed_state",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="orca_completing_policies",
                to="db.state",
            ),
        ),
        migrations.AddField(
            model_name="organizationalunitassignmentpolicy",
            name="review_state",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="orca_reviewing_policies",
                to="db.state",
            ),
        ),
        migrations.CreateModel(
            name="IssueServiceLevel",
            fields=audit_fields()
            + [
                ("assignment_due_at", models.DateTimeField(blank=True, null=True)),
                ("completion_due_at", models.DateTimeField(blank=True, null=True)),
                ("original_assignment_due_at", models.DateTimeField(blank=True, null=True)),
                ("original_completion_due_at", models.DateTimeField(blank=True, null=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("unit_project", "The area's policy for this project"),
                            ("unit", "The area's own policy"),
                            ("process", "The process template"),
                            ("manual", "Somebody set it by hand"),
                        ],
                        default="unit",
                        max_length=16,
                    ),
                ),
                ("source_version", models.CharField(blank=True, default="", max_length=64)),
                ("change_reason", models.TextField(blank=True, default="")),
            ]
            + audit_relations()
            + [
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_service_level_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "issue",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_service_level",
                        to="db.issue",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_service_levels",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Issue Service Level",
                "verbose_name_plural": "Issue Service Levels",
                "db_table": "orca_issue_service_levels",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ProcessInstanceReference",
            fields=audit_fields()
            + [
                ("external_source", models.CharField(max_length=255)),
                ("external_instance_id", models.CharField(max_length=255)),
                ("template_name", models.CharField(max_length=255)),
                ("template_version", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="running",
                        max_length=16,
                    ),
                ),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ]
            + audit_relations()
            + [
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_process_instances",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Process Instance Reference",
                "verbose_name_plural": "Process Instance References",
                "db_table": "orca_process_instances",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ProcessInstanceItem",
            fields=audit_fields()
            + [
                ("step_key", models.CharField(max_length=255)),
                (
                    "completion_mode",
                    models.CharField(
                        choices=[
                            ("automatic", "The system closes it"),
                            ("automatic_with_review", "The system flags it for review"),
                            ("manual", "Only a person closes it"),
                        ],
                        default="manual",
                        max_length=24,
                    ),
                ),
            ]
            + audit_relations()
            + [
                (
                    "issue",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_process_item",
                        to="db.issue",
                    ),
                ),
                (
                    "process_instance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="db.processinstancereference",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_process_items",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Process Instance Item",
                "verbose_name_plural": "Process Instance Items",
                "db_table": "orca_process_instance_items",
                "ordering": ("created_at",),
            },
        ),
        migrations.CreateModel(
            name="ProcessCompletionEvent",
            fields=audit_fields()
            + [
                ("source", models.CharField(max_length=255)),
                ("event_id", models.CharField(blank=True, default="", max_length=255)),
                ("rule_version", models.CharField(blank=True, default="", max_length=64)),
                ("evidence", models.JSONField(default=dict)),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("automatic", "The system closes it"),
                            ("automatic_with_review", "The system flags it for review"),
                            ("manual", "Only a person closes it"),
                        ],
                        max_length=24,
                    ),
                ),
            ]
            + audit_relations()
            + [
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_completion_events",
                        to="db.issue",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_completion_events",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Process Completion Event",
                "verbose_name_plural": "Process Completion Events",
                "db_table": "orca_process_completion_events",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="issueservicelevel",
            index=models.Index(fields=["workspace", "completion_due_at"], name="orca_sla_completion_idx"),
        ),
        migrations.AddIndex(
            model_name="processinstancereference",
            index=models.Index(fields=["workspace", "status"], name="orca_process_status_idx"),
        ),
        migrations.AddIndex(
            model_name="processcompletionevent",
            index=models.Index(fields=["issue", "created_at"], name="orca_completion_issue_idx"),
        ),
        migrations.AddConstraint(
            model_name="processinstancereference",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("workspace", "external_source", "external_instance_id"),
                name="orca_process_instance_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="processinstanceitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("issue",),
                name="orca_process_item_unique_issue",
            ),
        ),
        migrations.AddConstraint(
            model_name="processinstanceitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("process_instance", "step_key"),
                name="orca_process_item_unique_step",
            ),
        ),
    ]
