# Directory (SCIM) sidecar tables for the Orca organizational layer, plus the
# provenance columns that let a directory sync coexist with manual edits.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0131_issue_organizational_unit"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- Provenance on the existing organizational layer ---------------
        migrations.AddField(
            model_name="organizationalunit",
            name="sync_source",
            field=models.CharField(
                choices=[("manual", "Manual"), ("scim", "SCIM")],
                default="manual",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="organizationalunit",
            name="external_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="organizationalunit",
            name="directory_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="organizationalunitmembership",
            name="sync_source",
            field=models.CharField(
                choices=[("manual", "Manual"), ("scim", "SCIM")],
                default="manual",
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationalunit",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), models.Q(("external_id", ""), _negated=True)),
                fields=("workspace", "external_id"),
                name="org_unit_unique_workspace_external_id_when_bound",
            ),
        ),
        # --- Directory connection ------------------------------------------
        migrations.CreateModel(
            name="OrganizationalDirectoryConnection",
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
                ("provider", models.CharField(default="entra", max_length=30)),
                ("is_enabled", models.BooleanField(default=False)),
                ("tenant_id", models.CharField(blank=True, default="", max_length=255)),
                ("token_hash", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("token_prefix", models.CharField(blank=True, default="", max_length=12)),
                ("token_issued_at", models.DateTimeField(blank=True, null=True)),
                ("token_last_used_at", models.DateTimeField(blank=True, null=True)),
                ("auto_create_units", models.BooleanField(default=True)),
                ("deprovision_removes_membership", models.BooleanField(default=True)),
                ("last_sync_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_summary", models.JSONField(default=dict)),
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
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="directory_connection",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Organizational Directory Connection",
                "verbose_name_plural": "Organizational Directory Connections",
                "db_table": "organizational_directory_connections",
                "ordering": ("-created_at",),
            },
        ),
        # --- Directory identities -------------------------------------------
        migrations.CreateModel(
            name="OrganizationalDirectoryIdentity",
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
                ("external_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("user_name", models.CharField(max_length=255)),
                ("email", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("display_name", models.CharField(blank=True, default="", max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "state",
                    models.CharField(
                        choices=[("linked", "Linked"), ("unresolved", "Unresolved")],
                        default="unresolved",
                        max_length=12,
                    ),
                ),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("raw_payload", models.JSONField(default=dict)),
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
                        related_name="directory_identities",
                        to="db.workspace",
                    ),
                ),
                (
                    "workspace_member",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="directory_identities",
                        to="db.workspacemember",
                    ),
                ),
            ],
            options={
                "verbose_name": "Organizational Directory Identity",
                "verbose_name_plural": "Organizational Directory Identities",
                "db_table": "organizational_directory_identities",
                "ordering": ("user_name",),
            },
        ),
        # --- Directory group membership --------------------------------------
        migrations.CreateModel(
            name="OrganizationalDirectoryGroupMembership",
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
                    "identity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="group_memberships",
                        to="db.organizationaldirectoryidentity",
                    ),
                ),
                (
                    "organizational_unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="directory_group_memberships",
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
                        related_name="directory_group_memberships",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Organizational Directory Group Membership",
                "verbose_name_plural": "Organizational Directory Group Memberships",
                "db_table": "organizational_directory_group_memberships",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="organizationaldirectoryidentity",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("workspace", "user_name"),
                name="org_directory_identity_unique_workspace_user_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationaldirectoryidentity",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), models.Q(("external_id", ""), _negated=True)),
                fields=("workspace", "external_id"),
                name="org_directory_identity_unique_workspace_external_id",
            ),
        ),
        migrations.AddConstraint(
            model_name="organizationaldirectorygroupmembership",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("organizational_unit", "identity"),
                name="org_directory_group_membership_unique_unit_identity",
            ),
        ),
    ]
