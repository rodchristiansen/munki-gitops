"""Turn an inventory projection into the Entra group ladder.

This is the hinge of the whole kit. Four inventory columns become five nested
groups; every policy, profile, app and script assignment in the estate is
addressed to one of those rungs.

Assigned membership, not dynamic rules, on purpose. A dynamic rule would
re-derive in a query language what inventory already knows, with no history, on
Entra's schedule, against attributes Entra holds rather than yours.

    python3 -m consumers.intune out/intune.csv --what-if
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from shared import csvdiff, guards  # noqa: E402
from shared.graph import GraphClient  # noqa: E402
from shared.hierarchy import group_ladder, should_exist  # noqa: E402

log = logging.getLogger(__name__)

MANAGED_MARKER = os.getenv("INTUNE_MANAGED_MARKER", "managed-by-gitops")
CACHE = pathlib.Path(os.getenv("ENROLLMENT_CACHE", ".cache")) / "intune.csv"


def desired_membership(rows: list[dict]) -> dict[str, set[str]]:
    """group name -> set of device serials that belong in it.

    Retired rows contribute nothing, which is what makes decommissioning a data
    edit rather than a checklist across five consoles.
    """
    desired: dict[str, set[str]] = {}
    for row in rows:
        if not should_exist(row):
            continue
        serial = (row.get("serial") or "").strip().upper()
        if not serial:
            continue
        for group in group_ladder(row):
            desired.setdefault(group, set()).add(serial)
    return desired


def resolve_devices(client: GraphClient, serials: set[str]) -> dict[str, str]:
    """serial -> Entra device object id, for the serials we care about.

    Where a serial has several records, prefer one that is not retired, then
    the most recently enrolled. Duplicates accumulate whenever a machine is
    reimaged or migrated, and they make every later count wrong.
    """
    found: dict[str, dict] = {}
    retired = {"retired", "deleted"}
    items = client.paged(
        "/deviceManagement/managedDevices"
        "?$select=id,azureADDeviceId,serialNumber,managementState,enrolledDateTime&$top=999"
    )
    for dev in items:
        serial = (dev.get("serialNumber") or "").strip().upper()
        if serial not in serials:
            continue
        existing = found.get(serial)
        if existing is None:
            found[serial] = dev
            continue
        state_now = (dev.get("managementState") or "").lower()
        state_old = (existing.get("managementState") or "").lower()
        if state_old in retired and state_now not in retired:
            found[serial] = dev
        elif state_now not in retired and dev.get("enrolledDateTime", "") > existing.get("enrolledDateTime", ""):
            found[serial] = dev
    return {s: d["azureADDeviceId"] for s, d in found.items() if d.get("azureADDeviceId")}


def converge(csv_text: str, *, what_if: bool = False, allow_large_shrink: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")
    for noisy in ("urllib3", "requests", "azure", "msal"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    rows = csvdiff.parse(csv_text)
    desired = desired_membership(rows)

    total = sum(len(v) for v in desired.values())
    guards.desired_floor(
        total, what="group membership",
        env="MIN_DESIRED_MEMBERSHIPS", default=10,
    )
    log.info("%d group(s), %d membership(s) desired from %d row(s).",
             len(desired), total, len(rows))

    client = GraphClient(what_if=what_if)

    # Planning must not require a tenant. With no token this prints the group
    # plan and stops, so someone who has just forked the kit can see exactly
    # what it would build before they hand it any credentials.
    if not client.token:
        log.info("No GRAPH_TOKEN -- planning offline, no calls will be made.")
        for group_name in sorted(desired):
            log.info("  %-46s %3d device(s)", group_name, len(desired[group_name]))
        log.info("Plan only: %d group(s) would be ensured, %d membership(s) set.",
                 len(desired), total)
        return 0

    every_serial = {s for members in desired.values() for s in members}
    serial_to_object = resolve_devices(client, every_serial)
    missing = every_serial - set(serial_to_object)
    if missing:
        log.warning("No directory object for %d serial(s): %s",
                    len(missing), ", ".join(sorted(missing)[:5]))

    skipped: list[str] = []
    for group_name in sorted(desired):
        gid = client.ensure_group(group_name, f"[{MANAGED_MARKER}] derived from inventory")
        if not gid:
            continue

        want = {serial_to_object[s] for s in desired[group_name] if s in serial_to_object}
        have = client.group_member_ids(gid)
        to_add, to_remove = want - have, have - want

        if not guards.removal_cap(
            len(to_remove), len(have), what=group_name,
            allow_large_shrink=allow_large_shrink,
        ):
            skipped.append(group_name)
            to_remove = set()

        for oid in sorted(to_add):
            client.add_member(gid, oid)
        for oid in sorted(to_remove):
            client.remove_member(gid, oid)

        if to_add or to_remove:
            log.info("%s: +%d / -%d", group_name, len(to_add), len(to_remove))

    if skipped:
        log.error("Skipped %d group(s) on the removal cap: %s",
                  len(skipped), ", ".join(skipped))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", type=pathlib.Path)
    ap.add_argument("--what-if", action="store_true")
    ap.add_argument("--allow-large-shrink", action="store_true")
    args = ap.parse_args(argv)
    return converge(
        args.csv.read_text(),
        what_if=args.what_if,
        allow_large_shrink=args.allow_large_shrink,
    )


if __name__ == "__main__":
    raise SystemExit(main())
