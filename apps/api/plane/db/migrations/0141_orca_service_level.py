# Processes, projected into Plane just far enough to be readable (Phase 4).
#
# Four tables and two policy fields, and they are one change: the deadlines a
# work item is held to (``IssueServiceLevel``, with the ``original_*`` pair
# that makes "we always deliver in four hours" falsifiable), the run of a
# process and its steps (``ProcessInstanceReference``/``ProcessInstanceItem``),
# the append-only record of a step being claimed finished
# (``ProcessCompletionEvent``), and the two states a policy may name for a
# completed step and one waiting for review.
#
# The plan called for 0141 *and* 0142. One migration, because it is one idea:
# splitting the tables from the policy fields would produce a first migration
# whose only effect is a table nothing writes yet.
#
# What is deliberately absent: the template, its steps, its branching, its
# schedule. Modelling those would make Plane a workflow engine, which F12
# decided it is not, and would leave two definitions of one process to drift.
#
# Generated with `manage.py makemigrations db --name orca_service_level` and
# verified with `makemigrations --check --dry-run` plus an apply/reverse round
# trip on an empty database.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0140_orca_availability"),
    ]

    operations = [
        migrations.AddField(
            model_name="organizationalunitassignmentpolicy",
            name="completed_state",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="orca_policies_completing_here",
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
                related_name="orca_policies_reviewing_here",
                to="db.state",
            ),
        ),
        migrations.CreateModel(
            name="IssueServiceLevel",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Last Modified At"
                    ),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Deleted At"
                    ),
                ),
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
                ("assignment_due_at", models.DateTimeField(blank=True, null=True)),
                ("completion_due_at", models.DateTimeField(blank=True, null=True)),
                (
                    "original_assignment_due_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "original_completion_due_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("unit_project", "Area policy for this project"),
                            ("unit", "Area policy"),
                            ("process", "Process template"),
                            ("manual", "Set by hand"),
                        ],
                        default="unit",
                        max_length=16,
                    ),
                ),
                (
                    "source_version",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                ("change_reason", models.TextField(blank=True, default="")),
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
                    "issue",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_service_level",
                        to="db.issue",
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
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Last Modified At"
                    ),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Deleted At"
                    ),
                ),
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
                "db_table": "orca_process_instance_references",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ProcessInstanceItem",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Last Modified At"
                    ),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Deleted At"
                    ),
                ),
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
                ("step_key", models.CharField(max_length=255)),
                (
                    "completion_mode",
                    models.CharField(
                        choices=[
                            ("automatic", "Automatic"),
                            ("automatic_with_review", "Automatic, with review"),
                            ("manual", "Manual"),
                        ],
                        default="manual",
                        max_length=24,
                    ),
                ),
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
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_process_items",
                        to="db.issue",
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
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_process_items",
                        to="db.workspace",
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
            ],
            options={
                "verbose_name": "Process Instance Item",
                "verbose_name_plural": "Process Instance Items",
                "db_table": "orca_process_instance_items",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ProcessCompletionEvent",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Last Modified At"
                    ),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Deleted At"
                    ),
                ),
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
                ("source", models.CharField(blank=True, default="", max_length=255)),
                ("event_id", models.CharField(blank=True, default="", max_length=255)),
                (
                    "rule_version",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("evidence", models.JSONField(blank=True, default=dict)),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("automatic", "Automatic"),
                            ("automatic_with_review", "Automatic, with review"),
                            ("manual", "Manual"),
                        ],
                        default="manual",
                        max_length=24,
                    ),
                ),
                ("applied", models.BooleanField(default=False)),
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
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_completion_events",
                        to="db.issue",
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
                "indexes": [
                    models.Index(
                        fields=["issue", "created_at"], name="orca_completion_issue_idx"
                    ),
                    models.Index(
                        fields=["workspace", "created_at"],
                        name="orca_completion_workspace_idx",
                    ),
                ],
            },
        ),
        migrations.AddIndex(
            model_name="processinstancereference",
            index=models.Index(
                fields=["workspace", "status"], name="orca_process_status_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="processinstancereference",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("workspace", "external_source", "external_instance_id"),
                name="orca_process_instance_unique_external",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="processinstancereference",
            unique_together={
                ("workspace", "external_source", "external_instance_id", "deleted_at")
            },
        ),
        migrations.AddConstraint(
            model_name="processinstanceitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("issue",),
                name="orca_process_item_unique_issue",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="processinstanceitem",
            unique_together={("issue", "deleted_at")},
        ),
    ]
