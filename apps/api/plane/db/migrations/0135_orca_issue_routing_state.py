# Queue state for the work an area owns: whether anybody is executing it, why
# it is waiting, and who the one answerable person is. Written by hand rather
# than by makemigrations (the agent session has no database) — run
# `python3 manage.py makemigrations --check` before merging.
#
# Order matters here. The CHECK that requires an executor for "assigned" rows
# can only be added after the data migration has given the existing rows one,
# so it comes last.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def backfill_routing_state(IssueOrganizationalUnit, IssueAssignee, queryset=None, now=None):
    """
    Give every existing link a queue state that matches reality.

    @description A link whose work item already has a native assignee is
    already being executed by somebody, so the oldest assignee becomes the
    primary executor. Everything else has nobody on it and joins the queue as
    a new item. Idempotent: a link that already carries an executor is left
    alone, so re-running after a partial failure changes nothing.

    Takes the model classes as arguments so the migration can pass the
    historical ones and the test suite the real ones — the judgement here is
    worth testing, and a migration function is otherwise unreachable.
    @param IssueOrganizationalUnit: The link model.
    @param IssueAssignee: The native assignee model.
    @param queryset: Links to process; defaults to every live link.
    @param now: Timestamp for ``queued_at``; defaults to the current time.
    @returns: How many links were updated.
    """
    now = now or timezone.now()
    links = queryset if queryset is not None else IssueOrganizationalUnit.objects.filter(deleted_at__isnull=True)

    updated = 0
    batch = []
    for link in links.iterator(chunk_size=500):
        if link.primary_executor_id is not None:
            continue

        assignee = (
            IssueAssignee.objects.filter(issue_id=link.issue_id, deleted_at__isnull=True)
            .order_by("created_at")
            .first()
        )
        if assignee is not None:
            link.routing_state = "assigned"
            link.primary_executor_id = assignee.assignee_id
            link.queue_reason = ""
            link.queued_at = None
        else:
            link.routing_state = "queued"
            link.queue_reason = "new_item"
            link.queued_at = link.queued_at or now

        batch.append(link)
        if len(batch) >= 500:
            IssueOrganizationalUnit.objects.bulk_update(
                batch, ["routing_state", "primary_executor_id", "queue_reason", "queued_at"]
            )
            updated += len(batch)
            batch = []

    if batch:
        IssueOrganizationalUnit.objects.bulk_update(
            batch, ["routing_state", "primary_executor_id", "queue_reason", "queued_at"]
        )
        updated += len(batch)

    return updated


def set_initial_routing_state(apps, schema_editor):
    """Run the backfill against the historical models."""
    backfill_routing_state(
        apps.get_model("db", "IssueOrganizationalUnit"),
        apps.get_model("db", "IssueAssignee"),
    )


def unset_routing_state(apps, schema_editor):
    """No-op: reversing the AddFields drops the columns this wrote."""



class Migration(migrations.Migration):
    dependencies = [
        ("db", "0134_orca_user_language_preference"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="routing_state",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("assigned", "Assigned"),
                    ("allocation_failed", "Allocation failed"),
                    ("suspended", "Suspended"),
                ],
                default="queued",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="queue_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("new_item", "New item"),
                    ("awaiting_coordinator", "Awaiting coordinator"),
                    ("awaiting_claim", "Awaiting claim"),
                    ("no_eligible_member", "No eligible member"),
                    ("executor_unavailable", "Executor unavailable"),
                    ("manually_returned", "Manually returned"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="queued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="assignment_due_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="primary_executor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="orca_primary_executions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(set_initial_routing_state, unset_routing_state),
        migrations.AddConstraint(
            model_name="issueorganizationalunit",
            constraint=models.CheckConstraint(
                condition=models.Q(("routing_state", "assigned"), _negated=True)
                | models.Q(("primary_executor__isnull", False)),
                name="issue_org_unit_assigned_requires_executor",
            ),
        ),
        migrations.AddConstraint(
            model_name="issueorganizationalunit",
            constraint=models.CheckConstraint(
                condition=models.Q(("routing_state", "assigned")) | models.Q(("primary_executor__isnull", True)),
                name="issue_org_unit_executor_requires_assigned",
            ),
        ),
        migrations.AddIndex(
            model_name="issueorganizationalunit",
            index=models.Index(
                fields=["workspace", "organizational_unit", "routing_state"],
                name="issue_org_unit_queue_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="issueorganizationalunit",
            index=models.Index(fields=["primary_executor", "routing_state"], name="issue_org_unit_executor_idx"),
        ),
    ]
