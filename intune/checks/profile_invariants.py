#!/usr/bin/env python3
"""Fail on catalog gaps or a bad PayloadVersion in the Intune-bound items.

    python3 intune/checks/profile_invariants.py

Run from the repo root. Munki promotes through Development, Testing, Staging
and Production in that order, so a declaration must be a prefix of that list:
`[Development, Testing]` is valid, `[Testing]` or `[Development, Staging]` is a
gap that strands the item in a catalog nobody tests from.

Every payload in a .mobileconfig also needs PayloadVersion as the integer 1.
macOS installs a profile whose PayloadVersion is a string or another number
from some sources and refuses it from others, so it fails here instead.

This is the Munki-only check the intune-gitops pipeline runs in its test stage.
"""
from __future__ import annotations

import pathlib
import plistlib
import sys

import yaml

WATERFALL = ["Development", "Testing", "Staging", "Production"]
YAML_ROOTS = ["intune/profiles", "pkgsinfo/apps/managed"]
PROFILE_ROOT = "intune/profiles"


def _waterfall(rel: str, catalogs) -> list[str]:
    if not catalogs or catalogs != WATERFALL[: len(catalogs)]:
        return [f"{rel}: catalogs {catalogs} must be a prefix of {WATERFALL}"]
    return []


def _payload_versions(rel: str, node) -> list[str]:
    found = []
    if isinstance(node, dict):
        if "PayloadType" in node:
            version = node.get("PayloadVersion")
            if type(version) is not int or version != 1:
                found.append(f"{rel}: {node.get('PayloadType')} has PayloadVersion {version!r}; it must be the integer 1")
        for child in node.values():
            found += _payload_versions(rel, child)
    elif isinstance(node, list):
        for child in node:
            found += _payload_versions(rel, child)
    return found


def problems(root: pathlib.Path) -> list[str]:
    found = []
    for base in YAML_ROOTS:
        for path in sorted((root / base).rglob("*.yaml")):
            rel = path.relative_to(root).as_posix()
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            found += _waterfall(rel, data.get("catalogs") or [])
    for path in sorted((root / PROFILE_ROOT).rglob("*.mobileconfig")):
        rel = path.relative_to(root).as_posix()
        try:
            profile = plistlib.loads(path.read_bytes())
        except Exception as exc:  # noqa: BLE001 -- any parse failure is a finding
            found.append(f"{rel}: not a readable property list ({exc})")
            continue
        found += _waterfall(rel, profile.get("Catalogs") or [])
        found += _payload_versions(rel, profile)
    return found


def main() -> int:
    found = problems(pathlib.Path.cwd())
    for line in found:
        print(f"error: {line}", file=sys.stderr)
    if not found:
        print("Catalogs are cumulative and every PayloadVersion is 1.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
