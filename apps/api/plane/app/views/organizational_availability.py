# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Absences and per-area limits over HTTP (RFC §8.1, item 3.3).

Three surfaces, and who may write each one is the whole design:

* **your own absences** — anybody, for themselves. Recording a holiday is not
  an administrative act, and a layer that made people ask their coordinator to
  mark them away would simply not be used;
* **somebody else's absences** — a coordinator of any area that person belongs
  to, or a workspace Admin. Any area, not the area: somebody's holiday is not
  per-area, so requiring the "right" coordinator would mean whoever noticed
  first often cannot record it;
* **what one area may put on somebody** — that area's coordinator or an Admin,
  with one exception: the person themselves may stop accepting new work. They
  may not set their own numeric ceiling, because that is the area's capacity
  planning rather than a personal preference.

Everything here is behind ``ORCA_AVAILABILITY_ENABLED`` as well as the layer's
own kill switch, and answers 404 when it is off — the same shape the rest of
the layer uses for a feature that is absent rather than forbidden.
"""

# Django imports
from django.db import transaction
from django.http import Http404
from django.utils.dateparse import parse_datetime

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission, is_unit_coordinator, is_workspace_admin, unit_capabilities
from plane.app.serializers import (
    MembershipAllocationSettingsSerializer,
    WorkspaceMemberAvailabilitySerializer,
)
from plane.app.services.orca import orca_availability_enabled, unavailable_window
from plane.db.models import (
    MembershipAllocationSettings,
    OrganizationalUnit,
    OrganizationalUnitMembership,
    UnavailabilityReason,
    WorkspaceMember,
    WorkspaceMemberAvailability,
)
from plane.utils.orca_error_codes import orca_error, orca_not_found

from .base import BaseAPIView
from .organizational_unit import OrganizationalUnitFeatureMixin


class OrcaAvailabilityFeatureMixin(OrganizationalUnitFeatureMixin):
    """
    The third switch, in front of these routes only.

    @description ``ORCA_AVAILABILITY_ENABLED=0`` means the instance has not
    adopted absences: the ranking ignores them and the sweep writes nothing, so
    an endpoint that kept accepting them would be collecting data the product
    does not act on. 404, not 403 — the feature is absent, not forbidden.
    """

    def initial(self, request, *args, **kwargs):
        response = super().initial(request, *args, **kwargs)
        if not orca_availability_enabled():
            raise Http404("Availability is disabled on this instance")
        return response


def _parse_window(data):
    """
    @description Read the two instants of an absence out of a request body.
    @param data: The request body.
    @returns ``(unavailable_from, unavailable_until, error_response)``. The
        error is ``None`` when the window is usable; ``unavailable_until`` is
        ``None`` for an open-ended absence, which is allowed and means
        indefinite.
    """
    raw_from = data.get("unavailable_from")
    raw_until = data.get("unavailable_until")

    starts = parse_datetime(raw_from) if isinstance(raw_from, str) else None
    if starts is None:
        return None, None, orca_error("ORG_INVALID_AVAILABILITY_WINDOW")

    ends = None
    if isinstance(raw_until, str) and raw_until.strip():
        ends = parse_datetime(raw_until)
        if ends is None or ends <= starts:
            return None, None, orca_error("ORG_INVALID_AVAILABILITY_WINDOW")

    return starts, ends, None


def _reason_of(data) -> str:
    """@description The absence's reason, defaulting to vacation. @returns str."""
    reason = data.get("reason")
    return reason if reason in UnavailabilityReason.values else UnavailabilityReason.VACATION


