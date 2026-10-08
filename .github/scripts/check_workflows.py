#!/usr/bin/env python3
"""Lint the GitHub Actions workflows in this repo, samples included.

    python3 .github/scripts/check_workflows.py

Fails when:
  - a `uses:` is not pinned to a full 40-character commit SHA (local ./
    actions excepted). A tag can be moved to point at different code.
  - the workflow-level permissions grant id-token or any write scope. Grant
    those per job.
  - a job with id-token: write has no `environment:`. The environment is what
    the federated credential trusts, and where reviewers or branch rules stop
    an edited workflow from minting a token.
"""
from __future__ import annotations

import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
GLOBS = [".github/workflows/*.yml", "pipelines/github/*.yml", "intune/pipelines/github/*.yml"]
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def walk_uses(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "uses" and isinstance(v, str):
                out.append(v)
            else:
                walk_uses(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_uses(v, out)


def check(path: pathlib.Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    try:
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{rel}: not valid YAML ({exc})"]
    errors = []
    uses: list[str] = []
    walk_uses(wf.get("jobs", {}), uses)
    for u in uses:
        if u.startswith("./") or u.startswith("docker://"):
            continue
        if not PINNED.match(u):
            errors.append(f"{rel}: '{u}' is not pinned to a commit SHA")

    top = wf.get("permissions")
    if isinstance(top, dict):
        for scope, level in top.items():
            if scope == "id-token" or level == "write":
                errors.append(f"{rel}: workflow-level permissions grant {scope}: {level}; grant it on the job that needs it")
    elif top in ("write-all",):
        errors.append(f"{rel}: workflow-level permissions are write-all")
    elif top is None:
        errors.append(f"{rel}: no workflow-level permissions block; set one (contents: read or {{}})")

    for name, job in (wf.get("jobs") or {}).items():
        perms = (job or {}).get("permissions") or {}
        if isinstance(perms, dict) and perms.get("id-token") == "write" and not job.get("environment"):
            errors.append(f"{rel}: job '{name}' has id-token: write but no environment")
    return errors


def main() -> int:
    files = sorted({p for g in GLOBS for p in ROOT.glob(g)})
    errors = [e for f in files for e in check(f)]
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    print(f"Checked {len(files)} workflow file(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
