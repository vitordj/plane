# The coordinator of an area, and where a grant comes from.
#
# Coordination is its own table rather than a third unit-member role because a
# coordinator need not belong to the area they answer for (RFC §5.2). That one
# fact is what forces the rest of this migration: the provenance ledger keyed
# every grant by a mandatory membership, and a coordinator who is not a member
# has no membership row for such a grant to point at. So ``membership`` becomes
# nullable, ``coordinator`` joins it, and two CHECKs keep exactly one of them
# filled and in step with the new ``grant_source`` label.
#
# ``IssueOrganizationalUnit.last_alerted_at`` rides along (item 2.4) so the
# phase ships one migration rather than two touching neighbouring tables.
#
# Numbered 0139, not the 0140 the RFC text says: 0139 was free and Django binds
# migrations by dependency, not by number. Availability (Phase 3) moves to 0140.
#
# Written by hand (the agent session has no database); confirm with
# `python3 apps/api/manage.py makemigrations --check --dry-run`.

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
                        related_name="coordinators",
                        to="db.organizationalunit",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="organizational_unit_coordinators",
                        to="db.workspace",
                    ),
                ),
                (
                    "workspace_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="coordinated_units",
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
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="last_alerted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Existing rows are all membership grants, so the default backfills them
        # correctly and the CHECK below holds from the moment it is added.
        migrations.AddField(
            model_name="organizationalunitgrant",
            name="grant_source",
            field=models.CharField(
                choices=[("membership", "Membership"), ("coordinator", "Coordinator")],
                default="membership",
                max_length=16,
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
        migrations.AddConstraint(
            model_name="organizationalunitgrant",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("coordinator", "unit_project"),
                name="org_unit_grant_unique_coordinator_unit_project_when_deleted_at_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationalunitgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("coordinator__isnull", True), ("membership__isnull", False)),
                    models.Q(("coordinator__isnull", False), ("membership__isnull", True)),
                    _connector="OR",
                ),
                name="org_unit_grant_exactly_one_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationalunitgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("grant_source", "membership"), ("membership__isnull", False)),
                    models.Q(("coordinator__isnull", False), ("grant_source", "coordinator")),
                    _connector="OR",
                ),
                name="org_unit_grant_source_matches_relation",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationalunitcoordinator",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("organizational_unit", "workspace_member"),
                name="org_unit_coordinator_unique_unit_member_when_deleted_at_null",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="organizationalunitcoordinator",
            unique_together={("organizational_unit", "workspace_member", "deleted_at")},
        ),
    ]
