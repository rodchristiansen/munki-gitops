#!/usr/bin/env python3
"""
resolve-superseded-pkgsinfo.py — auto-resolve "missing installer item" warnings
that are really just superseded (older) pkgsinfo left behind after the AutoPkg
pipeline's `repoclean --keep N` step.

Background
----------
`repoclean --keep 2` retires the oldest .pkg + pkgsinfo for a package once a
third newer version lands. When a clone is briefly out of step (or a stray
older pkgsinfo lingers locally), makecatalogs reports the old pkgsinfo as
referring to a missing installer item — its .pkg is already gone. Downloading
from Azure is futile because the blob is gone too, so the hook blocks.

Most of the time this is harmless: a strictly newer version of the SAME package
is present locally with its installer intact. The old pkgsinfo is just dead
weight. This helper detects exactly that case and deletes the superseded
pkgsinfo so the commit/push is not blocked.

Safety
------
A pkgsinfo is deleted ONLY when another pkgsinfo with the same `name` has a
STRICTLY GREATER version AND that newer version's own installer item is present
on disk. The newest/only version is never deleted. If no newer-and-present
sibling exists, the missing item is genuinely broken and stays blocking.

A cap (SUPERSEDED_CLEANUP_CAP, default 50) aborts the whole sweep if an
implausible number of deletions is requested — that signals a bad pull or a
wholesale repoclean, which a human should look at, not an automatic delete.

Usage
-----
    makecatalogs ... 2>&1 | resolve-superseded-pkgsinfo.py \
        --repo-root /path/to/repo [--apply]

Reads makecatalogs output on stdin. Recognises both warning dialects:
    Munki:  "WARNING: <ref> refers to missing installer item: <loc>"
    Cimian: "WARNING: <ref> has missing installer => pkgs/<loc>"
where <ref> is the pkgsinfo path (relative to the pkgsinfo dir, or repo-root)
and <loc> is the installer item path (relative to the pkgs dir).

Without --apply nothing is deleted (dry run). With --apply the superseded
pkgsinfo files are removed from disk; staging/committing is left to the caller.

Emits a JSON object on stdout:
    {
      "superseded":   ["deployment/pkgsinfo/apps/docs/Word-16.109.26051019.yaml", ...],
      "blocked":      [{"ref": "...", "loc": "...", "name": "...", "version": "..."}],
      "cap_exceeded": false,
      "cap":          50
    }
`superseded` paths are repo-root-relative. Exit status:
    0  nothing blocking remains (everything resolved or no missing items)
    1  genuine missing items remain (caller should block)
    2  cap exceeded — refused to delete, caller should block
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import sys
from pathlib import Path


# ── version comparison ──────────────────────────────────────────────────────
# Faithful port of munkilib.pkgutils.MunkiLooseVersion so our ordering matches
# exactly what makecatalogs / Munki itself would decide. Self-contained: the
# real class imports Foundation, which is not available to a plain python3.
_COMPONENT_RE = re.compile(r"(\d+ | [a-z]+ | \.)", re.VERBOSE)


def _parse_version(vstring) -> list:
    if vstring is None:
        vstring = ""
    components = [x for x in _COMPONENT_RE.split(str(vstring)) if x and x != "."]
    for i, obj in enumerate(components):
        try:
            components[i] = int(obj)
        except ValueError:
            pass
    return components


def _cmp(a, b) -> int:
    return (a > b) - (a < b)


def version_compare(a, b) -> int:
    """Return -1/0/1 for MunkiLooseVersion(a) </==/> MunkiLooseVersion(b)."""
    va, vb = _parse_version(a), _parse_version(b)
    max_length = max(len(va), len(vb))
    va = va + [0] * (max_length - len(va))
    vb = vb + [0] * (max_length - len(vb))
    for x, y in zip(va, vb):
        try:
            result = _cmp(x, y)
        except TypeError:
            # int sorts below str (mirrors MunkiLooseVersion._compare)
            return -1 if isinstance(x, int) else 1
        if result:
            return result
    return 0


# ── pkgsinfo parsing ────────────────────────────────────────────────────────
def _load_pkgsinfo(path: Path) -> dict | None:
    """Read the fields we need (name / version / installer location) from a
    pkgsinfo file as LITERAL strings.

    YAML is parsed with a small line parser rather than PyYAML on purpose:
    PyYAML coerces `version: 16.110` to the float 16.11 (dropping the trailing
    zero) and `version: 11.1` to 11.1, which would corrupt version ordering.
    Reading the literal text keeps the string makecatalogs/MunkiLooseVersion
    actually compare. Plist/XML pkgsinfo already store versions as strings, so
    those go through plistlib.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw.strip():
        return None

    suffix = path.suffix.lower()
    stripped = raw.lstrip()
    looks_plist = stripped.startswith(b"<?xml") or stripped.startswith(b"<plist")

    if suffix in (".plist", ".xml") or looks_plist:
        try:
            data = plistlib.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return _minimal_scalars(raw)


