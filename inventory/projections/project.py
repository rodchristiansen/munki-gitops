#!/usr/bin/env python3
"""Project one inventory export into a narrowed CSV per downstream system.

Inventory holds one row per device. No downstream system wants the whole row --
each gets only the columns it can act on, and (where relevant) only the rows for
the platform it manages.

    python3 projections/project.py inventory.csv --out-dir out/

Read the column contract in ../README.md before adding a column here. A column
that nothing consumes does not belong in the contract.
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys

# Column sets per target. Order is the output column order.
COLUMNS: dict[str, list[str]] = {
    "munki": [
        "serial", "catalog", "area", "location", "asset", "usage",
        "status", "allocation", "username", "platform", "hostname", "fleet",
    ],
    "cimian": [
        "serial", "catalog", "area", "location", "asset", "usage",
        "status", "allocation", "username", "platform", "hostname", "fleet",
    ],
    "intune": [
        "serial", "catalog", "area", "location", "asset", "usage",
        "status", "allocation", "username", "platform", "hostname",
    ],
    "mdm": [
        "serial", "platform", "usage", "status", "catalog",
    ],
}

# Targets that only care about one platform. Omit a target here to send it
# every row regardless of platform.
PLATFORM: dict[str, str] = {
    "munki": "Macintosh",
    "cimian": "Windows",
}


def project(rows: list[dict], target: str) -> list[dict]:
    keep = COLUMNS[target]
    want = PLATFORM.get(target)
    out = []
    for row in rows:
        if want and row.get("platform") != want:
            continue
        out.append({col: row.get(col, "") for col in keep})
    return out


def write(path: pathlib.Path, target: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS[target])
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inventory", type=pathlib.Path)
    ap.add_argument("--out-dir", type=pathlib.Path, default=pathlib.Path("out"))
    ap.add_argument("--targets", nargs="*", default=sorted(COLUMNS))
    args = ap.parse_args(argv)

    with args.inventory.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    missing = set(COLUMNS["munki"]) - set(rows[0]) if rows else set()
    if missing:
        print(f"error: inventory is missing columns: {sorted(missing)}", file=sys.stderr)
        return 1

    for target in args.targets:
        if target not in COLUMNS:
            print(f"error: unknown target {target!r}", file=sys.stderr)
            return 1
        projected = project(rows, target)
        dest = args.out_dir / f"{target}.csv"
        write(dest, target, projected)
        print(f"{dest}: {len(projected)} row(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
