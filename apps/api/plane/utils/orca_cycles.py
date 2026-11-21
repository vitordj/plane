# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Orca's notion of a completed cycle, for the places upstream still reads ``end_date``.

The fork allows start-only cycles ("parallel cycles"): several cycles can run
at once and one ends only when somebody ends it, which is recorded in
``view_props["completed"]``. The cycle list derives its ``status`` from that
(``plane.app.views.cycle.base``), but upstream's archive endpoints decide
"completed" from ``end_date`` alone, so a start-only cycle that was ended by
hand could not be archived. This module is the single definition they consult.
"""


def orca_start_only_cycle_is_completed(cycle):
    """
    @description Whether a cycle without an ``end_date`` has been completed by
    hand, using the same rule as the ``status`` annotation of the cycle list:
    when the project auto-completes cycles, only a passed ``end_date`` ends
    one (so a start-only cycle is never completed); otherwise
    ``view_props["completed"]`` does.
    @param cycle: A ``Cycle`` instance.
    @returns ``True`` only for a cycle with no ``end_date`` that the fork
        considers completed; ``False`` for any cycle that has an ``end_date``.
    """
    if cycle.end_date is not None:
        return False
    custom_settings = getattr(cycle.project, "custom_settings", None)
    if custom_settings is not None and custom_settings.cycle_auto_complete:
        return False
    return (cycle.view_props or {}).get("completed") is True
