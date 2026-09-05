# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
A deployed container has to be able to say which commit it came from.

The release chain proves which digest a commit produced (P0.2) and which
digest was promoted (P0.3), but both checks happen in the registry. Without
this, "is staging running the code we reviewed?" could only be answered by
trusting the pipeline that produced the answer. The image carries the commit
as an environment variable; the endpoint and the management command read it
back — the command because the worker and the beat container have no HTTP
surface and are exactly where a stale image hides.
"""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from plane.license.models import Instance, InstanceAdmin
from plane.utils.orca_build_info import BUILD_SHA_ENV, IMAGE_TAG_ENV, SERVICE_ENV, build_info


@pytest.fixture
def instance(db):
    # current_version and last_checked_at have no defaults on the model.
    return Instance.objects.create(
        instance_name="Orca",
        instance_id="orca-test",
        current_version="1.4.1",
        last_checked_at=timezone.now(),
    )


@pytest.fixture
def instance_admin_client(instance, admin_user):
    InstanceAdmin.objects.create(instance=instance, user=admin_user, role=20)
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.mark.unit
class TestBuildInfoPayload:
    def test_it_reports_what_the_image_was_built_with(self, monkeypatch, settings):
        monkeypatch.setenv(BUILD_SHA_ENV, "abc123def456")
        monkeypatch.setenv(IMAGE_TAG_ENV, "sha-abc123def456")
        monkeypatch.setenv(SERVICE_ENV, "beat-worker")
        monkeypatch.setenv("APP_VERSION", "1.4.0-plane.1.4.1")
        settings.ORCA_ORG_UNITS_ENABLED = True

        info = build_info()

        assert info == {
            "service": "beat-worker",
            "version": "1.4.0-plane.1.4.1",
            "git_sha": "abc123def456",
            "image_tag": "sha-abc123def456",
            "orca_org_units_enabled": True,
        }

    def test_an_image_not_built_by_ci_says_so_instead_of_guessing(self, monkeypatch):
        monkeypatch.delenv(BUILD_SHA_ENV, raising=False)
        monkeypatch.delenv(IMAGE_TAG_ENV, raising=False)
        monkeypatch.delenv(SERVICE_ENV, raising=False)

        info = build_info()

        assert info["git_sha"] == ""
        assert info["image_tag"] == ""
        # The API is the only service with an HTTP surface, so it is the
        # sensible answer when nothing named the service.
        assert info["service"] == "api"

    def test_it_carries_the_kill_switch(self, monkeypatch, settings):
        """
        @description The one setting that has to hold the same value in api,
        worker and beat (P0.14). Reading it from the same place that reports
        the commit is what makes "these three containers agree" checkable.
        """
        settings.ORCA_ORG_UNITS_ENABLED = False
        assert build_info()["orca_org_units_enabled"] is False


@pytest.mark.unit
class TestBuildInfoEndpoint:
    def test_an_instance_admin_gets_the_commit(self, instance_admin_client, monkeypatch):
        monkeypatch.setenv(BUILD_SHA_ENV, "deadbeef")

        response = instance_admin_client.get(reverse("orca-build-info"))

        assert response.status_code == 200
        assert response.data["git_sha"] == "deadbeef"

    def test_an_ordinary_member_is_refused(self, plain_user):
        client = APIClient()
        client.force_authenticate(user=plain_user)

        assert client.get(reverse("orca-build-info")).status_code == 403

    def test_an_anonymous_caller_is_refused(self, db):
        assert APIClient().get(reverse("orca-build-info")).status_code in (401, 403)

    def test_the_kill_switch_does_not_hide_it(self, instance_admin_client, settings):
        """
        @description Deliberately outside the organizational kill switch: the
        endpoint has to answer precisely when something looks wrong, and a
        misconfigured switch is one of those things.
        """
        settings.ORCA_ORG_UNITS_ENABLED = False

        response = instance_admin_client.get(reverse("orca-build-info"))

        assert response.status_code == 200
        assert response.data["orca_org_units_enabled"] is False


@pytest.mark.unit
def test_the_command_prints_the_same_answer_as_the_endpoint(db, monkeypatch):
    monkeypatch.setenv(BUILD_SHA_ENV, "cafebabe")
    monkeypatch.setenv(SERVICE_ENV, "worker")
    out = StringIO()

    call_command("orca_build_info", stdout=out)

    assert json.loads(out.getvalue()) == build_info()
