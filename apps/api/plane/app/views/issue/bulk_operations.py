# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import json
from datetime import datetime

# Django imports
from django.db import transaction
from django.utils import timezone

# Third Party imports
from rest_framework.response import Response
from rest_framework import status

# Module imports
from .. import BaseAPIView
from plane.app.permissions import (
    ProjectEntityPermission,
)
from plane.db.models import (
    Project,
    Issue,
    IssueLabel,
    IssueAssignee,
    IssueSubscriber,
    Label,
    Cycle,
    Module,
    ProjectMember,
    State,
    CycleIssue,
    ModuleIssue,
)
from plane.bgtasks.issue_activities_task import issue_activity


class BulkIssueOperationsEndpoint(BaseAPIView):
    """
    Orca Custom Endpoint:
    Provides a bulk update operations API for issue fields (priority, state, dates, etc.),
    as well as labels, assignees, cycle, and module associations.
    """
    permission_classes = [
        ProjectEntityPermission,
    ]

    @transaction.atomic
    def post(self, request, slug, project_id):
        """
        Processes a bulk operation request for a set of issue IDs in a project.

        The whole batch is one transaction: the date checks below run per work
        item inside the loop, so a work item rejected halfway through would
        otherwise leave the ones before it already written.
        """
        issue_ids = request.data.get("issue_ids", [])
        if not len(issue_ids):
            return Response(
                {"error": "Issue IDs are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get all the issues
        issues = (
            Issue.objects.filter(
                workspace__slug=slug, project_id=project_id, pk__in=issue_ids
            )
            .select_related("state")
            .prefetch_related("labels", "assignees")
        )
        # Current epoch
        epoch = int(timezone.now().timestamp())

        # Project details
        project = Project.objects.get(workspace__slug=slug, pk=project_id)
        workspace_id = project.workspace_id

        # Initialize arrays
        bulk_update_issues = []
        bulk_issue_activities = []
        bulk_update_issue_labels = []
        bulk_update_issue_assignees = []

        properties = request.data.get("properties", {})

        if properties.get("start_date", False) and properties.get(
            "target_date", False
        ):
            if (
                datetime.strptime(
                    properties.get("start_date"), "%Y-%m-%d"
                ).date()
                > datetime.strptime(
                    properties.get("target_date"), "%Y-%m-%d"
                ).date()
            ):
                return Response(
                    {
                        "error_code": 4100,
                        "error_message": "INVALID_ISSUE_DATES",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        for issue in issues:
            # Priority
            if properties.get("priority", False):
                bulk_issue_activities.append(
                    {
                        "type": "issue.activity.updated",
                        "requested_data": json.dumps(
                            {"priority": properties.get("priority")}
                        ),
                        "current_instance": json.dumps(
                            {"priority": (issue.priority)}
                        ),
                        "issue_id": str(issue.id),
                        "actor_id": str(request.user.id),
                        "project_id": str(project_id),
                        "epoch": epoch,
                    }
                )
                issue.priority = properties.get("priority")

            # Subscription
            if "is_subscribed" in properties:
                is_subscribed = properties.get("is_subscribed")
                if is_subscribed:
                    IssueSubscriber.objects.get_or_create(
                        issue=issue,
                        subscriber=request.user,
                        project_id=project_id,
                        workspace_id=workspace_id,
                    )
                else:
                    IssueSubscriber.objects.filter(
                        issue=issue,
                        subscriber=request.user,
                        project_id=project_id,
                    ).delete()

            # State
            if properties.get("state_id", False):
                try:
                    state_obj = State.objects.get(pk=properties.get("state_id"), project_id=project_id)
                    bulk_issue_activities.append(
                        {
                            "type": "issue.activity.updated",
                            "requested_data": json.dumps(
                                {"state": properties.get("state")}
                            ),
                            "current_instance": json.dumps(
                                {"state": str(issue.state_id)}
                            ),
                            "issue_id": str(issue.id),
                            "actor_id": str(request.user.id),
                            "project_id": str(project_id),
                            "epoch": epoch,
                        }
                    )
                    issue.state = state_obj
                except State.DoesNotExist:
                    pass

            # Start date
            if "start_date" in properties:
                start_date_val = properties.get("start_date")
                start_date_val = start_date_val if start_date_val else None
                if start_date_val:
                    if (
                        issue.target_date
                        and not properties.get("target_date", False)
                        and issue.target_date
                        <= datetime.strptime(
                            start_date_val, "%Y-%m-%d"
                        ).date()
                    ):
                        transaction.set_rollback(True)
                        return Response(
                            {
                                "error_code": 4101,
                                "error_message": "INVALID_ISSUE_START_DATE",
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                bulk_issue_activities.append(
                    {
                        "type": "issue.activity.updated",
                        "requested_data": json.dumps(
                            {"start_date": start_date_val}
                        ),
                        "current_instance": json.dumps(
                            {"start_date": str(issue.start_date)}
                        ),
                        "issue_id": str(issue.id),
                        "actor_id": str(request.user.id),
                        "project_id": str(project_id),
                        "epoch": epoch,
                    }
                )
                issue.start_date = start_date_val

            # Target date
            if "target_date" in properties:
                target_date_val = properties.get("target_date")
                target_date_val = target_date_val if target_date_val else None
                if target_date_val:
                    if (
                        issue.start_date
                        and not properties.get("start_date", False)
                        and issue.start_date
                        >= datetime.strptime(
                            target_date_val, "%Y-%m-%d"
                        ).date()
                    ):
                        transaction.set_rollback(True)
                        return Response(
                            {
                                "error_code": 4102,
                                "error_message": "INVALID_ISSUE_TARGET_DATE",
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                bulk_issue_activities.append(
                    {
                        "type": "issue.activity.updated",
                        "requested_data": json.dumps(
                            {"target_date": target_date_val}
                        ),
                        "current_instance": json.dumps(
                            {"target_date": str(issue.target_date)}
                        ),
                        "issue_id": str(issue.id),
                        "actor_id": str(request.user.id),
                        "project_id": str(project_id),
                        "epoch": epoch,
                    }
                )
                issue.target_date = target_date_val

            # Cycles
            if "cycle_id" in properties:
                cycle_id = properties.get("cycle_id")
                # A cycle from another project is not a cycle this work item can join,
                # so the whole change is skipped rather than clearing what is there.
                if cycle_id and not Cycle.objects.filter(pk=cycle_id, project_id=project_id).exists():
                    cycle_id = False
                if cycle_id is not False:
                    # Delete existing CycleIssue for the issue
                    CycleIssue.objects.filter(issue=issue, project_id=project_id).delete()
                    if cycle_id:
                        CycleIssue.objects.create(
                            issue=issue,
                            cycle_id=cycle_id,
                            project_id=project_id,
                            workspace_id=workspace_id,
                        )

            # Modules
            if "module_ids" in properties:
                module_ids = properties.get("module_ids", [])
                # Drop any module outside this project, as IssueSerializer.validate does for labels
                module_ids = [
                    str(m_id)
                    for m_id in Module.objects.filter(project_id=project_id, id__in=module_ids).values_list(
                        "id", flat=True
                    )
                ]
                ModuleIssue.objects.filter(
                    issue=issue,
                    project_id=project_id,
                ).exclude(module_id__in=module_ids).delete()

                # Get existing module IDs for the issue
                existing_module_ids = {
                    str(mi.module_id)
                    for mi in ModuleIssue.objects.filter(issue=issue, project_id=project_id)
                }
                new_module_ids = [
                    m_id for m_id in module_ids
                    if str(m_id) not in existing_module_ids
                ]
                for module_id in new_module_ids:
                    ModuleIssue.objects.create(
                        issue=issue,
                        module_id=module_id,
                        project_id=project_id,
                        workspace_id=workspace_id,
                    )

            bulk_update_issues.append(issue)

            # Labels
            if "label_ids" in properties:
                label_ids = properties.get("label_ids", [])
                # Only this project's labels, the same rule IssueSerializer.validate applies
                label_ids = [
                    str(l_id)
                    for l_id in Label.objects.filter(project_id=project_id, id__in=label_ids).values_list(
                        "id", flat=True
                    )
                ]
                IssueLabel.objects.filter(
                    issue=issue,
                    project_id=project_id,
                ).exclude(label_id__in=label_ids).delete()

                existing_label_ids = {
                    str(il.label_id)
                    for il in IssueLabel.objects.filter(issue=issue, project_id=project_id)
                }
                new_label_ids = [
                    l_id for l_id in label_ids
                    if str(l_id) not in existing_label_ids
                ]
                for label_id in new_label_ids:
                    bulk_update_issue_labels.append(
                        IssueLabel(
                            issue=issue,
                            label_id=label_id,
                            created_by=request.user,
                            project_id=project_id,
                            workspace_id=workspace_id,
                        )
                    )
                bulk_issue_activities.append(
                    {
                        "type": "issue.activity.updated",
                        "requested_data": json.dumps(
                            {"label_ids": label_ids}
                        ),
                        "current_instance": json.dumps(
                            {
                                "label_ids": [
                                    str(label.id)
                                    for label in issue.labels.all()
                                ]
                            }
                        ),
                        "issue_id": str(issue.id),
                        "actor_id": str(request.user.id),
                        "project_id": str(project_id),
                        "epoch": epoch,
                    }
                )

            # Assignees
            if "assignee_ids" in properties:
                assignee_ids = properties.get("assignee_ids", [])
                # An assignee has to be an active member of the project, the same rule
                # IssueSerializer.validate applies — guests and non-members are dropped
                assignee_ids = [
                    str(a_id)
                    for a_id in ProjectMember.objects.filter(
                        project_id=project_id,
                        role__gte=15,
                        is_active=True,
                        member_id__in=assignee_ids,
                    ).values_list("member_id", flat=True)
                ]
                IssueAssignee.objects.filter(
                    issue=issue,
                    project_id=project_id,
                ).exclude(assignee_id__in=assignee_ids).delete()

                existing_assignee_ids = {
                    str(ia.assignee_id)
                    for ia in IssueAssignee.objects.filter(issue=issue, project_id=project_id)
                }
                new_assignee_ids = [
                    a_id for a_id in assignee_ids
                    if str(a_id) not in existing_assignee_ids
                ]
                for assignee_id in new_assignee_ids:
                    bulk_update_issue_assignees.append(
                        IssueAssignee(
                            issue=issue,
                            assignee_id=assignee_id,
                            created_by=request.user,
                            project_id=project_id,
                            workspace_id=workspace_id,
                        )
                    )
                bulk_issue_activities.append(
                    {
                        "type": "issue.activity.updated",
                        "requested_data": json.dumps(
                            {
                                "assignee_ids": assignee_ids
                            }
                        ),
                        "current_instance": json.dumps(
                            {
                                "assignee_ids": [
                                    str(assignee.id)
                                    for assignee in issue.assignees.all()
                                ]
                            }
                        ),
                        "issue_id": str(issue.id),
                        "actor_id": str(request.user.id),
                        "project_id": str(project_id),
                        "epoch": epoch,
                    }
                )

        # Bulk update all the objects
        Issue.objects.bulk_update(
            bulk_update_issues,
            [
                "priority",
                "start_date",
                "target_date",
                "state",
            ],
            batch_size=100,
        )

        # Create new labels
        IssueLabel.objects.bulk_create(
            bulk_update_issue_labels,
            ignore_conflicts=True,
            batch_size=100,
        )

        # Create new assignees
        IssueAssignee.objects.bulk_create(
            bulk_update_issue_assignees,
            ignore_conflicts=True,
            batch_size=100,
        )
        # update the issue activity
        [
            issue_activity.delay(**activity)
            for activity in bulk_issue_activities
        ]

        return Response(status=status.HTTP_204_NO_CONTENT)