class OrcaMyAvailabilityEndpoint(OrcaAvailabilityFeatureMixin, BaseAPIView):
    """
    ``GET/POST/DELETE /api/orca/workspaces/{slug}/availability/me/``

    @description Your own absences. ``POST`` records one, ``DELETE`` with an
    ``id`` removes one, and ``GET`` lists them with the one covering right now
    marked, which is what the profile screen shows.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug):
        member = WorkspaceMember.objects.filter(workspace__slug=slug, member=request.user, is_active=True).first()
        if member is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        windows = WorkspaceMemberAvailability.objects.filter(workspace_member=member).order_by("-unavailable_from")
        current = unavailable_window(member.id)
        return Response(
            {
                "available": current is None,
                "current": WorkspaceMemberAvailabilitySerializer(current).data if current else None,
                "windows": WorkspaceMemberAvailabilitySerializer(windows, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def post(self, request, slug):
        member = WorkspaceMember.objects.filter(workspace__slug=slug, member=request.user, is_active=True).first()
        if member is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        starts, ends, error = _parse_window(request.data)
        if error is not None:
            return error

        window = WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            unavailable_from=starts,
            unavailable_until=ends,
            reason=_reason_of(request.data),
            created_by=request.user,
        )
        return Response(WorkspaceMemberAvailabilitySerializer(window).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def delete(self, request, slug):
        window = WorkspaceMemberAvailability.objects.filter(
            pk=request.query_params.get("id") or request.data.get("id"),
            workspace_member__member=request.user,
            workspace__slug=slug,
        ).first()
        if window is None:
            return orca_not_found("ORG_AVAILABILITY_NOT_FOUND")

        window.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrcaMemberAvailabilityEndpoint(OrcaAvailabilityFeatureMixin, BaseAPIView):
    """
    ``GET/POST/DELETE .../members/{workspace_member_id}/availability/``

    @description Somebody else's absences, for a coordinator of any area they
    belong to or a workspace Admin. "Any area" is deliberate: an absence is not
    per-area, so demanding the right coordinator would leave the person who
    noticed unable to record it.
    """

    def _may_manage(self, user, member) -> bool:
        """@description Whether this caller may record absences for that person. @returns bool."""
        if is_workspace_admin(user, workspace_id=member.workspace_id):
            return True
        units = OrganizationalUnit.objects.filter(
            memberships__workspace_member=member, memberships__is_active=True
        ).distinct()
        return any(is_unit_coordinator(user, unit) for unit in units)

    def _member(self, slug, workspace_member_id):
        return WorkspaceMember.objects.filter(pk=workspace_member_id, workspace__slug=slug, is_active=True).first()

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, workspace_member_id):
        member = self._member(slug, workspace_member_id)
        if member is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")
        if not self._may_manage(request.user, member):
            return orca_error("ORG_UNIT_PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)

        windows = WorkspaceMemberAvailability.objects.filter(workspace_member=member).order_by("-unavailable_from")
        current = unavailable_window(member.id)
        return Response(
            {
                "available": current is None,
                "current": WorkspaceMemberAvailabilitySerializer(current).data if current else None,
                "windows": WorkspaceMemberAvailabilitySerializer(windows, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def post(self, request, slug, workspace_member_id):
        member = self._member(slug, workspace_member_id)
        if member is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")
        if not self._may_manage(request.user, member):
            return orca_error("ORG_UNIT_PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)

        starts, ends, error = _parse_window(request.data)
        if error is not None:
            return error

        window = WorkspaceMemberAvailability.objects.create(
            workspace_member=member,
            unavailable_from=starts,
            unavailable_until=ends,
            reason=_reason_of(request.data),
            created_by=request.user,
        )
        return Response(WorkspaceMemberAvailabilitySerializer(window).data, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def delete(self, request, slug, workspace_member_id):
        member = self._member(slug, workspace_member_id)
        if member is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")
        if not self._may_manage(request.user, member):
            return orca_error("ORG_UNIT_PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)

        window = WorkspaceMemberAvailability.objects.filter(
            pk=request.query_params.get("id") or request.data.get("id"), workspace_member=member
        ).first()
        if window is None:
            return orca_not_found("ORG_AVAILABILITY_NOT_FOUND")

        window.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrcaMembershipAllocationEndpoint(OrcaAvailabilityFeatureMixin, BaseAPIView):
    """
    ``GET/PUT .../organizational-units/{unit_id}/members/{pk}/allocation/``

    @description What this area may put on this person: whether its ranking may
    pick them at all, and their own ceiling on open work.

    The coordinator (or an Admin) may set both. The person themselves may only
    turn ``accepts_new_work`` off — saying "not more right now" is theirs to
    say, while a number that shapes how the area distributes work is the area's
    to decide. Turning it back *on* is also theirs: it is the same statement in
    the other direction, and needing a coordinator to undo it would make people
    reluctant to use it in the first place.
    """

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def get(self, request, slug, unit_id, pk):
        membership = self._membership(slug, unit_id, pk)
        if membership is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        settings_row = MembershipAllocationSettings.objects.filter(membership=membership).first()
        return Response(self._payload(membership, settings_row), status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
    def put(self, request, slug, unit_id, pk):
        membership = self._membership(slug, unit_id, pk)
        if membership is None:
            return orca_not_found("ORG_UNIT_MEMBERSHIP_NOT_FOUND")

        unit = membership.organizational_unit
        coordinates = unit_capabilities(request.user, unit)["can_assign"]
        is_self = membership.workspace_member.member_id == request.user.id
        if not (coordinates or is_self):
            return orca_error("ORG_UNIT_PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)

        wants_limit = "max_open_items" in request.data
        if wants_limit and not coordinates:
            return orca_error("ORG_ALLOCATION_LIMIT_FORBIDDEN", status.HTTP_403_FORBIDDEN)

        limit = None
        if wants_limit:
            raw = request.data.get("max_open_items")
            if raw not in (None, ""):
                try:
                    limit = int(raw)
                except (TypeError, ValueError):
                    return orca_error("ORG_POLICY_INVALID_VALUE")
                if limit <= 0:
                    return orca_error("ORG_POLICY_INVALID_VALUE")

        with transaction.atomic():
            settings_row, _ = MembershipAllocationSettings.objects.select_for_update().get_or_create(
                membership=membership, defaults={"created_by": request.user}
            )
            if "accepts_new_work" in request.data:
                settings_row.accepts_new_work = bool(request.data.get("accepts_new_work"))
            if wants_limit:
                settings_row.max_open_items = limit
            settings_row.save()

        return Response(self._payload(membership, settings_row), status=status.HTTP_200_OK)

    def _membership(self, slug, unit_id, pk):
        return (
            OrganizationalUnitMembership.objects.filter(
                pk=pk, organizational_unit_id=unit_id, organizational_unit__workspace__slug=slug
            )
            .select_related("organizational_unit", "workspace_member")
            .first()
        )

    def _payload(self, membership, settings_row) -> dict:
        """
        @description The membership's allocation settings, with the defaults
        spelled out when no row exists — a client should not have to know that
        "absent" means "accepting, no limit".
        @returns dict.
        """
        return {
            "membership": str(membership.id),
            "member_id": str(membership.workspace_member.member_id),
            "accepts_new_work": True if settings_row is None else settings_row.accepts_new_work,
            "max_open_items": None if settings_row is None else settings_row.max_open_items,
            "settings": MembershipAllocationSettingsSerializer(settings_row).data if settings_row else None,
        }
