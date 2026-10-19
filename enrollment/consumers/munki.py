"""Push the Munki projection into the Munki repo and the repo's blob storage.

The client reads computers.csv to learn its own catalog/area/usage, so this
consumer's whole job is: put the approved projection where the fleet can see it,
and invalidate the CDN path so it is visible now rather than at TTL.

`shared` comes from intune-gitops, so put both enrollment directories on
PYTHONPATH, this repo's first:

    PYTHONPATH="enrollment:../intune-gitops/enrollment" python3 -m consumers.munki out/munki.csv --what-if

A trigger in intune-gitops reaches it with
ENROLLMENT_CONSUMERS="munki=consumers.munki:converge".
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import pathlib

from shared import csvdiff

log = logging.getLogger(__name__)

REPO_PATH = os.getenv("MUNKI_COMPUTERS_CSV_PATH", "deployment/enroll/computers.csv")

# The published column order, the one the preflight reads by header name. A
# projection that arrives in any other shape is bad input, not something to
# publish and let the fleet discover.
EXPECTED_HEADER = [
    "serial", "catalog", "area", "location", "asset", "usage",
    "status", "allocation", "username", "platform", "hostname", "fleet",
]

# Refuse to publish fewer rows than this. Set it from your own fleet size: a
# projection that suddenly shrinks is a broken export far more often than a
# fleet that halved overnight.
MIN_ROWS = int(os.getenv("MUNKI_MIN_ROWS", "1"))


def converge(csv_text: str, *, what_if: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")
    header = next(csv.reader(io.StringIO(csv_text)), [])
    if header != EXPECTED_HEADER:
        log.error("Refusing to publish: header is %s, expected %s.", header, EXPECTED_HEADER)
        return 1
    rows = csvdiff.parse(csv_text)
    if len(rows) < max(MIN_ROWS, 1):
        log.error("Refusing to publish %d row(s); the floor is %d.", len(rows), max(MIN_ROWS, 1))
        return 1

    log.info("%d device row(s) for the Munki repo.", len(rows))

    if what_if:
        log.info("[WHATIF] would write %s (%d rows)", REPO_PATH, len(rows))
        log.info("[WHATIF] would purge CDN path /%s", REPO_PATH)
        return 0

    # Left as integration points on purpose -- every org's repo host and CDN
    # differ, and neither belongs in a sample that writes to production.
    #   push_file_to_repo(REPO_PATH, csv_text)
    #   upload_blob(container="repo", path=REPO_PATH, data=csv_text.encode())
    #   purge_cdn_path(f"/{REPO_PATH}")
    log.warning("Publish steps are integration points; wire them for your repo host.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", type=pathlib.Path)
    ap.add_argument("--what-if", action="store_true")
    args = ap.parse_args(argv)
    return converge(args.csv.read_text(), what_if=args.what_if)


if __name__ == "__main__":
    raise SystemExit(main())
