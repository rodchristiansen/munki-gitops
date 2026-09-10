"""Guards against the one failure this kit keeps re-learning.

Every stage derives a desired state by parsing something. A degraded parse does
not throw -- it yields an empty or tiny desired set, which is a well-formed
answer that happens to be wrong. Converging on it removes everything, cleanly,
on a green build.

Adding is safe. Removing is not. These guards only ever constrain removal.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


class GuardTripped(RuntimeError):
    """Raised when a run looks like a parse failure rather than a real change."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def desired_floor(total_desired: int, *, what: str, env: str, default: int) -> None:
    """Refuse to act when the desired set has collapsed.

    The parse fails atomically -- a broken tree or a bad path yields near-zero,
    never a healthy-looking subset -- so a floor comfortably below the real
    baseline but far above zero catches the failure without ever firing on a
    legitimate change.

    Set the floor from YOUR baseline and record that baseline next to it. A
    floor inherited from someone else's estate is a decoration.
    """
    floor = _env_int(env, default)
    if total_desired < floor:
        raise GuardTripped(
            f"{what}: desired set collapsed to {total_desired} (floor {floor}). "
            f"Refusing to converge -- this is a parse failure, not a fleet change. "
            f"Override with {env} if the floor is genuinely wrong."
        )


def removal_cap(
    to_remove: int,
    actual: int,
    *,
    what: str,
    allow_large_shrink: bool = False,
    env: str = "MAX_REMOVAL_PCT",
    default: float = 10.0,
) -> bool:
    """Whether a removal batch is within the allowed proportion.

    Returns True to proceed, False to skip this target. Skipping one suspicious
    target and continuing beats failing the whole run: the other four hundred
    still need converging.
    """
    if to_remove == 0 or actual == 0:
        return True
    cap = _env_float(env, default)
    pct = 100.0 * to_remove / actual
    if pct <= cap or allow_large_shrink:
        return True
    log.error(
        "%s: would remove %d of %d (%.1f%%, cap %.1f%%) -- skipping. "
        "Re-run with allowLargeShrink=true if this is intended.",
        what, to_remove, actual, pct, cap,
    )
    return False
