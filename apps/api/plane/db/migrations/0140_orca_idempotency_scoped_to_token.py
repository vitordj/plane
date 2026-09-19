# R1.A12: an Idempotency-Key is unique per API token, not across the workspace.
#
# Two integrations in the same workspace (a Zendesk connector and a migration
# script, say) must be able to reuse the same key without one burning the
# other's namespace. A receipt whose token was deleted (SET_NULL) no longer
# occupies a live token's key — PostgreSQL treats NULL as distinct, which is
# the acceptable leftover the review named.
#
# Numbered 0140. Availability (Phase 3) moves to 0141: Django binds migrations
# by dependency, not by number, and this follow-up landed before that phase.
#
# Written by hand (the agent session has no database); confirm with
# `python3 apps/api/manage.py makemigrations --check --dry-run`.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0139_orca_unit_coordinator"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="automationoperation",
            name="orca_operation_unique_idempotency_key",
        ),
        migrations.AddConstraint(
            model_name="automationoperation",
            constraint=models.UniqueConstraint(
                fields=("workspace", "api_token", "idempotency_key"),
                name="orca_operation_unique_token_idempotency_key",
            ),
        ),
    ]