_MIN_TOP = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$")
_MIN_NESTED = re.compile(r"^\s+location:\s*(.*?)\s*$")


def _minimal_scalars(raw: bytes) -> dict | None:
    """Extract name / version / installer location without a YAML library.

    Handles Munki's top-level `installer_item_location` and Cimian's nested
    `installer:` / `location:` form. Only the keys this tool needs.
    """
    text = raw.decode("utf-8", "replace")
    data: dict = {}
    in_installer = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = _MIN_TOP.match(line)
        if top:
            key, val = top.group(1), top.group(2).strip().strip("\"'")
            in_installer = key == "installer"
            if key in ("name", "version", "installer_item_location") and val:
                data[key] = val
            continue
        if in_installer:
            nested = _MIN_NESTED.match(line)
            if nested:
                val = nested.group(1).strip().strip("\"'")
                if val:
                    data.setdefault("installer", {})["location"] = val
    return data or None


def _installer_location(data: dict):
    """Extract the installer item location across Munki + Cimian schemas."""
    loc = data.get("installer_item_location")
    if not loc:
        installer = data.get("installer")
        if isinstance(installer, dict):
            loc = installer.get("location")
    if not loc:
        return None
    return _normalize_loc(loc)


def _normalize_loc(loc: str) -> str:
    return str(loc).replace("\\", "/").lstrip("/")


# ── installer presence ──────────────────────────────────────────────────────
def _installer_present(pkgs_dir: Path, loc: str) -> bool:
    """Case-insensitive existence check, mirroring makecatalogs' fallback."""
    if not loc:
        return False
    candidate = pkgs_dir / loc
    if candidate.is_file():
        return True
    parent = candidate.parent
    target = candidate.name.lower()
    try:
        for entry in parent.iterdir():
            if entry.name.lower() == target and entry.is_file():
                return True
    except OSError:
        pass
    return False


# ── warning parsing ─────────────────────────────────────────────────────────
_WARN_MUNKI = re.compile(
    r"WARNING:\s+(?P<ref>.+?)\s+refers to missing installer item:\s+(?P<loc>.+?)\s*$"
)
_WARN_CIMIAN = re.compile(
    r"WARNING:\s+(?P<ref>.+?)\s+has missing installer =>\s*(?:pkgs[\\/])?(?P<loc>.+?)\s*$"
)


def _parse_warnings(lines, pkgsinfo_subdir: str):
    """Yield (ref_relative_to_repo_root, loc) for each missing-item warning."""
    seen = set()
    for line in lines:
        line = line.rstrip()
        m = _WARN_MUNKI.search(line) or _WARN_CIMIAN.search(line)
        if not m:
            continue
        ref = m.group("ref").strip().replace("\\", "/")
        loc = _normalize_loc(m.group("loc"))
        # Normalise the pkgsinfo ref to a repo-root-relative path. makecatalogs
        # emits it relative to the pkgsinfo dir; some dialects include the
        # leading deployment/pkgsinfo/ already.
        if pkgsinfo_subdir in ref:
            ref = ref[ref.index(pkgsinfo_subdir):]
        else:
            ref = f"{pkgsinfo_subdir}/{ref.lstrip('/')}"
        key = (ref, loc)
        if key not in seen:
            seen.add(key)
            yield ref, loc


