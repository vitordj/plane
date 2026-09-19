# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Shared shape rules for the ``/api/v1/orca/`` request bodies.

One rule, applied everywhere: **an unknown key is an error**. DRF's default is
to ignore fields it was not asked about, which is right for a form and wrong
for a machine contract — a client that sends ``asignees`` or
``assignment_due`` gets a 201 and believes the field took effect. The failure
then shows up days later as "the SLA is never set", with nothing in any log to
say why.
"""

# Third party imports
from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    """
    A serializer that refuses input it does not understand.

    @description Reports every unknown key at once, with the accepted ones
    listed, so a caller fixes one integration rather than one field per
    deploy. Nested serializers inherit this by being ``StrictSerializer``
    themselves — the check runs per block, and the error path names the block.
    """

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                accepted = ", ".join(sorted(self.fields))
                raise serializers.ValidationError(
                    {key: f"Unknown field. This block accepts: {accepted}." for key in unknown}
                )
        return super().to_internal_value(data)
