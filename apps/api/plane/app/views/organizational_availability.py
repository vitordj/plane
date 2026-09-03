# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Saying you are away, and saying how much you will take.

Two things, deliberately with different owners:

* **Absences** belong to the person. They enter their own; a coordinator of an
  area that person works in may enter one for them, because somebody has to
  when a colleague is in hospital, and an admin may always.
* **How much work an area gives somebody** belongs to the area. The person may
  switch off new work — that is a "not right now", and refusing it would just
  produce the same effect through a fake absence — but the ceiling is the
  coordinator's, or anybody could set their own limit to one and stop being
  given anything while still looking available.

Every route here is 404 when ``ORCA_AVAILABILITY_ENABLED`` is off, the same
way the organizational layer's routes are: a feature that is not on should
read as absent, not as something the caller lacks rights for.
"""

# Django imports
from django.http import Http404
from django.utils.dateparse import parse_datetime

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.permissions.organizational_unit import (
    is_unit_coordinator,
    is_workspace_admin,
)
from plane.app.serializers import (
    MembershipAllocationSettingsSerializer,
    WorkspaceMemberAvailabilitySerializer,
)
from plane.app.services.orca import availability_enabled
from plane.db.models import (
    MembershipAllocationSettings,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    WorkspaceMember,
    WorkspaceMemberAvailability,
)
from plane.db.models.organizational_unit import AvailabilityReason, AvailabilitySource
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView


class AvailabilityFeatureMixin:
    """
    404 while the feature is off.

    @description Same shape as the organizational layer's own switch: an
    instance that has not turned availability on should not have these routes
    at all, so that switching it off during an incident makes them gone rather
    than merely refusing.
    """

    def initial(self, request, *args, **kwargs):
        if not availability_enabled():
            raise Http404("Availability is disabled on this instance")
        return super().initial(request, *args, **kwargs)


def _coordinates_any_area_of(actor, workspace_member) -> bool:
    """
    @description Whether the actor runs a queue this person works in. That is
    who legitimately records somebody else's absence: their coordinator has to
    plan around it, and a coordinator of an area they have nothing to do with
    does not.
    @param actor: The requesting user.
    @param workspace_member: The person the absence is about.
    @returns: ``True`` when the actor coordinates at least one of their areas.
    """
    unit_ids = OrganizationalUnitMembership.objects.filter(
        workspace_member=workspace_member, is_active=True
    ).values_list("organizational_unit_id", flat=True)
    if not unit_ids:
        return False

    return any(
        is_unit_coordinator(actor, unit)
        for unit in OrganizationalUnit.objects.filter(pk__in=list(unit_ids), is_active=True)
    )


class MyAvailabilityEndpoint(AvailabilityFeatureMixin, BaseAPIView):
    """The requesting person's own absences."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        member = WorkspaceMember.objects.filter(workspace__slug=slug, member=request.user, is_active=True).first()
        if member is None:
            return orca_not_found("ORG_WORKSPACE_MEMBER_NOT_FOUND")

        rows = WorkspaceMemberAvailability.objects.filter(workspace_member=member).select_related(
            "workspace_member__member"
        )
        return Response(WorkspaceMemberAvailabilitySerializer(rows, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def post(self, request, slug):
        member = WorkspaceMember.objects.filter(workspace__slug=slug, member=request.user, is_active=True).first()
        if member is None:
            return orca_not_found("ORG_WORKSPACE_MEMBER_NOT_FOUND")
        return _create_absence(request, member)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def delete(self, request, slug, pk):
        row = WorkspaceMemberAvailability.objects.filter(
            pk=pk, workspace__slug=slug, workspace_member__member=request.user
        ).first()
        if row is None:
            return orca_not_found("ORG_AVAILABILITY_NOT_FOUND")

        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemberAvailabilityEndpoint(AvailabilityFeatureMixin, BaseAPIView):
    """Somebody else's absences, for the people who have to plan around them."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, workspace_member_id):
        member = WorkspaceMember.objects.filter(pk=workspace_member_id, workspace__slug=slug).first()
        if member is None:
            return orca_not_found("ORG_WORKSPACE_MEMBER_NOT_FOUND")
        if not self._may_read(request.user, member):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        rows = WorkspaceMemberAvailability.objects.filter(workspace_member=member).select_related(
            "workspace_member__member"
        )
        return Response(WorkspaceMemberAvailabilitySerializer(rows, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def post(self, request, slug, workspace_member_id):
        member = WorkspaceMember.objects.filter(pk=workspace_member_id, workspace__slug=slug, is_active=True).first()
        if member is None:
            return orca_not_found("ORG_WORKSPACE_MEMBER_NOT_FOUND")
        if not self._may_read(request.user, member):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        return _create_absence(request, member)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def delete(self, request, slug, workspace_member_id, pk):
        row = WorkspaceMemberAvailability.objects.filter(
            pk=pk, workspace__slug=slug, workspace_member_id=workspace_member_id
        ).first()
        if row is None:
            return orca_not_found("ORG_AVAILABILITY_NOT_FOUND")
        if not self._may_read(request.user, row.workspace_member):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _may_read(self, actor, workspace_member) -> bool:
        if workspace_member.member_id == actor.id:
            return True
        if is_workspace_admin(actor, workspace_member.workspace_id):
            return True
        return _coordinates_any_area_of(actor, workspace_member)


def _create_absence(request, workspace_member):
    """
    @description The body of both POSTs. ``source`` is always ``manual`` here:
    the column exists so an HR or directory import can be told apart later, and
    letting a payload claim to be an import would break exactly that.
    @param request: The DRF request.
    @param workspace_member: Who the absence is about.
    @returns: The created row, or the standard error body.
    """
    unavailable_from = parse_datetime(str(request.data.get("unavailable_from") or ""))
    if unavailable_from is None:
        return orca_error("ORG_AVAILABILITY_INTERVAL_INVALID")

    raw_until = request.data.get("unavailable_until")
    unavailable_until = parse_datetime(str(raw_until)) if raw_until else None
    if raw_until and unavailable_until is None:
        return orca_error("ORG_AVAILABILITY_INTERVAL_INVALID")
    if unavailable_until is not None and unavailable_until <= unavailable_from:
        return orca_error("ORG_AVAILABILITY_INTERVAL_INVALID")

    reason = request.data.get("reason") or AvailabilityReason.OTHER
    if reason not in AvailabilityReason.values:
        return orca_error("ORG_AVAILABILITY_INTERVAL_INVALID")

    row = WorkspaceMemberAvailability.objects.create(
        workspace_member=workspace_member,
        workspace_id=workspace_member.workspace_id,
        unavailable_from=unavailable_from,
        unavailable_until=unavailable_until,
        reason=reason,
        source=AvailabilitySource.MANUAL,
        created_by=request.user,
    )
    return Response(WorkspaceMemberAvailabilitySerializer(row).data, status=status.HTTP_201_CREATED)


class MembershipAllocationEndpoint(AvailabilityFeatureMixin, BaseAPIView):
    """How much work one area gives one of its people."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id, pk):
        membership = self._membership(slug, unit_id, pk)
        if membership is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        row = MembershipAllocationSettings.objects.filter(membership=membership).first()
        if row is None:
            # The defaults, rather than 404: "nobody has touched this" and
            # "takes everything, no ceiling" are the same state, and a form
            # that has to handle both says so twice.
            return Response(
                {"membership": str(membership.id), "accepts_new_work": True, "max_open_items": None},
                status=status.HTTP_200_OK,
            )
        return Response(MembershipAllocationSettingsSerializer(row).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def put(self, request, slug, unit_id, pk):
        membership = self._membership(slug, unit_id, pk)
        if membership is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        unit = membership.organizational_unit
        may_coordinate = is_unit_coordinator(request.user, unit) or is_workspace_admin(request.user, unit.workspace_id)
        is_themself = membership.workspace_member.member_id == request.user.id
        if not (may_coordinate or is_themself):
            return orca_error("ORG_EXECUTOR_NOT_ELIGIBLE", status.HTTP_403_FORBIDDEN)

        row, _ = MembershipAllocationSettings.objects.get_or_create(
            membership=membership, defaults={"workspace_id": unit.workspace_id}
        )

        if "accepts_new_work" in request.data:
            row.accepts_new_work = bool(request.data.get("accepts_new_work"))

        if "max_open_items" in request.data:
            # The person's own "not right now" is theirs; the number is the
            # area's. Otherwise anybody could set it to one and stop receiving
            # work while still reading as available.
            if not may_coordinate:
                return orca_error("ORG_ALLOCATION_LIMIT_FORBIDDEN", status.HTTP_403_FORBIDDEN)
            limit = request.data.get("max_open_items")
            if limit in (None, ""):
                row.max_open_items = None
            else:
                try:
                    limit = int(limit)
                except (TypeError, ValueError):
                    return orca_error("ORG_INVALID_ASSIGNMENT_MODE")
                if limit < 1:
                    return orca_error("ORG_INVALID_ASSIGNMENT_MODE")
                row.max_open_items = limit

        row.save()
        return Response(MembershipAllocationSettingsSerializer(row).data, status=status.HTTP_200_OK)

    def _membership(self, slug, unit_id, pk):
        return (
            OrganizationalUnitMembership.objects.select_related("organizational_unit", "workspace_member")
            .filter(pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug, is_active=True)
            .first()
        )
