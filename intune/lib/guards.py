"""Assignment-side guards. See ../../enrollment/shared/guards.py for the
membership-side twin -- same idea, one layer down.

The sentence this file exists for: the assignment API REPLACES the whole
assignment set. Sending an empty list is a successful call that unassigns a
policy from every device in the fleet, and it is exactly what the code produces
when a manifest walk returns nothing.
"""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "enrollment"))

from shared.guards import GuardTripped, desired_floor  # noqa: E402,F401

__all__ = ["GuardTripped", "assignment_floor"]


def assignment_floor(total: int) -> None:
    """Refuse to assign when the manifest walk has collapsed.

    Set MIN_DESIRED_ASSIGNMENTS from your own baseline and record that baseline
    in a comment with a date.

    There is deliberately NO clear-ratio guard here -- no check on how much of
    what already exists in the tenant this run would clear. Measured against a
    live tenant, a healthy run clears roughly a third of existing macOS configs,
    because a tenant holds many policies no manifest assigns: portal-created,
    left over from a migration, deliberately unassigned. "Existing minus
    desired" is dominated by those, so a ratio on it fires constantly on healthy
    data while saying nothing about a collapse. The floor is the signal; the
    ratio was noise, and worse, it trained people to re-run with the override on.
    """
    desired_floor(
        total,
        what="profile assignments",
        env="MIN_DESIRED_ASSIGNMENTS",
        default=3,
    )
