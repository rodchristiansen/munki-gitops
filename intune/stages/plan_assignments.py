#!/usr/bin/env python3
"""Resolve a manifest tree into the assignments it implies. No tenant needed.

This is the whole kit in one command: manifest path becomes group, the three
MDM-rendered keys become assignments against those groups, conditions become
assignment filters, and managed_uninstalls become exclusions where they are
actually inherited.

Run it before anything writes. It is the artefact to read in a pull request.

    python3 -m stages.plan_assignments manifests/
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import guards  # noqa: E402
from lib.conditions import CondParseError, UnsupportedCondition, translate  # noqa: E402
from lib.manifests import is_descendant, load_tree, walk_managed_key  # noqa: E402

RENDERED_KEYS = ("managed_profiles", "managed_apps", "managed_scripts")


class Assignment:
    def __init__(self, key, name, group, cond=None, filter_rule=None):
        self.key, self.name, self.group = key, name, group
        self.cond, self.filter_rule = cond, filter_rule

    def __repr__(self):
        return f"{self.key}:{self.name}->{self.group}"


def build_plan(root: pathlib.Path):
    """Return (assignments, exclusions, skipped)."""
    assignments: list[Assignment] = []
    exclusions: list[tuple[str, str]] = []   # (name, group asking to opt out)
    skipped: list[tuple[str, str, str]] = []  # (name, cond, why)

    for _path, group, data in load_tree(root):
        for key in RENDERED_KEYS:
            for name, cond in walk_managed_key(data, key):
                # The app stage reads only top-level managed_apps. The linter
                # fails the build on a conditional one; this stage refuses to
                # pretend it worked.
                if key == "managed_apps" and cond:
                    skipped.append((name, cond, "managed_apps under a condition is never read"))
                    continue
                if not cond:
                    assignments.append(Assignment(key, name, group))
                    continue
                try:
                    _display, rule = translate(cond)
                except (UnsupportedCondition, CondParseError) as exc:
                    skipped.append((name, cond, str(exc)))
                    continue
                assignments.append(Assignment(key, name, group, cond, rule))

        for name, cond in walk_managed_key(data, "managed_uninstalls"):
            if not cond:
                exclusions.append((name, group))

    return assignments, exclusions, skipped


def resolve_exclusions(assignments, exclusions):
    """Keep only exclusions that actually subtract something.

    An exclusion is meaningful iff its group is a strict descendant of a group
    the item is genuinely assigned to. Anything else is inert -- and an
    exclusion list full of no-ops is a list nobody reads, which is how the one
    that matters gets missed.
    """
    reach = collections.defaultdict(set)
    for a in assignments:
        reach[a.name].add(a.group)

    live, inert = [], []
    for name, group in exclusions:
        if any(is_descendant(group, ancestor) for ancestor in reach.get(name, ())):
            live.append((name, group))
        else:
            inert.append((name, group))
    return live, inert


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifests", nargs="?", type=pathlib.Path,
                    default=pathlib.Path("manifests"))
    args = ap.parse_args(argv)

    assignments, exclusions, skipped = build_plan(args.manifests)
    live, inert = resolve_exclusions(assignments, exclusions)

    guards.assignment_floor(len(assignments))

    by_key = collections.defaultdict(list)
    for a in assignments:
        by_key[a.key].append(a)

    for key in RENDERED_KEYS:
        items = by_key.get(key, [])
        if not items:
            continue
        print(f"\n{key}  ({len(items)} assignment(s))")
        for a in sorted(items, key=lambda x: (x.name, x.group)):
            line = f"  {a.name:<26} -> {a.group}"
            if a.filter_rule:
                line += f"\n  {'':<26}    filter: {a.filter_rule}"
            print(line)

    if live:
        print(f"\nexclusions  ({len(live)} live)")
        for name, group in sorted(live):
            print(f"  {name:<26} -X {group}")
    if inert:
        print(f"\ninert exclusions  ({len(inert)}; not inherited there, nothing to subtract)")
        for name, group in sorted(inert):
            print(f"  {name:<26} -- {group}")

    if skipped:
        print(f"\nSKIPPED  ({len(skipped)}) -- the linter fails the build on these")
        for name, cond, why in skipped:
            print(f"  {name:<26} [{cond}]\n  {'':<26}    {why}")

    print(f"\n{len(assignments)} assignment(s), {len(live)} exclusion(s), "
          f"{len(skipped)} skipped.")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
