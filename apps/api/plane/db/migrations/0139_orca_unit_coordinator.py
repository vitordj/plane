# The coordinator: who runs an area's queue, and the access that job needs.
#
# Four things, one migration, because they are one change. The coordination
# itself (``OrganizationalUnitCoordinator``); a second origin for the
# provenance ledger, since a coordinator's access hangs from the coordination
# and not from a membership they may not have — hence ``grant_source``, the
# nullable ``membership`` and the CHECK that keeps exactly one of the two set;
# ``IssueOrganizationalUnit.last_alerted_at``, which the assignment-SLA sweep
# (item 2.4) reads to alert once per breach instead of once per run; and the
# ``suspended`` outcome, which is the coordinator parking an item that is
# blocked outside the area (RFC §6.2) and had no way to be recorded.
#
# Plan item 2.1 called this migration 0140 and Phase 3's 0139. The order came
# out the other way round because Phase 2 lands first, and the number is a
# position in a sequence, not a name.
#
# Generated with `manage.py makemigrations db --name orca_unit_coordinator`
# and verified with `makemigrations --check --dry-run` plus an apply/reverse
# round trip on an empty database.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0138_orca_automation_binding"),
    ]

    operations = [
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="last_alerted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
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
            model_name="assignmentdecision",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("assigned", "Assigned"),
                    ("queued", "Queued"),
                    ("allocation_failed", "Allocation failed"),
                    ("suspended", "Suspended"),
                    ("rejected", "Rejected"),
                ],
                max_length=20,
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
        migrations.CreateModel(
            name="OrganizationalUnitCoordinator",
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
                    "organizational_unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="coordinators",
                        to="db.organizationalunit",
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
                        related_name="organizational_unit_coordinators",
                        to="db.workspace",
                    ),
                ),
                (
                    "workspace_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="organizational_unit_coordinations",
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
                condition=models.Q(
                    ("deleted_at__isnull", True), ("coordinator__isnull", False)
                ),
                fields=("coordinator", "unit_project"),
                name="org_unit_grant_unique_coordinator_unit_project",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationalunitgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("coordinator__isnull", True), ("membership__isnull", False)
                    ),
                    models.Q(
                        ("coordinator__isnull", False), ("membership__isnull", True)
                    ),
                    _connector="OR",
                ),
                name="org_unit_grant_exactly_one_origin",
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