# ── sibling index ───────────────────────────────────────────────────────────
_NAME_LINE = re.compile(rb"^name:\s*(.+?)\s*$", re.MULTILINE)


def _quick_name(path: Path) -> str | None:
    """Cheap top-level `name:` read to avoid fully parsing every pkgsinfo."""
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    m = _NAME_LINE.search(head)
    if not m:
        return None
    return m.group(1).decode("utf-8", "replace").strip().strip("\"'")


def _build_sibling_index(pkgsinfo_dir: Path, pkgs_dir: Path, target_names: set):
    """Map name -> list of {version, present} for every pkgsinfo of that name."""
    index: dict[str, list] = {}
    for path in pkgsinfo_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".yaml", ".yml", ".plist", ".xml", ""):
            continue
        if path.name.startswith("."):
            continue
        name = _quick_name(path)
        if name is None or name not in target_names:
            continue
        data = _load_pkgsinfo(path)
        if not data:
            continue
        real_name = data.get("name")
        if not isinstance(real_name, str) or real_name not in target_names:
            continue
        loc = _installer_location(data)
        index.setdefault(real_name, []).append({
            "version": data.get("version"),
            "present": _installer_present(pkgs_dir, loc) if loc else False,
        })
    return index


# ── core decision ───────────────────────────────────────────────────────────
def resolve(repo_root: Path, pkgsinfo_subdir: str, pkgs_subdir: str,
            warning_lines, cap: int):
    pkgsinfo_dir = repo_root / pkgsinfo_subdir
    pkgs_dir = repo_root / pkgs_subdir

    missing = list(_parse_warnings(warning_lines, pkgsinfo_subdir))
    if not missing:
        return {"superseded": [], "blocked": [], "cap_exceeded": False, "cap": cap}

    # Read name+version for each missing pkgsinfo.
    missing_info = []
    target_names: set = set()
    for ref, loc in missing:
        data = _load_pkgsinfo(repo_root / ref)
        name = data.get("name") if data else None
        version = data.get("version") if data else None
        missing_info.append({"ref": ref, "loc": loc, "name": name, "version": version})
        if name is not None:
            target_names.add(name)

    index = _build_sibling_index(pkgsinfo_dir, pkgs_dir, target_names)

    superseded = []
    blocked = []
    for item in missing_info:
        name, version = item["name"], item["version"]
        siblings = index.get(name, []) if name is not None else []
        has_newer_present = any(
            sib["present"]
            and sib["version"] is not None
            and version is not None
            and version_compare(sib["version"], version) > 0
            for sib in siblings
        )
        if has_newer_present:
            superseded.append(item["ref"])
        else:
            blocked.append(item)

    # De-dupe superseded refs while preserving order.
    superseded = list(dict.fromkeys(superseded))

    cap_exceeded = len(superseded) > cap
    return {
        "superseded": superseded,
        "blocked": blocked,
        "cap_exceeded": cap_exceeded,
        "cap": cap,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--pkgsinfo-subdir", default="deployment/pkgsinfo")
    parser.add_argument("--pkgs-subdir", default="deployment/pkgs")
    parser.add_argument("--cap", type=int,
                        default=int(os.environ.get("SUPERSEDED_CLEANUP_CAP", "50")))
    parser.add_argument("--apply", action="store_true",
                        help="actually delete superseded pkgsinfo files")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    result = resolve(
        repo_root,
        args.pkgsinfo_subdir.strip("/"),
        args.pkgs_subdir.strip("/"),
        sys.stdin.read().splitlines(),
        args.cap,
    )

    if args.apply and result["superseded"] and not result["cap_exceeded"]:
        deleted = []
        for ref in result["superseded"]:
            target = repo_root / ref
            try:
                target.unlink()
                deleted.append(ref)
            except OSError:
                pass
        result["superseded"] = deleted

    print(json.dumps(result))

    if result["cap_exceeded"]:
        return 2
    return 1 if result["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
