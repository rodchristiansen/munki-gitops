"""Walk a manifest tree and yield what each key asks for, and where.

The path-to-group rule lives in ../../enrollment/shared/hierarchy.py and is
imported, not copied. One naming convention, one implementation -- the whole
architecture rests on a manifest path and a group name being the same address,
so there must not be two opinions about how that address is spelled.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "enrollment"))

from shared.hierarchy import (  # noqa: E402
    is_descendant,
    manifest_path_to_group,
)

__all__ = ["walk_managed_key", "load_tree", "manifest_path_to_group", "is_descendant"]

# Keys this kit renders into the MDM. Munki ignores all three.
DELIVERABLE_KEYS = ("managed_profiles", "managed_scripts", "managed_apps")


def walk_managed_key(data: dict, key: str, parent_cond: str | None = None):
    """Yield (name, condition_or_None) for every entry under `key`.

    Descends through conditional_items, combining nested conditions with AND.
    Used by the linter and by every assign stage, so they cannot disagree about
    what a manifest asks for.
    """
    top = data.get(key) or []
    if isinstance(top, list):
        for entry in top:
            if isinstance(entry, str):
                yield entry, parent_cond

    for block in data.get("conditional_items") or []:
        if not isinstance(block, dict):
            continue
        cond = (block.get("condition") or "").strip()
        if not cond:
            continue
        combined = f"({parent_cond}) AND ({cond})" if parent_cond else cond
        yield from walk_managed_key(block, key, combined)


def load_tree(root: pathlib.Path) -> list[tuple[pathlib.Path, str, dict]]:
    """Return (path, group_name, parsed) for every manifest that maps to a group.

    Manifests outside the usage roots -- CoreApps.yaml and friends -- have no
    group and are skipped here. They still matter to Munki; they just are not
    an address this pipeline can assign to.
    """
    out = []
    for path in sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml")):
        rel = path.relative_to(root).as_posix()
        group = manifest_path_to_group(rel)
        if not group:
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{rel}: {exc}") from exc
        if isinstance(data, dict):
            out.append((path, group, data))
    return out
