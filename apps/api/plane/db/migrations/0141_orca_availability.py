# Availability and per-membership allocation limits (Phase 3). Two tables and
# nothing else: being away keeps the automatic ranking from picking somebody,
# it never withdraws access and never moves work on its own.
# Hand-written; run `python3 manage.py makemigrations --check` before merging.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0140_orca_queue_alerts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceMemberAvailability",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
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
                        choices=[("manual", "Entered by hand"), ("hr", "HR system"), ("directory", "Directory")],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("external_id", models.CharField(blank=True, default="", max_length=255)),
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
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_member_availability",
                        to="db.workspace",
                    ),
                ),
                (
                    "workspace_member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_availability",
                        to="db.workspacemember",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workspace Member Availability",
                "verbose_name_plural": "Workspace Member Availability",
                "db_table": "orca_member_availability",
                "ordering": ("-unavailable_from",),
            },
        ),
        migrations.CreateModel(
            name="MembershipAllocationSettings",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("accepts_new_work", models.BooleanField(default=True)),
                ("max_open_items", models.PositiveIntegerField(blank=True, null=True)),
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
                (
                    "membership",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="allocation_settings",
                        to="db.organizationalunitmembership",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_allocation_settings",
                        to="db.workspace",
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
        migrations.AddIndex(
            model_name="workspacememberavailability",
            index=models.Index(fields=["workspace_member", "unavailable_from"], name="orca_availability_member_idx"),
        ),
        migrations.AddConstraint(
            model_name="workspacememberavailability",
            constraint=models.CheckConstraint(
                condition=models.Q(("unavailable_until__isnull", True))
                | models.Q(("unavailable_until__gt", models.F("unavailable_from"))),
                name="orca_availability_interval_ordered",
            ),
        ),
        migrations.AddConstraint(
            model_name="membershipallocationsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(("max_open_items__isnull", True)) | models.Q(("max_open_items__gt", 0)),
                name="orca_allocation_max_open_positive",
            ),
        ),
    ]
