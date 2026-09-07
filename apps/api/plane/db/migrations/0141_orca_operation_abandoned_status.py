# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Split a receipt's terminal failure into ``failed`` and ``abandoned``.

    ``failed`` stays what it was: a refusal the endpoint chose, which a replay
    reproduces because repeating it cannot change the answer. ``abandoned`` is
    new, and is what an unhandled exception leaves behind — a deadlock, a
    dropped connection, an integrity error under concurrency. The work never
    happened, so the next call carrying that key must run it instead of being
    handed the stored error forever (review finding R1.A6).

    Choices only: no column changes shape, and no existing row moves. Rows
    already sitting in ``failed`` from a transient error keep replaying their
    error until retention removes them, which is the old behaviour and is not
    worth a data migration guessing which failures were which.
    """

    dependencies = [("db", "0140_orca_decision_automation_provenance")]

    operations = [
        migrations.AlterField(
            model_name="automationoperation",
            name="status",
            field=models.CharField(
                choices=[
                    ("in_progress", "In progress"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("abandoned", "Abandoned"),
                ],
                default="in_progress",
                max_length=12,
            ),
        ),
    ]
