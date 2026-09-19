# Queue state and primary executor for a work item owned by an area.
#
# Responsibility and assignment used to be one moment: the link existed, and
# either somebody was assigned or nobody was. There was no way to tell "waiting
# to be picked up" from "the allocator tried and found nobody", which is the
# distinction a coordinator's board is made of. See
# docs/orca-work-management-rfc.md §5.1.
#
# Written by hand rather than by makemigrations (the agent session has no
# database); run `python3 apps/api/manage.py makemigrations --check --dry-run`
# to confirm it matches the models.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def set_initial_routing_state(apps, schema_editor):
    """
    @description Give every existing link a state that matches reality: an item
    that already has an assignee is ``assigned`` to the earliest of them, and
    everything else joins the queue as a new item. Runs before the CHECK that
    requires an executor for ``assigned`` is added, because until this has run
    every row is ``assigned`` with no executor by field default.
    @param apps: Historical model registry.
    @param schema_editor: Unused; the data is rewritten through the ORM.
    @returns None
    """
    IssueOrganizationalUnit = apps.get_model("db", "IssueOrganizationalUnit")
    IssueAssignee = apps.get_model("db", "IssueAssignee")
    now = timezone.now()

    batch = []
    for link in IssueOrganizationalUnit.objects.filter(deleted_at__isnull=True).iterator(chunk_size=500):
        earliest = (
            IssueAssignee.objects.filter(issue_id=link.issue_id, deleted_at__isnull=True)
            .order_by("created_at")
            .first()
        )
        if earliest is not None:
            link.routing_state = "assigned"
            link.primary_executor_id = earliest.assignee_id
            link.queue_reason = ""
            link.queued_at = None
        else:
            link.routing_state = "queued"
            link.primary_executor_id = None
            link.queue_reason = "new_item"
            # Only on the way in: re-running must not reset how long an item
            # has been waiting.
            link.queued_at = link.queued_at or now

        batch.append(link)
        if len(batch) >= 500:
            IssueOrganizationalUnit.objects.bulk_update(
                batch, ["routing_state", "primary_executor", "queue_reason", "queued_at"]
            )
            batch = []

    if batch:
        IssueOrganizationalUnit.objects.bulk_update(
            batch, ["routing_state", "primary_executor", "queue_reason", "queued_at"]
        )


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
                max_length=32,
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
        # Data first: the CHECK below would reject every pre-existing row.
        migrations.RunPython(set_initial_routing_state, migrations.RunPython.noop, elidable=False),
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
                name="issue_org_unit_executor_only_when_assigned",
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
            index=models.Index(fields=["primary_executor", "routing_state"], name="issue_org_unit_load_idx"),
        ),
    ]
