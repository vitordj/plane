# How an area assigns work: one default policy per area, optionally overridden
# per project. Hand-written (the agent session has no database) — run
# `python3 manage.py makemigrations --check` before merging.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0135_orca_issue_routing_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationalUnitAssignmentPolicy",
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
                (
                    "default_mode",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("self_claim", "Self claim"),
                            ("least_loaded", "Least loaded"),
                        ],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("allowed_modes", models.JSONField(default=list)),
                ("assignment_sla_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("max_open_items_per_member", models.PositiveIntegerField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="organizationalunitassignmentpolicy_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="organizationalunitassignmentpolicy_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "organizational_unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignment_policies",
                        to="db.organizationalunit",
                    ),
                ),
                (
                    "unit_project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignment_policies",
                        to="db.organizationalunitproject",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignment_policies",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Assignment Policy",
                "verbose_name_plural": "Assignment Policies",
                "db_table": "orca_assignment_policies",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="organizationalunitassignmentpolicy",
            constraint=models.UniqueConstraint(
                condition=models.Q(("unit_project__isnull", True), ("deleted_at__isnull", True)),
                fields=("organizational_unit",),
                name="orca_policy_one_default_per_unit",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationalunitassignmentpolicy",
            constraint=models.UniqueConstraint(
                condition=models.Q(("unit_project__isnull", False), ("deleted_at__isnull", True)),
                fields=("organizational_unit", "unit_project"),
                name="orca_policy_one_per_unit_project",
            ),
        ),
    ]
