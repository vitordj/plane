# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Startup-registration tests for the organizational reconciliation task.

The web process reaches the task through a lazy import right before calling
``.delay()``, so a producer-side import proves nothing about the worker: the
worker only knows the tasks it imported at startup. It imports them from
``CELERY_IMPORTS`` plus ``autodiscover_tasks()``, and autodiscovery only looks
for modules literally named ``tasks`` — never ``*_task.py``. Leaving the module
out of ``CELERY_IMPORTS`` therefore fails at runtime, in the worker, with
``Received unregistered task``, long after the request that queued it returned
200.

These tests reproduce worker startup rather than importing the task module
directly, which would register it as a side effect and pass regardless.
"""

import pytest
from django.conf import settings

from plane.celery import app as celery_app

TASK_NAME = "plane.bgtasks.organizational_unit_task.reconcile_organizational_access"
TASK_MODULE = "plane.bgtasks.organizational_unit_task"


@pytest.mark.unit
class TestOrganizationalTaskRegistration:
    def test_the_task_module_is_listed_in_celery_imports(self):
        # The setting is what the worker actually reads at startup; asserting on
        # it separately localizes the failure when someone rewrites the tuple.
        assert TASK_MODULE in tuple(settings.CELERY_IMPORTS)

    def test_the_celery_app_carries_the_module_in_its_import_list(self):
        # namespace="CELERY" maps CELERY_IMPORTS onto celery's own `imports`
        # key. If that mapping ever breaks, the setting above would still pass
        # while the worker imported nothing.
        assert TASK_MODULE in tuple(celery_app.conf.imports or ())

    def test_worker_startup_registers_the_reconciliation_task(self):
        # import_default_modules() is exactly what a booting worker runs to
        # turn `imports` into registered tasks, so this is the real check.
        celery_app.loader.import_default_modules()

        assert TASK_NAME in celery_app.tasks

    def test_the_registered_task_is_the_one_the_dispatcher_queues(self):
        # A name can be registered by a callable other than the one the
        # producer imports, so pin the two together: this is the symbol
        # ``dispatch_reconciliation`` imports before calling ``.delay()``.
        from plane.bgtasks.organizational_unit_task import reconcile_organizational_access

        celery_app.loader.import_default_modules()

        assert reconcile_organizational_access.name == TASK_NAME
        # ``shared_task`` hands back a lazy Proxy rather than the task itself,
        # so `is` compares the wrapper and always fails; resolve it first.
        assert celery_app.tasks[TASK_NAME] is reconcile_organizational_access._get_current_object()
