#!/usr/bin/env python3
"""Write the plan to the tenant.

Read stages/plan_assignments.py first -- this stage does nothing the plan did
not already show you.

The one sentence that governs this file: the assignment API REPLACES the whole
assignment set. You do not add a group to a policy; you send the complete list
of who it applies to, and whatever you send becomes the truth. Sending an empty
list is a successful call that unassigns the policy from every device.

That is also exactly what this code produces if the manifest walk returns
nothing. Hence the floor, and hence whatIf being a supported way to run rather
than a debug flag.

    WHATIF=true python3 -m stages.apply_assignments manifests/
"""
from __future__ import annotations

import argparse
import collections
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "enrollment"))

from lib import guards  # noqa: E402
from shared.graph import GraphClient  # noqa: E402
from stages.plan_assignments import build_plan, resolve_exclusions  # noqa: E402

log = logging.getLogger(__name__)

MANAGED_MARKER = os.getenv("INTUNE_MANAGED_MARKER", "managed-by-gitops")

# Only objects carrying the marker are ever modified. Everything else in the
# tenant belongs to somebody else -- created in the portal, inherited from a
# migration, deliberately hand-managed. A pipeline that reconciles "the tenant
# should contain exactly what my repo contains" deletes all of it on first run.
def is_ours(policy: dict) -> bool:
    return MANAGED_MARKER in (policy.get("description") or "")


def load_protected() -> set[str]:
    """Profiles another pipeline owns, which this one must not touch.

    Two pipelines that both do a full replace on the same object take turns
    winning. When two automated systems can write the same object, one of them
    has to be able to name what it does not own, in a file the other reads.
    """
    path = pathlib.Path(os.getenv("PROTECTED_PROFILES", "protected-profiles.yaml"))
    if not path.exists():
        return set()
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    names: set[str] = set()
    for section in ("All", "Assigned", "Shared"):
        for identifier in data.get(section) or []:
            if isinstance(identifier, str) and identifier.strip():
                names.add(identifier.strip().rsplit(".", 1)[-1])
    return names


def apply(root: pathlib.Path, *, what_if: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")

    assignments, exclusions, skipped = build_plan(root)
    if skipped:
        log.error("%d manifest entr(ies) could not be rendered. Run the linter; "
                  "refusing to apply a partial plan.", len(skipped))
        return 1

    live_exclusions, inert = resolve_exclusions(assignments, exclusions)
    for name, group in inert:
        log.info("Inert exclusion, not sent: %s from %s (not inherited there).", name, group)

    guards.assignment_floor(len(assignments))

    client = GraphClient(what_if=what_if)
    if not client.token:
        log.error("No GRAPH_TOKEN. Run stages.plan_assignments for an offline plan.")
        return 1

    protected = load_protected()
    if protected:
        log.info("Owned by another pipeline, skipped here: %s", ", ".join(sorted(protected)))

    # name -> [(group, filter_rule)]
    wanted: dict[str, list[tuple[str, str | None]]] = collections.defaultdict(list)
    for a in assignments:
        if a.key != "managed_profiles":
            continue
        if a.name in protected:
            continue
        wanted[a.name].append((a.group, a.filter_rule))

    excluded_by_name: dict[str, list[str]] = collections.defaultdict(list)
    for name, group in live_exclusions:
        excluded_by_name[name].append(group)

    existing = {
        p.get("displayName"): p
        for p in client.paged("/deviceManagement/deviceConfigurations?$top=999")
        if is_ours(p)
    }

    applied = missing = 0
    for name, targets in sorted(wanted.items()):
        policy = existing.get(name)
        if not policy:
            log.warning("Manifest references profile '%s' but no policy carrying "
                        "the managed marker exists in the tenant.", name)
            missing += 1
            continue

        spec: list[dict] = []
        for group, filter_rule in targets:
            gid = client.group_id(group)
            if not gid:
                log.warning("  group %s not found, skipping that target.", group)
                continue
            target: dict = {
                "@odata.type": "#microsoft.graph.groupAssignmentTarget",
                "groupId": gid,
            }
            if filter_rule:
                # Filters are created on demand elsewhere and reused; a real
                # run resolves the rule to a filter id here.
                target["deviceAndAppManagementAssignmentFilterType"] = "include"
            spec.append({"target": target})

        for group in excluded_by_name.get(name, []):
            gid = client.group_id(group)
            if gid:
                spec.append({"target": {
                    "@odata.type": "#microsoft.graph.exclusionGroupAssignmentTarget",
                    "groupId": gid,
                }})

        if not spec:
            log.error("  %s resolved to no targets -- refusing to send an empty "
                      "assignment set.", name)
            continue

        log.info("%s -> %d target(s), %d exclusion(s)",
                 name, len(targets), len(excluded_by_name.get(name, [])))
        client.post(
            f"/deviceManagement/deviceConfigurations/{policy['id']}/assign",
            json={"assignments": spec},
        )
        applied += 1

    log.info("Applied %d profile assignment(s); %d referenced but absent from the tenant.",
             applied, missing)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifests", nargs="?", type=pathlib.Path,
                    default=pathlib.Path("manifests"))
    ap.add_argument("--what-if", action="store_true")
    args = ap.parse_args(argv)
    what_if = args.what_if or os.getenv("WHATIF", "").strip().lower() == "true"
    return apply(args.manifests, what_if=what_if)


if __name__ == "__main__":
    raise SystemExit(main())
