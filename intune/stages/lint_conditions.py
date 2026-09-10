#!/usr/bin/env python3
"""Fail the build on anything the assign stages cannot honour.

This stage exists because of one specific failure. The assign stage, meeting a
condition it could not translate, logged one INFO line and skipped the block.
That is the right behaviour there -- assigning without the filter would apply
the thing to every device in the group instead of the subset you meant. But a
skip is invisible: a phased rollout aimed by an untranslatable condition simply
did not happen, on a green build, with the only trace one line in the middle of
a few thousand.

So the fix is not in the assign stage. It is here, at the front of the pipeline,
making the situation unreachable.

Four rules:
  1. managed_profiles / managed_scripts under a condition with no filter
     equivalent -- the silent skip.
  2. A malformed condition.
  3. managed_apps nested under conditional_items -- the app stage reads only
     top-level managed_apps, so a conditional one is dropped without a word.
  4. A condition that translates but emits an operator the platform rejects.

    python3 -m stages.lint_conditions manifests/
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.conditions import CondParseError, UnsupportedCondition, translate  # noqa: E402
from lib.manifests import load_tree, walk_managed_key  # noqa: E402

FILTERED_KEYS = ("managed_profiles", "managed_scripts")

# Operators the platform rejects on device.model even though they translate.
REJECTED_OPERATORS = ("-notStartsWith", "-notContains")


class Finding:
    def __init__(self, path, key, name, cond, problem, note=""):
        self.path, self.key, self.name = path, key, name
        self.cond, self.problem, self.note = cond, problem, note

    def render(self) -> str:
        lines = [f"ERROR | {self.path}"]
        if self.cond:
            lines.append(f"      |   condition: {self.cond}")
        lines.append(f"      |   key: {self.key}" + (f" -> {self.name}" if self.name else ""))
        lines.append(f"      |   {self.problem}")
        if self.note:
            for extra in self.note.splitlines():
                lines.append(f"      |   {extra}")
        return "\n".join(lines)


# Munki still evaluates managed_installs locally, so an untranslatable
# condition breaks only the keys this pipeline renders. Saying so stops people
# ripping up conditions that work perfectly well.
STILL_FINE = ("The managed_installs in this block still work -- the client\n"
              "evaluates those on the device. Only the MDM-rendered keys are affected.")


def lint_tree(root: pathlib.Path) -> list[Finding]:
    findings: list[Finding] = []

    try:
        tree = load_tree(root)
    except ValueError as exc:
        return [Finding(str(root), "yaml", "", "", f"Manifest failed to parse: {exc}")]

    for path, _group, data in tree:
        rel = path.relative_to(root).as_posix()

        # Rules 1, 2 and 4: conditions attached to keys we render with filters.
        for key in FILTERED_KEYS:
            for name, cond in walk_managed_key(data, key):
                if not cond:
                    continue
                try:
                    _display, rule = translate(cond)
                except UnsupportedCondition as exc:
                    findings.append(Finding(
                        rel, key, name, cond,
                        f"Cannot translate: {exc}.", STILL_FINE))
                    continue
                except CondParseError as exc:
                    findings.append(Finding(
                        rel, key, name, cond,
                        f"Malformed condition: {exc}."))
                    continue
                for bad in REJECTED_OPERATORS:
                    if bad in rule:
                        findings.append(Finding(
                            rel, key, name, cond,
                            f"Translates to {bad}, which the platform rejects "
                            f"on device.model."))

        # Rule 3: managed_apps under a conditional block is silently dropped.
        for name, cond in walk_managed_key(data, "managed_apps"):
            if cond:
                findings.append(Finding(
                    rel, "managed_apps", name, cond,
                    "managed_apps under conditional_items is never read.",
                    "The app stage reads only top-level managed_apps, so this\n"
                    "entry would be dropped in silence. Move it to the top level,\n"
                    "or to a manifest whose group is the cohort you mean."))

    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifests", nargs="?", type=pathlib.Path,
                    default=pathlib.Path("manifests"))
    ap.add_argument("--warn-only", action="store_true",
                    help="report findings without failing the build")
    args = ap.parse_args(argv)

    findings = lint_tree(args.manifests)
    for finding in findings:
        print(finding.render())
        print()

    if not findings:
        print(f"OK    | {args.manifests}: every condition translates.")
        return 0

    print(f"{len(findings)} finding(s).")
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
