# How an area hands work out: default mode, allowed modes, SLA and load cap,
# either for the area as a whole or overridden for one of its projects.
#
# Two partial unique constraints rather than one over (unit, unit_project):
# Postgres treats NULLs as distinct, so a single constraint would let an area
# collect any number of "default" policies. See
# docs/orca-work-management-rfc.md §5.2.
#
# Written by hand (the agent session has no database); confirm with
# `python3 apps/api/manage.py makemigrations --check --dry-run`.

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
                        related_name="organizational_assignment_policies",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Organizational Unit Assignment Policy",
                "verbose_name_plural": "Organizational Unit Assignment Policies",
                "db_table": "organizational_unit_assignment_policies",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="organizationalunitassignmentpolicy",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), ("unit_project__isnull", True)),
                fields=("organizational_unit",),
                name="orca_policy_unique_unit_default",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationalunitassignmentpolicy",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), ("unit_project__isnull", False)),
                fields=("organizational_unit", "unit_project"),
                name="orca_policy_unique_unit_project",
            ),
        ),
    ]
