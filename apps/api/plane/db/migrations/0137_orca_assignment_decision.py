# The audit trail: every decision about who executes a work item, and every
# change of which area owns it. Both tables are append-only at the model level.
# The link's current_assignment_decision lands here rather than in 0135 to keep
# the dependency one-directional. Hand-written — run
# `python3 manage.py makemigrations --check` before merging.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def audit_fields():
    return [
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
    ]


def audit_user_fields(model_name):
    return [
        (
            "created_by",
            models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name=f"{model_name}_created_by",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Created By",
            ),
        ),
        (
            "updated_by",
            models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name=f"{model_name}_updated_by",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Last Modified By",
            ),
        ),
    ]


DECISION_TRIGGERS = [
    ("public_api", "Public API"),
    ("internal_api", "Internal API"),
    ("ui_claim", "Claimed in the app"),
    ("ui_coordinator", "Coordinator in the app"),
    ("reassign", "Reassignment"),
    ("availability", "Availability sweep"),
    ("return_to_queue", "Returned to the queue"),
    ("command", "Management command"),
]

REQUESTED_MODES = [
    ("default", "Default"),
    ("manual", "Manual"),
    ("self_claim", "Self claim"),
    ("least_loaded", "Least loaded"),
    ("explicit", "Explicit"),
]

EFFECTIVE_MODES = [
    ("manual", "Manual"),
    ("self_claim", "Self claim"),
    ("least_loaded", "Least loaded"),
    ("explicit", "Explicit"),
]

POLICY_SOURCES = [
    ("request", "Request"),
    ("unit_project", "Area and project"),
    ("unit", "Area"),
    ("fallback", "Fallback"),
]

OUTCOMES = [
    ("assigned", "Assigned"),
    ("queued", "Queued"),
    ("allocation_failed", "Allocation failed"),
    ("rejected", "Rejected"),
]

RESPONSIBILITY_SOURCES = [
    ("public_api", "Public API"),
    ("internal_api", "Internal API"),
    ("ui", "App"),
    ("command", "Management command"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0136_orca_assignment_policy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssignmentDecision",
            fields=audit_fields()
            + [
                ("trigger", models.CharField(choices=DECISION_TRIGGERS, max_length=24)),
                ("requested_mode", models.CharField(blank=True, choices=REQUESTED_MODES, max_length=16, null=True)),
                ("effective_mode", models.CharField(choices=EFFECTIVE_MODES, max_length=16)),
                ("policy_source", models.CharField(choices=POLICY_SOURCES, max_length=16)),
                ("policy_version", models.PositiveIntegerField(blank=True, null=True)),
                ("algorithm_version", models.CharField(default="lb-1", max_length=16)),
                ("outcome", models.CharField(choices=OUTCOMES, max_length=24)),
                ("candidates_snapshot", models.JSONField(blank=True, default=list)),
                ("reason", models.TextField(blank=True, default="")),
            ]
            + audit_user_fields("assignmentdecision")
            + [
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
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="orca_assignment_decisions",
                        to="db.project",
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
                    "chosen_assignee",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_decisions_chosen",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "previous_primary_executor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_decisions_superseded",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_decisions_made",
                        to=settings.AUTH_USER_MODEL,
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
            fields=audit_fields()
            + [
                ("source", models.CharField(choices=RESPONSIBILITY_SOURCES, max_length=16)),
                ("reason", models.TextField(blank=True, default="")),
            ]
            + audit_user_fields("issueresponsibilityevent")
            + [
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
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="orca_responsibility_events",
                        to=settings.AUTH_USER_MODEL,
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
            index=models.Index(fields=["issue", "created_at"], name="orca_responsibility_issue_idx"),
        ),
    ]
