# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Keep an assignment decision's provenance after its receipt is purged.

    ``AssignmentDecision.automation_operation`` is ``SET_NULL``, and the P0.20
    retention job hard-deletes receipts past the window. Django answers that
    delete with an UPDATE over the decision row, which is append-only, and the
    guard in ``save()`` never sees it because a queryset update does not call
    ``save()``. The information the foreign key carried is gone silently, and
    the log does not record that it existed (review finding R1.A3).

    These two columns hold the same answer in a form the purge cannot reach.
    Existing rows get the blank default: their receipts may already be gone,
    and inventing a key for them would be worse than admitting the gap.
    """

    dependencies = [("db", "0139_orca_unit_coordinator")]

    operations = [
        migrations.AddField(
            model_name="assignmentdecision",
            name="automation_idempotency_key",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="assignmentdecision",
            name="automation_operation_type",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
