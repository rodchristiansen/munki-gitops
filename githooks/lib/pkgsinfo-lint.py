#!/usr/bin/env python3
"""
pkgsinfo-lint.py  —  static pkgsinfo structural validation for Munki pre-commit.

Schema derived from Munki source, not copied from Cimian. Key source locations:
    packages/MunkiTools/code/cli/munki/shared/utils/yamlutils.swift
    packages/MunkiTools/code/cli/munki/shared/installer/installer.swift
    packages/MunkiTools/code/cli/munki/shared/admin/pkginfoOptions.swift

Usage:
    pkgsinfo-lint.py <repo-root>

Reads staged pkgsinfo paths via `git diff --cached --name-only`, filters to
deployment/pkgsinfo/**/*.{yaml,yml,plist}, validates each, and emits a
human-readable report on stderr. Exit 0 pass, exit 1 errors found.

Catches problems `makecatalogs` silently accepts:
    * Unknown top-level keys (typos like install_scritp)
    * Wrong-case keys (InstallerType vs installer_type)
    * Wrong-case catalogs (production vs Production)
    * Invalid supported_architectures values (x64 — Cimian-style, not Munki)
    * Invalid RestartAction values
    * nopkg with no installcheck_script AND no installs — install loop
    * RequireRestart + unattended_install: true — incompatible
    * Duplicate top-level keys (YAML silently drops all but the last)
    * Empty catalogs array
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import yaml  # PyYAML — ships with macOS Python3
except ImportError:
    sys.stderr.write("ERROR: PyYAML not available; cannot lint pkgsinfo\n")
    sys.exit(0)  # don't block commits if lint can't run


# ── Munki-native schema (distinct from Cimian's schema) ─────────────────────

VALID_TOP_KEYS: set[str] = {
    # Identity
    "name", "display_name", "version", "category", "developer",
    "description", "notes", "icon_name", "icon_hash", "_metadata",
    # Catalog assignment
    "catalogs",
    # Installer descriptors
    "installer_type",
    "installer_item_location", "installer_item_hash",
    "installer_item_size", "installed_size",
    "uninstaller_item_location", "uninstaller_item_hash",
    "uninstaller_item_size",
    "uninstall_method", "uninstallable",
    "receipts", "installs", "items_to_copy",
    "installer_choices_xml", "payloads", "package_path",
    # Profiles (deprecated installer_type but still used)
    "PayloadIdentifier",
    # Install behaviour
    "autoremove", "unattended_install", "unattended_uninstall",
    "force_install_after_date", "OnDemand", "featured",
    "localized_strings", "installable_condition",
    "unused_software_removal_info",
    # System compatibility
    "minimum_os_version", "maximum_os_version", "minimum_munki_version",
    "supported_architectures",
    # UX
    "RestartAction", "blocking_applications",
    # Scripts
    "preinstall_script", "postinstall_script",
    "preuninstall_script", "postuninstall_script",
    "uninstall_script",
    "installcheck_script", "uninstallcheck_script",
    # Relationships
    "requires", "update_for", "conditional_items",
}

# Currently-supported installer types in Munki (see installer.swift:218-246).
# Absent installer_type defaults to pkg_install.
CURRENT_INSTALLER_TYPES: set[str] = {
    "pkg_install", "copy_from_dmg", "stage_os_installer", "nopkg",
}

# Deprecated but still encountered in older pkgsinfo — warn, don't block.
DEPRECATED_INSTALLER_TYPES: set[str] = {
    "appdmg", "startosinstall", "profile", "apple_update_metadata",
    # Any value beginning with "Adobe" historically maps to an Adobe installer
}

# Valid catalog names (from Munki/CLAUDE.md and real pkgsinfo).
VALID_CATALOGS: set[str] = {
    "Development", "Testing", "Staging", "Production",
    "Kiosk", "Curriculum", "Personal",
}

# Valid supported_architectures in Munki — distinct from Cimian's (no "x64").
VALID_ARCHES: set[str] = {"x86_64", "arm64"}

# Valid RestartAction values. PascalCase — different from Cimian's
# restart_action snake_case key.
#
# Swift source (pkginfoOptions.swift:41-45) lists RequireRestart,
# RecommendRestart, RequireLogout. But real in-repo pkgsinfo additionally
# uses `None` (treated as a no-op explicit opt-out), and makecatalogs
# doesn't reject it. Accept it here too so the lint doesn't false-positive.
VALID_RESTART_ACTIONS: set[str] = {
    "None", "RequireRestart", "RecommendRestart", "RequireLogout",
}


def issues_for_file(path: Path) -> list[str]:
    """Validate one pkgsinfo file. Returns a list of error strings."""
    errors: list[str] = []

    suffix = path.suffix.lower()
    raw_bytes = path.read_bytes()

    # Parse the pkgsinfo — support YAML and plist (including extensionless plists).
    data: Any
    try:
        if suffix in (".yaml", ".yml"):
            data = yaml.safe_load(raw_bytes)
            # YAML: detect duplicate top-level keys manually
            # (safe_load silently drops duplicates).
            duplicate_keys = _yaml_duplicate_top_keys(raw_bytes.decode("utf-8", "replace"))
            for k in duplicate_keys:
                errors.append(f"Duplicate top-level key '{k}' (YAML drops all but the last value)")
        elif suffix == ".plist" or raw_bytes.lstrip().startswith(b"<?xml"):
            data = plistlib.loads(raw_bytes)
        else:
            # Extensionless plist (e.g. Thunderbird-135.0.1) or pure YAML without ext
            try:
                data = plistlib.loads(raw_bytes)
            except Exception:
                data = yaml.safe_load(raw_bytes)
    except Exception as exc:
        # makecatalogs already blocks on parse errors with better messages;
        # don't duplicate here. Return empty so the commit path gets to it.
        return []

    if not isinstance(data, dict):
        errors.append("Top-level structure is not a mapping/dict")
        return errors

    top_keys = set(data.keys())

    # 1. Unknown top-level keys (typos)
    for key in sorted(top_keys - VALID_TOP_KEYS):
        # Case-insensitive match against valid keys → suggest correction
        ci_match = next((v for v in VALID_TOP_KEYS if v.lower() == key.lower()), None)
        if ci_match:
            errors.append(f"Key '{key}' has wrong case — should be '{ci_match}'")
        else:
            errors.append(f"Unknown top-level key '{key}' (typo? not in Munki pkgsinfo schema)")

    # 2. Required fields
    for required in ("name", "version", "catalogs"):
        if required not in data:
            errors.append(f"Missing required field '{required}'")

    # 3. Empty name/version
    for k in ("name", "version"):
        if k in data and not str(data.get(k) or "").strip():
            errors.append(f"Field '{k}' is empty")

    # 4. catalogs must be a non-empty list of valid values
    if "catalogs" in data:
        catalogs = data.get("catalogs")
        if not isinstance(catalogs, list):
            errors.append("Field 'catalogs' must be a list")
        elif len(catalogs) == 0:
            errors.append("Field 'catalogs' is empty — pkgsinfo won't be assigned to any catalog")
        else:
            for c in catalogs:
                if c not in VALID_CATALOGS:
                    ci = next((v for v in VALID_CATALOGS if v.lower() == str(c).lower()), None)
                    if ci:
                        errors.append(f"Catalog '{c}' has wrong case — should be '{ci}'")
                    else:
                        errors.append(f"Invalid catalog '{c}' (valid: {', '.join(sorted(VALID_CATALOGS))})")

    # 5. supported_architectures — Munki uses x86_64/arm64 (not Cimian's x64)
    if "supported_architectures" in data:
        archs = data.get("supported_architectures") or []
        if not isinstance(archs, list):
            errors.append("Field 'supported_architectures' must be a list")
        else:
            for a in archs:
                if a not in VALID_ARCHES:
                    if a in ("x64",):
                        errors.append(f"Invalid architecture 'x64' — Munki uses 'x86_64' (Cimian uses 'x64')")
                    else:
                        errors.append(f"Invalid architecture '{a}' (valid: {', '.join(sorted(VALID_ARCHES))})")

    # 6. RestartAction (PascalCase)
    if "RestartAction" in data:
        ra = data.get("RestartAction")
        if ra not in VALID_RESTART_ACTIONS:
            errors.append(
                f"Invalid RestartAction '{ra}' "
                f"(valid: {', '.join(sorted(VALID_RESTART_ACTIONS))})"
            )
        # RequireRestart + unattended_install: true — incompatible
        if ra == "RequireRestart" and bool(data.get("unattended_install")) is True:
            errors.append(
                "'RestartAction: RequireRestart' cannot combine with "
                "'unattended_install: true' — a forced restart is not unattended"
            )

    # 7. installer_type validation
    installer_type = data.get("installer_type", "pkg_install")
    if not isinstance(installer_type, str) or not installer_type:
        errors.append("Field 'installer_type' must be a non-empty string")
        installer_type = "pkg_install"

    is_current = installer_type in CURRENT_INSTALLER_TYPES
    is_deprecated = (
        installer_type in DEPRECATED_INSTALLER_TYPES
        or installer_type.startswith("Adobe")
    )
    if not is_current and not is_deprecated:
        errors.append(
            f"Unknown installer_type '{installer_type}' "
            f"(current: {', '.join(sorted(CURRENT_INSTALLER_TYPES))})"
        )

    # 8. nopkg install loop trap
    # nopkg runs install_script/postinstall_script every managedsoftwareupdate
    # cycle unless installcheck_script or `installs` tells Munki it's already
    # done. Block commits that would create the loop.
    if installer_type == "nopkg":
        has_install_script = any(
            k in data for k in ("postinstall_script", "preinstall_script", "install_script")
        )
        has_installcheck = "installcheck_script" in data
        has_installs = "installs" in data and data.get("installs")
        if has_install_script and not has_installcheck and not has_installs:
            errors.append(
                "installer_type 'nopkg' has install-side script(s) but no "
                "'installcheck_script' and no 'installs' — will re-run on every "
                "managedsoftwareupdate cycle. Add installcheck_script that "
                "exits 1 when already applied, or populate 'installs'."
            )

    # 9. pkg_install requires installer_item_location + hash
    if installer_type == "pkg_install":
        if "installer_item_location" not in data:
            errors.append("installer_type 'pkg_install' requires 'installer_item_location'")
        if "installer_item_hash" not in data:
            errors.append("installer_type 'pkg_install' requires 'installer_item_hash'")

    # 10. copy_from_dmg requires items_to_copy
    if installer_type == "copy_from_dmg":
        if "installer_item_location" not in data:
            errors.append("installer_type 'copy_from_dmg' requires 'installer_item_location'")
        if "items_to_copy" not in data or not data.get("items_to_copy"):
            errors.append("installer_type 'copy_from_dmg' requires non-empty 'items_to_copy'")

    # 11. stage_os_installer requires installer_item_location + items_to_copy
    if installer_type == "stage_os_installer":
        if "installer_item_location" not in data:
            errors.append("installer_type 'stage_os_installer' requires 'installer_item_location'")
        if "items_to_copy" not in data or not data.get("items_to_copy"):
            errors.append("installer_type 'stage_os_installer' requires 'items_to_copy'")

    return errors


def _yaml_duplicate_top_keys(text: str) -> list[str]:
    """Scan raw YAML text for top-level key duplicates.

    PyYAML safe_load silently drops earlier values when a key repeats;
    we detect by line-level parsing (only zero-indent `^key:` lines).
    Skips lines inside block scalars and inside '# comment' lines.
    """
    seen: Counter[str] = Counter()
    in_block_scalar = False
    block_indent = 0
    for raw_line in text.splitlines():
        stripped = raw_line.rstrip()
        if not stripped:
            continue
        if stripped.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if in_block_scalar:
            if indent > block_indent:
                continue
            in_block_scalar = False
        if indent == 0:
            # Top-level `key:` line
            if ":" in stripped:
                key = stripped.split(":", 1)[0]
                if key.isidentifier() or all(c.isalnum() or c == "_" for c in key):
                    # Check for block scalar indicator (| or >) on this line
                    rest = stripped.split(":", 1)[1].strip()
                    if rest.startswith("|") or rest.startswith(">"):
                        in_block_scalar = True
                        block_indent = 0
                    seen[key] += 1
    return [k for k, n in seen.items() if n > 1]


def get_staged_pkgsinfo(repo_root: Path) -> list[Path]:
    """Return absolute paths of staged pkgsinfo files."""
    proc = subprocess.run(
        ["git", "-C", str(repo_root),
         "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return []

    staged: list[Path] = []
    for line in proc.stdout.splitlines():
        if not line.startswith("deployment/pkgsinfo/"):
            continue
        # Include all pkgsinfo files; extensionless files are often plists.
        path = repo_root / line
        if path.is_file():
            staged.append(path)
    return staged


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: pkgsinfo-lint.py <repo-root>\n")
        return 2

    repo_root = Path(sys.argv[1]).resolve()
    staged = get_staged_pkgsinfo(repo_root)

    if not staged:
        return 0

    issues: list[tuple[str, list[str]]] = []
    for f in staged:
        rel = str(f.relative_to(repo_root))
        errs = issues_for_file(f)
        if errs:
            issues.append((rel, errs))

    if not issues:
        sys.stderr.write(f"[pre-commit] pkgsinfo structural lint passed ({len(staged)} file(s))\n")
        return 0

    total_errors = sum(len(e) for _, e in issues)
    sys.stderr.write("\n")
    sys.stderr.write(f"  COMMIT BLOCKED: {total_errors} structural error(s) in {len(issues)} pkgsinfo file(s)\n")
    sys.stderr.write("\n")
    for rel, errs in issues:
        sys.stderr.write(f"  {rel}\n")
        for e in errs:
            sys.stderr.write(f"    • {e}\n")
        sys.stderr.write("\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
