"""Process only what moved.

A four-figure fleet re-pushed to five APIs every time somebody fixes a typo is
slow and a good way to meet your tenant's throttling limits. Each consumer keeps
a cached copy of the CSV it last processed and acts on the difference.

Two escape hatches, both of which earn their keep:
  FULL_RUN=true      process every row -- for when the LOGIC changed, so the
                     diff is empty but every row now means something new
  BYPASS_CACHE=true  same run, but do not trust the cached copy
"""
from __future__ import annotations

import csv
import io
import os
import pathlib


def parse(csv_text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() == "true"


def changed_rows(incoming: list[dict], cached: list[dict], key: str = "serial") -> list[dict]:
    """Rows whose content differs from the last processed run."""
    if _flag("FULL_RUN") or _flag("BYPASS_CACHE"):
        return list(incoming)
    old = {r.get(key): r for r in cached}
    return [row for row in incoming if old.get(row.get(key)) != row]


def read_cache(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write_cache(path: pathlib.Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
