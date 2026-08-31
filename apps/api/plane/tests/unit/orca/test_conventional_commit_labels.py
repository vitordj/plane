# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Coverage for the Orca conventional-commit auto-labelling.

A project can opt in to having work items labelled from their title prefix:
``fix(api): drop the stale row`` gets a ``fix`` label, created on first use.
Three pieces make that work, none of them tested before —

* ``extract_conventional_commit_type`` decides what counts as a prefix;
* ``apply_conventional_commit_label`` labels one work item on create and on
  rename, and takes the old label off when the prefix changes;
* ``auto_label_conventional_commits_for_project`` back-fills the project the
  moment the setting is switched on.

The labelling writes real ``Label`` and ``IssueLabel`` rows and reuses whatever
is already there, so these run against the database. The last class goes through
the work item endpoint to prove the serializer actually calls into any of it.
"""

import pytest
from rest_framework import status

from plane.db.models import Issue, IssueLabel, Label, ProjectCustomSettings, ProjectMember, State
from plane.utils.conventional_commits import (
    CONVENTIONAL_COMMIT_PREFIXES,
    apply_conventional_commit_label,
    auto_label_conventional_commits_for_project,
    extract_conventional_commit_type,
)

from .conftest import ROLE_ADMIN

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def project_admin(project, admin_user, workspace_with_members):
    return ProjectMember.objects.create(
        project=project, member=admin_user, workspace=workspace_with_members, role=ROLE_ADMIN, is_active=True
    )


@pytest.fixture
def backlog_state(project, workspace_with_members):
    return State.objects.create(
        name="Backlog",
        group="backlog",
        sequence=10,
        color="#000000",
        default=True,
        project=project,
        workspace=workspace_with_members,
    )


@pytest.fixture
def auto_label_on(project, workspace_with_members):
    return ProjectCustomSettings.objects.create(
        project=project, workspace=workspace_with_members, auto_conventional_commit_labels=True
    )


@pytest.fixture
def make_issue(project, workspace_with_members, admin_user, backlog_state):
    def _make(name, state=None, project_override=None):
        return Issue.objects.create(
            name=name,
            project=project_override or project,
            workspace=workspace_with_members,
            state=state or backlog_state,
            created_by=admin_user,
        )

    return _make


def label_names_on(issue):
    """The labels actually attached right now, ignoring soft-deleted links."""
    return set(
        IssueLabel.objects.filter(issue_id=issue.id, deleted_at__isnull=True).values_list("label__name", flat=True)
    )


def issues_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/issues/"


# --- the prefix parser -------------------------------------------------------


@pytest.mark.unit
class TestExtractConventionalCommitType:
    @pytest.mark.parametrize("prefix", sorted(CONVENTIONAL_COMMIT_PREFIXES))
    def test_every_supported_prefix_is_recognised(self, prefix):
        assert extract_conventional_commit_type(f"{prefix}: do the thing") == prefix

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("feat(api): add the endpoint", "feat"),
            ("fix(web/ui): stop the flicker", "fix"),
            ("feat!: drop the legacy field", "feat"),
            ("refactor(store)!: rename the action", "refactor"),
            ("FEAT: shout about it", "feat"),
            ("Fix: mixed case", "fix"),
            ("   chore: leading whitespace", "chore"),
            ("feat:    lots of space", "feat"),
        ],
    )
    def test_accepted_shapes(self, title, expected):
        assert extract_conventional_commit_type(title) == expected

    @pytest.mark.parametrize(
        "title",
        [
            "",
            "   ",
            "no prefix at all",
            "banana: not a commit type",
            "feat",
            "feat:",
            "feat: ",
            "feat -- not a colon",
            "the feat: prefix is not at the start",
            "feat (api): space before the scope",
            "123: numeric prefix",
        ],
    )
    def test_rejected_shapes(self, title):
        assert extract_conventional_commit_type(title) is None

    def test_none_title_is_handled(self):
        assert extract_conventional_commit_type(None) is None


# --- labelling one work item -------------------------------------------------


@pytest.mark.unit
class TestApplyConventionalCommitLabel:
    def test_does_nothing_when_the_project_has_not_opted_in(self, db, make_issue):
        """No settings row at all — the common case, and it must stay silent."""
        issue = make_issue("feat: add the thing")

        assert apply_conventional_commit_label(issue) is None
        assert label_names_on(issue) == set()
        assert not Label.objects.filter(name="feat").exists()

    def test_does_nothing_when_the_setting_is_off(self, db, project, workspace_with_members, make_issue):
        ProjectCustomSettings.objects.create(
            project=project, workspace=workspace_with_members, auto_conventional_commit_labels=False
        )
        issue = make_issue("feat: add the thing")

        assert apply_conventional_commit_label(issue) is None
        assert label_names_on(issue) == set()

    def test_creates_the_label_and_attaches_it(self, db, auto_label_on, make_issue):
        issue = make_issue("feat: add the thing")

        label = apply_conventional_commit_label(issue)

        assert label is not None
        assert label.name == "feat"
        assert label.color == CONVENTIONAL_COMMIT_PREFIXES["feat"]
        assert label.project_id == issue.project_id
        assert label.workspace_id == issue.workspace_id
        assert label_names_on(issue) == {"feat"}

    def test_reuses_an_existing_label_whatever_its_case(
        self, db, auto_label_on, project, workspace_with_members, make_issue
    ):
        existing = Label.objects.create(name="Fix", color="#123456", project=project, workspace=workspace_with_members)
        issue = make_issue("fix: repair it")

        label = apply_conventional_commit_label(issue)

        assert label.id == existing.id, "a label differing only in case must not be duplicated"
        assert label.color == "#123456", "an existing label keeps the colour someone chose for it"
        assert Label.objects.filter(project=project, name__iexact="fix").count() == 1

    def test_is_idempotent(self, db, auto_label_on, make_issue):
        issue = make_issue("fix: repair it")

        apply_conventional_commit_label(issue)
        apply_conventional_commit_label(issue)

        assert IssueLabel.objects.filter(issue_id=issue.id, deleted_at__isnull=True).count() == 1

    def test_a_title_without_a_prefix_gets_nothing(self, db, auto_label_on, make_issue):
        issue = make_issue("just a work item")

        assert apply_conventional_commit_label(issue) is None
        assert label_names_on(issue) == set()

    def test_renaming_across_prefixes_swaps_the_label(self, db, auto_label_on, make_issue):
        issue = make_issue("feat: add the thing")
        apply_conventional_commit_label(issue)

        issue.name = "fix: no, repair the thing"
        issue.save()
        apply_conventional_commit_label(issue, old_title="feat: add the thing")

        assert label_names_on(issue) == {"fix"}, "the stale feat label must come off"

    def test_renaming_within_the_same_prefix_keeps_one_label(self, db, auto_label_on, make_issue):
        issue = make_issue("feat: add the thing")
        apply_conventional_commit_label(issue)

        issue.name = "feat(api): add the thing properly"
        issue.save()
        apply_conventional_commit_label(issue, old_title="feat: add the thing")

        assert label_names_on(issue) == {"feat"}
        assert IssueLabel.objects.filter(issue_id=issue.id, deleted_at__isnull=True).count() == 1

    def test_renaming_away_from_a_prefix_removes_the_label(self, db, auto_label_on, make_issue):
        issue = make_issue("feat: add the thing")
        apply_conventional_commit_label(issue)

        issue.name = "add the thing"
        issue.save()

        assert apply_conventional_commit_label(issue, old_title="feat: add the thing") is None
        assert label_names_on(issue) == set()

    def test_hand_picked_labels_survive_a_rename(self, db, auto_label_on, project, workspace_with_members, make_issue):
        """Only the prefix label is managed; a label someone chose is not ours to remove."""
        manual = Label.objects.create(name="urgent", color="#FF0000", project=project, workspace=workspace_with_members)
        issue = make_issue("feat: add the thing")
        apply_conventional_commit_label(issue)
        IssueLabel.objects.create(issue=issue, label=manual, project=project, workspace=workspace_with_members)

        issue.name = "fix: repair the thing"
        issue.save()
        apply_conventional_commit_label(issue, old_title="feat: add the thing")

        assert label_names_on(issue) == {"fix", "urgent"}

    def test_labels_are_scoped_to_their_own_project(
        self, db, auto_label_on, project, second_project, workspace_with_members, make_issue
    ):
        ProjectCustomSettings.objects.create(
            project=second_project, workspace=workspace_with_members, auto_conventional_commit_labels=True
        )
        other_state = State.objects.create(
            name="Backlog",
            group="backlog",
            sequence=10,
            color="#000000",
            project=second_project,
            workspace=workspace_with_members,
        )

        here = make_issue("feat: here")
        there = make_issue("feat: there", state=other_state, project_override=second_project)
        apply_conventional_commit_label(here)
        apply_conventional_commit_label(there)

        assert Label.objects.filter(project=project, name="feat").count() == 1
        assert Label.objects.filter(project=second_project, name="feat").count() == 1

    def test_an_issue_without_a_project_is_ignored(self, db):
        assert apply_conventional_commit_label(None) is None
        assert apply_conventional_commit_label(Issue()) is None


# --- back-filling a project --------------------------------------------------


@pytest.mark.unit
class TestBackfillOnEnable:
    def test_labels_the_unlabelled_work_items(self, db, project, make_issue):
        feat = make_issue("feat: one")
        fix = make_issue("fix: two")
        plain = make_issue("three")

        auto_label_conventional_commits_for_project(project)

        assert label_names_on(feat) == {"feat"}
        assert label_names_on(fix) == {"fix"}
        assert label_names_on(plain) == set()

    def test_skips_work_that_is_already_labelled(self, db, project, workspace_with_members, make_issue):
        """
        The back-fill deliberately only touches work with no labels at all, so it
        cannot bury a triage someone already did.
        """
        manual = Label.objects.create(name="urgent", color="#FF0000", project=project, workspace=workspace_with_members)
        issue = make_issue("feat: already triaged")
        IssueLabel.objects.create(issue=issue, label=manual, project=project, workspace=workspace_with_members)

        auto_label_conventional_commits_for_project(project)

        assert label_names_on(issue) == {"urgent"}

    def test_reuses_one_label_row_across_work_items(self, db, project, make_issue):
        first = make_issue("feat: one")
        second = make_issue("feat(api): two")

        auto_label_conventional_commits_for_project(project)

        assert Label.objects.filter(project=project, name="feat").count() == 1
        assert label_names_on(first) == label_names_on(second) == {"feat"}

    def test_is_idempotent(self, db, project, make_issue):
        issue = make_issue("feat: one")

        auto_label_conventional_commits_for_project(project)
        auto_label_conventional_commits_for_project(project)

        assert IssueLabel.objects.filter(issue_id=issue.id, deleted_at__isnull=True).count() == 1

    def test_a_missing_project_is_ignored(self, db):
        assert auto_label_conventional_commits_for_project(None) is None


# --- the wiring, through the endpoint ----------------------------------------


@pytest.mark.contract
class TestLabellingThroughTheEndpoint:
    """
    The serializer is what calls the labeller, on create and on a title change.
    These prove the wiring, not the rules — the rules are covered above.
    """

    def test_creating_a_work_item_applies_the_label(
        self, admin_client, workspace_with_members, project, project_admin, backlog_state, auto_label_on
    ):
        response = admin_client.post(
            issues_url(workspace_with_members.slug, project.id),
            {"name": "feat: add the endpoint", "state_id": str(backlog_state.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(id=response.data["id"])
        assert label_names_on(issue) == {"feat"}

    def test_creating_a_work_item_applies_nothing_when_the_setting_is_off(
        self, admin_client, workspace_with_members, project, project_admin, backlog_state
    ):
        response = admin_client.post(
            issues_url(workspace_with_members.slug, project.id),
            {"name": "feat: add the endpoint", "state_id": str(backlog_state.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = Issue.objects.get(id=response.data["id"])
        assert label_names_on(issue) == set()

    def test_renaming_a_work_item_swaps_the_label(
        self, admin_client, workspace_with_members, project, project_admin, backlog_state, auto_label_on
    ):
        created = admin_client.post(
            issues_url(workspace_with_members.slug, project.id),
            {"name": "feat: add the endpoint", "state_id": str(backlog_state.id)},
            format="json",
        )
        issue_id = created.data["id"]

        response = admin_client.patch(
            f"{issues_url(workspace_with_members.slug, project.id)}{issue_id}/",
            {"name": "fix: repair the endpoint"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert label_names_on(Issue.objects.get(id=issue_id)) == {"fix"}

    def test_editing_something_other_than_the_title_leaves_labels_alone(
        self, admin_client, workspace_with_members, project, project_admin, backlog_state, auto_label_on
    ):
        created = admin_client.post(
            issues_url(workspace_with_members.slug, project.id),
            {"name": "feat: add the endpoint", "state_id": str(backlog_state.id)},
            format="json",
        )
        issue_id = created.data["id"]

        response = admin_client.patch(
            f"{issues_url(workspace_with_members.slug, project.id)}{issue_id}/",
            {"priority": "high"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert label_names_on(Issue.objects.get(id=issue_id)) == {"feat"}
