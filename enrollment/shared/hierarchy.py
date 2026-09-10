"""The naming convention shared by inventory, Entra groups and manifest paths.

This module is the single definition of how four inventory columns become a
ladder of group names and a manifest path. Everything else in the kit derives
targeting from here; nothing else should build a group name by hand.
"""
from __future__ import annotations

import os
import re

GROUP_PREFIX = os.getenv("INTUNE_GROUP_PREFIX", "Devices")

# Inventory statuses meaning "this device should not exist in any system".
RETIRE_STATUSES = {"Retired", "Lost", "Stolen", "Returned Lease End", "Disposed"}

# Top-level usages that own a manifest tree. A row with any other usage has no
# manifest and no group ladder below Devices-All.
USAGE_ROOTS = {"Assigned", "Shared"}


def sanitize(component: str | None) -> str:
    """Group-name components carry no spaces or punctuation.

    Removing rather than replacing is deliberate: "Design Studio",
    "DesignStudio" and "Design-Studio" collapse to one group, so a stray space
    in inventory cannot silently fork a group.
    """
    return re.sub(r"[^A-Za-z0-9]+", "", (component or "").strip())


def group_ladder(row: dict) -> list[str]:
    """Five nested group names, broadest to narrowest.

    Devices-All
    Devices-{usage}
    Devices-{usage}-{catalog}
    Devices-{usage}-{catalog}-{area}
    Devices-{usage}-{catalog}-{area}-{location}

    A device is placed in every rung, so a policy can address "every device",
    "every shared device", or "the machines in one room" without inventing a
    new targeting mechanism.
    """
    usage = sanitize(row.get("usage"))
    catalog = sanitize(row.get("catalog"))
    area = sanitize(row.get("area"))
    location = sanitize(row.get("location"))

    ladder = [GROUP_PREFIX + "-All"]
    if not usage:
        return ladder
    parts = [usage]
    ladder.append(f"{GROUP_PREFIX}-" + "-".join(parts))
    for component in (catalog, area, location):
        if not component:
            break
        parts.append(component)
        ladder.append(f"{GROUP_PREFIX}-" + "-".join(parts))
    return ladder


def manifest_path(row: dict) -> str | None:
    """The manifest a row's devices read: {usage}/{catalog}/{area}.yaml."""
    usage = (row.get("usage") or "").strip()
    if usage not in USAGE_ROOTS:
        return None
    catalog = (row.get("catalog") or "").strip()
    area = (row.get("area") or "").strip()
    parts = [p for p in (usage, catalog, area) if p]
    return "/".join(parts) + ".yaml"


def manifest_path_to_group(short_path: str) -> str | None:
    """Inverse of manifest_path: manifests/Assigned/Staff/IT.yaml -> group.

    Returns None for a manifest that does not map to a group -- anything
    outside the usage roots, and hostname-level manifests deeper than the
    room, for which no group exists.
    """
    path = short_path.replace("\\", "/").removeprefix("manifests/")
    for suffix in (".yaml", ".yml"):
        path = path.removesuffix(suffix)
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] not in USAGE_ROOTS:
        return None
    if len(parts) > 4:
        parts = parts[:4]
    return f"{GROUP_PREFIX}-" + "-".join(sanitize(p) for p in parts)


def is_descendant(child_group: str, ancestor_group: str) -> bool:
    """Group ancestry is a prefix check, because names mirror the hierarchy."""
    return child_group.startswith(ancestor_group + "-")


def should_exist(row: dict) -> bool:
    """Whether this device should be present in downstream systems at all."""
    return (row.get("status") or "").strip() not in RETIRE_STATUSES
