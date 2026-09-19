# Leave, vacation, and "I am not taking more from this area".
#
# WorkspaceMemberAvailability is a window on a workspace member: half-open
# [from, until), with a null until meaning open-ended. MembershipAllocationSettings
# is the per-membership opt-out / personal cap. Ranking (item 3.2) is what
# actually reads them; this migration only creates the tables and the CHECK
# that a closed window has to run forwards (RFC §5.2).
#
# Numbered 0141. 0140 was claimed by R1.A12 (idempotency scoped to token);
# Django binds migrations by dependency, not by number. Phase 4's IssueServiceLevel
# moves to 0142.
#
# Written by hand (the agent session has no database); confirm with
# `python3 apps/api/manage.py makemigrations --check --dry-run`.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0140_orca_idempotency_scoped_to_token"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceMemberAvailability",
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
                ("unavailable_from", models.DateTimeField()),
                ("unavailable_until", models.DateTimeField(blank=True, null=True)),
                (
                    "reason",
                    models.CharField(
                        choices=[("vacation", "Vacation"), ("leave", "Leave"), ("other", "Other")],
                        default="other",
                        max_length=16,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[("manual", "Manual"), ("hr", "HR"), ("directory", "Directory")],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("external_id", models.CharField(blank=True, default="", max_length=255)),
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
                        related_name="orca_availability_windows",
                        to="db.workspace",
                    ),
                ),
                (
                    "workspace_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_availability_windows",
                        to="db.workspacemember",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workspace Member Availability",
                "verbose_name_plural": "Workspace Member Availabilities",
                "db_table": "orca_workspace_member_availability",
                "ordering": ("-unavailable_from",),
            },
        ),
        migrations.CreateModel(
            name="MembershipAllocationSettings",
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
                ("accepts_new_work", models.BooleanField(default=True)),
                ("max_open_items", models.PositiveIntegerField(blank=True, null=True)),
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
                    "membership",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="allocation_settings",
                        to="db.organizationalunitmembership",
                    ),
                ),
            ],
            options={
                "verbose_name": "Membership Allocation Settings",
                "verbose_name_plural": "Membership Allocation Settings",
                "db_table": "orca_membership_allocation_settings",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="workspacememberavailability",
            constraint=models.CheckConstraint(
                condition=models.Q(("unavailable_until__isnull", True))
                | models.Q(("unavailable_until__gt", models.F("unavailable_from"))),
                name="orca_availability_until_after_from",
            ),
        ),
        migrations.AddIndex(
            model_name="workspacememberavailability",
            index=models.Index(
                fields=["workspace_member", "unavailable_from"],
                name="orca_avail_member_from_idx",
            ),
        ),
    ]
