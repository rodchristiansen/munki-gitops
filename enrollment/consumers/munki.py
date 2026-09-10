"""Push the Munki projection into the Munki repo and the repo's blob storage.

The client reads computers.csv to learn its own catalog/area/usage, so this
consumer's whole job is: put the approved projection where the fleet can see it,
and invalidate the CDN path so it is visible now rather than at TTL.

    python3 -m consumers.munki out/munki.csv --what-if
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from shared import csvdiff  # noqa: E402

log = logging.getLogger(__name__)

REPO_PATH = os.getenv("MUNKI_COMPUTERS_CSV_PATH", "deployment/enroll/computers.csv")


def converge(csv_text: str, *, what_if: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s | %(message)s")
    rows = csvdiff.parse(csv_text)
    if not rows:
        log.error("Refusing to publish an empty computers.csv.")
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
