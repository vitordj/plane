# Availability, and what one person will accept from one area (Phase 3).
#
# Two tables, two different questions. ``WorkspaceMemberAvailability`` is about
# a person everywhere — a window rather than a boolean, because the one thing
# everybody forgets after a holiday is the toggle they set before it, and a
# window closes itself. ``MembershipAllocationSettings`` is about a person in
# one area: somebody can be perfectly available and still not be taking new
# work from one of their three areas, and saying that per membership is the
# only way not to say the wrong thing about the other two.
#
# The CHECK on the window is the whole of its validation: a window that ends
# before it starts covers nothing and would read as "available" everywhere,
# which is the opposite of what whoever typed it meant. Overlaps are allowed
# on purpose — two systems recording one absence is normal, and the question
# the ranking asks is whether *any* window covers now.
#
# The third operation is the ranking's version default moving to ``lb-2``.
# ``lb-2`` is ``lb-1`` plus the three exclusions these tables make possible,
# and a row defaulting to ``lb-1`` while the service runs ``lb-2`` would be a
# lie nobody wrote on purpose. Existing rows keep the version they recorded.
#
# Generated with `manage.py makemigrations db --name orca_availability` and
# verified with `makemigrations --check --dry-run` plus an apply/reverse round
# trip on an empty database.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0139_orca_unit_coordinator"),
    ]

    operations = [
        migrations.AlterField(
            model_name="assignmentdecision",
            name="algorithm_version",
            field=models.CharField(default="lb-2", max_length=16),
        ),
        migrations.CreateModel(
            name="MembershipAllocationSettings",
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
                    "membership",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="allocation_settings",
                        to="db.organizationalunitmembership",
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
            ],
            options={
                "verbose_name": "Membership Allocation Settings",
                "verbose_name_plural": "Membership Allocation Settings",
                "db_table": "orca_membership_allocation_settings",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="WorkspaceMemberAvailability",
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
                ("unavailable_from", models.DateTimeField()),
                ("unavailable_until", models.DateTimeField(blank=True, null=True)),
                (
                    "reason",
                    models.CharField(
                        choices=[
                            ("vacation", "Vacation"),
                            ("leave", "Leave"),
                            ("other", "Other"),
                        ],
                        default="vacation",
                        max_length=16,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("hr", "HR system"),
                            ("directory", "Directory"),
                        ],
                        default="manual",
                        max_length=16,
                    ),
                ),
                (
                    "external_id",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=255
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
                "db_table": "orca_workspace_member_availability",
                "ordering": ("-unavailable_from",),
                "indexes": [
                    models.Index(
                        fields=[
                            "workspace_member",
                            "unavailable_from",
                            "unavailable_until",
                        ],
                        name="orca_availability_window_idx",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("unavailable_until__isnull", True),
                            ("unavailable_until__gt", models.F("unavailable_from")),
                            _connector="OR",
                        ),
                        name="orca_availability_until_after_from",
                    )
                ],
            },
        ),
    ]
