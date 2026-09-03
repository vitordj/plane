# Coordinators — the people who run an area's queue — and the provenance the
# access ledger needs to withdraw only what coordination gave.
# Hand-written; run `python3 manage.py makemigrations --check` before merging.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0138_orca_automation_binding"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OrganizationalUnitCoordinator",
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
                ("is_active", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="organizationalunitcoordinator_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="organizationalunitcoordinator_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "organizational_unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="coordinators",
                        to="db.organizationalunit",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_unit_coordinators",
                        to="db.workspace",
                    ),
                ),
                (
                    "workspace_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_coordinations",
                        to="db.workspacemember",
                    ),
                ),
            ],
            options={
                "verbose_name": "Organizational Unit Coordinator",
                "verbose_name_plural": "Organizational Unit Coordinators",
                "db_table": "organizational_unit_coordinators",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="organizationalunitcoordinator",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("organizational_unit", "workspace_member"),
                name="orca_unit_coordinator_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="organizationalunitcoordinator",
            index=models.Index(fields=["workspace", "is_active"], name="orca_coordinator_workspace_idx"),
        ),
        # Existing grants all came from membership, which is what the default
        # records; nothing needs backfilling.
        migrations.AddField(
            model_name="organizationalunitgrant",
            name="grant_source",
            field=models.CharField(
                choices=[("membership", "Membership"), ("coordinator", "Coordination")],
                default="membership",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="organizationalunitgrant",
            name="coordinator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="grants",
                to="db.organizationalunitcoordinator",
            ),
        ),
        migrations.AlterField(
            model_name="organizationalunitgrant",
            name="membership",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="grants",
                to="db.organizationalunitmembership",
            ),
        ),
    ]
