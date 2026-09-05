# The append-only record of every allocation, and of every change of which
# area owns a work item — plus the pointer from the link to the decision
# currently in force.
#
# Split from 0136 so the FK on IssueOrganizationalUnit can be added after the
# decision table exists, which keeps the model imports one-way: the decision
# log knows about the organizational models, not the other way round. See
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
        ("db", "0136_orca_assignment_policy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssignmentDecision",
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
                    "trigger",
                    models.CharField(
                        choices=[
                            ("public_api", "Public API"),
                            ("internal_api", "Internal API"),
                            ("ui_claim", "UI claim"),
                            ("ui_coordinator", "UI coordinator"),
                            ("reassign", "Reassign"),
                            ("availability", "Availability"),
                            ("return_to_queue", "Return to queue"),
                            ("command", "Command"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "requested_mode",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("default", "Default"),
                            ("explicit", "Explicit"),
                            ("manual", "Manual"),
                            ("self_claim", "Self claim"),
                            ("least_loaded", "Least loaded"),
                        ],
                        max_length=16,
                        null=True,
                    ),
                ),
                (
                    "effective_mode",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("self_claim", "Self claim"),
                            ("least_loaded", "Least loaded"),
                            ("explicit", "Explicit"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "policy_source",
                    models.CharField(
                        choices=[
                            ("request", "Request"),
                            ("unit_project", "Unit project"),
                            ("unit", "Unit"),
                            ("fallback", "Fallback"),
                        ],
                        max_length=16,
                    ),
                ),
                ("policy_version", models.PositiveIntegerField(blank=True, null=True)),
                ("algorithm_version", models.CharField(default="lb-1", max_length=16)),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("assigned", "Assigned"),
                            ("queued", "Queued"),
                            ("allocation_failed", "Allocation failed"),
                            ("rejected", "Rejected"),
                        ],
                        max_length=20,
                    ),
                ),
                ("candidates_snapshot", models.JSONField(default=list)),
                ("reason", models.TextField(blank=True, default="")),
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
                    "chosen_assignee",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_assignment_decisions_won",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "previous_primary_executor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_assignment_decisions_lost",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_assignment_decisions_made",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_assignment_decisions",
                        to="db.issue",
                    ),
                ),
                (
                    "organizational_unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignment_decisions",
                        to="db.organizationalunit",
                    ),
                ),
                (
                    "policy",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="decisions",
                        to="db.organizationalunitassignmentpolicy",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_assignment_decisions",
                        to="db.project",
                    ),
                ),
                (
                    "supersedes",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="superseded_by",
                        to="db.assignmentdecision",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_assignment_decisions",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Assignment Decision",
                "verbose_name_plural": "Assignment Decisions",
                "db_table": "orca_assignment_decisions",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="IssueResponsibilityEvent",
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
                    "source",
                    models.CharField(
                        choices=[
                            ("public_api", "Public API"),
                            ("internal_api", "Internal API"),
                            ("ui", "UI"),
                            ("command", "Command"),
                        ],
                        max_length=16,
                    ),
                ),
                ("reason", models.TextField(blank=True, default="")),
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
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_responsibility_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "from_unit",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="responsibility_events_from",
                        to="db.organizationalunit",
                    ),
                ),
                (
                    "to_unit",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="responsibility_events_to",
                        to="db.organizationalunit",
                    ),
                ),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_responsibility_events",
                        to="db.issue",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_responsibility_events",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Issue Responsibility Event",
                "verbose_name_plural": "Issue Responsibility Events",
                "db_table": "orca_issue_responsibility_events",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="current_assignment_decision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="current_for_links",
                to="db.assignmentdecision",
            ),
        ),
        migrations.AddIndex(
            model_name="assignmentdecision",
            index=models.Index(fields=["issue", "created_at"], name="orca_decision_issue_idx"),
        ),
        migrations.AddIndex(
            model_name="assignmentdecision",
            index=models.Index(fields=["organizational_unit", "created_at"], name="orca_decision_unit_idx"),
        ),
        migrations.AddIndex(
            model_name="issueresponsibilityevent",
            index=models.Index(fields=["issue", "created_at"], name="orca_resp_event_issue_idx"),
        ),
    ]
