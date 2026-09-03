# Remembers when an overdue work item was last complained about, so a sweep
# every fifteen minutes does not notify the same person ninety-six times a day.
# Hand-written; run `python3 manage.py makemigrations --check` before merging.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("db", "0139_orca_unit_coordinator")]

    operations = [
        migrations.AddField(
            model_name="issueorganizationalunit",
            name="last_alerted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
