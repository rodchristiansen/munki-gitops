# Munki Git Hooks

A battle-tested set of git hooks that turn a Munki repo into a GitOps-native deployment system. Every commit validates `pkgsinfo`, every pull downloads the packages it just referenced, every push syncs the repo to cloud storage. The hooks catch the kind of silent-failure mistakes `makecatalogs` lets through, protect against the failure modes that have bitten real deployments, and stay out of the way when nothing's changed.

Two parallel implementations ship here:

- **`azure/`** — Azure Blob Storage via `azcopy`.
- **`aws/`** — S3 via `aws s3 cp`/`sync`.

Both use the same hook names, same flags, same env-var bypass, same safety guards. Pick the cloud you're on; the admin UX is identical.

## Contents

| Path                              | Purpose                                                         |
|-----------------------------------|------------------------------------------------------------------|
| `.min-version`                    | Enforced floor — every hook warns if older than this YYYY.MM.DD. |
| `lib/common.sh`                   | Shared helpers: version check, worktree linker, size guard, lock. |
| `lib/pkgsinfo-lint.py`            | Structural pkgsinfo validator (Munki-native schema).             |
| `lib/resolve-superseded-pkgsinfo.py` | Auto-removes older pkgsinfo whose installer was retired by `repoclean`, when a newer version is present locally. |
| `azure/pre-commit`                | Validate pkgsinfo, auto-download missing pkgs, block bad commits. |
| `azure/pre-push`                  | Main: sync changes to Azure Blob, remove orphans. Branches: hand off to `pre-push-pr-packages`. |
| `azure/pre-push-pr-packages`      | Branch pushes: upload each package the pushed pkgsinfo need, create-only, keyed by SHA-256. |
| `azure/post-merge`                | Download packages referenced by newly-pulled pkgsinfo.           |
| `azure/post-rewrite`              | Safety net for `git rebase` / `git commit --amend`.              |
| `azure/post-checkout`             | (Optional) per-admin secrets bootstrap scaffolding.              |
| `aws/*`                           | Parallel implementation against S3.                              |
| `tests/*.zsh`                     | Offline tests with fake `az`, `azcopy` and `aws`.                |

## Install

Opt in per clone — git doesn't use `githooks/` by default:

```sh
git clone https://github.com/your-org/your-munki-repo.git
cd your-munki-repo

# Pick your cloud
git config core.hooksPath githooks/azure    # or githooks/aws
```

Some teams symlink instead of `core.hooksPath`:

```sh
ln -s ../githooks/azure .githooks
git config core.hooksPath .githooks
```

## Configuration

The hooks read environment variables for everything that might differ between organisations. Set them in your `~/.zshrc`, `~/.bashrc`, or `direnv` config.

### Azure

| Variable                           | Default                 | Purpose                                         |
|------------------------------------|-------------------------|-------------------------------------------------|
| `MUNKI_STORAGE_ACCOUNT`            | `yourstorageaccount`    | Azure Blob storage account name.                |
| `MUNKI_CONTAINER`                  | `munki`                 | Container name inside the storage account.      |
| `MUNKI_AZURE_TENANT_ID`            | *(none)*                | Optional — tenant ID for `az login` hints.      |
| `MUNKI_CACHING_SERVERS`            | *(empty)*               | `host1:host2` HTTP caching servers for download fast-path. |

### AWS

| Variable                           | Default                 | Purpose                                         |
|------------------------------------|-------------------------|-------------------------------------------------|
| `MUNKI_S3_BUCKET`                  | `your-munki-bucket`     | S3 bucket name.                                 |
| `MUNKI_S3_PREFIX`                  | *(empty)*               | Optional key prefix inside the bucket.          |
| `MUNKI_AWS_REGION`                 | `us-east-1`             | Region for SigV4 signing / endpoint.            |
| `MUNKI_CACHING_SERVERS`            | *(empty)*               | Same as Azure — HTTP caching fast-path.         |

### Cross-cutting

| Variable                        | Default                                                        | Purpose                             |
|---------------------------------|----------------------------------------------------------------|-------------------------------------|
| `MUNKI_MIN_PKGSINFO_FOR_VALID`  | `50`                                                           | Floor below which orphan cleanup refuses to run (a sparse or wrong checkout). |
| `MUNKI_AZURE_ORPHAN_DELETION_CAP` / `MUNKI_S3_ORPHAN_DELETION_CAP` | `50`                     | Most orphans one push may delete from storage. |
| `MUNKI_MANAGED_APP_CATEGORIES`  | *(empty)*                                                      | Optional comma-separated vocabulary for `category` in Intune app descriptors. |
| `MUNKI_MAX_FILE_SIZE_MB`        | `50`                                                           | Binary-size guard threshold.        |
| `MUNKI_ALLOW_BINARY_PATHS`      | `^deployment/pkgs/:^deployment/icons/`                         | Colon-separated regexes of paths where big files are allowed. |
| `MUNKI_WORKTREE_LINK_PATHS`     | `deployment/pkgs:deployment/icons:deployment/catalogs`         | Colon-separated paths to symlink in linked worktrees. |

### Emergency bypass

All env vars silently skip the hook:

| Variable                        | Effect                                                         |
|---------------------------------|----------------------------------------------------------------|
| `GIT_NO_VERIFY=1`               | Generic git bypass — skips every hook.                         |
| `SKIP_MUNKI_HOOKS=1`            | Munki-specific — skips all hooks.                              |
| `SKIP_POST_MERGE=1`             | Skips only `post-merge`.                                       |
| `SKIP_PRE_PUSH=1`               | Skips only `pre-push`.                                         |
| `DISABLE_CUSTOM_HOOKS=1`        | Kills every hook.                                              |

Also honoured: `git pull --no-verify`, `git merge --no-verify` — parsed out of the parent process command line.

## What each hook does

### `pre-commit`

1. **Hook version check** — warns if the hook is older than `.min-version`.
2. **Concurrency lock** — prevents overlap with `pre-push`; stale locks auto-steal.
3. **Binary-size guard** — rejects staged files >50 MB outside recognised pkg/icon paths.
4. **Worktree cache link** — silent no-op in primary worktree; symlinks cloud caches in linked worktrees.
5. **Datetime auto-quote** — rewrites unquoted tz-aware ISO8601 scalars (`creation_date: 2026-04-22T17:51:22Z`) to quoted strings in staged pkgsinfo and re-stages them. Unquoted, PyYAML loads them as tz-aware `datetime` objects that crash any consumer comparing them against a naive datetime (a catalog promoter, a report script). Silent, idempotent.
6. **Structural pkgsinfo linter** — catches typos, wrong-case keys, invalid `installer_type`, `nopkg` install-loop traps, `RequireRestart`+`unattended_install` combos. 47 valid top-keys, full enum validation. See `lib/pkgsinfo-lint.py` for the full schema.
7. **`makecatalogs` validation** — parse errors, missing required keys, invalid item locations, empty catalogs.
8. **Missing-pkg auto-download** — pulls the referenced installer items from cloud storage; blocks only if still missing after download.
9. **Superseded-pkgsinfo resolution** — if a package is still "missing" after download, it's usually an older pkgsinfo whose `.pkg` was retired by `repoclean --keep N` (the blob is gone too, so downloading is futile). When a strictly newer version of the *same* package is present locally with its installer intact, the old pkgsinfo is dead weight: `lib/resolve-superseded-pkgsinfo.py` deletes it (newest-version-safe, capped at `SUPERSEDED_CLEANUP_CAP`=50) and re-validates instead of blocking the commit.
10. **Orphan pkg cleanup** — main branch only, capped at 10 deletions (prevents catastrophic mass-delete on partial branches).

### `pre-push`

What a push does depends on the branch.

**A branch other than main** runs `pre-push-pr-packages`. It reads the refs git passes on stdin, finds the pkgsinfo the pushed commits add or change, and for each one:

1. Skips `nopkg`, `profile` and `apple_update_metadata` items and Intune descriptors under `apps/managed/`, which have no package.
2. Refuses an `installer_item_location` that is absolute or contains an empty or `..` segment. A location becomes a local path and a storage key, so it must stay under `deployment/pkgs/`.
3. Accepts a package already in storage only when its `sha256` metadata matches the pkgsinfo's `installer_item_hash`.
4. Uploads a missing package create-only (`azcopy --overwrite=false`, S3 `--if-none-match '*'`) after checking the local file's SHA-256, and records that hash in the object's metadata. Losing a create race to the same bytes is fine; to different bytes it blocks.
5. Refuses to replace a package path that holds different bytes. Package paths are immutable: clients and the CDN cache them for a year.
6. Records SHA-256 metadata on a legacy object only with proof: its MD5 (Azure) or single-part ETag (S3) matches the local file, or `origin/main` already assigns that hash to that path.

It never deletes. The push pipeline's gate then finds every package it needs.

**main** gets the full path:

1. **Fast-forward check** — pulls if behind; aborts on non-FF.
2. **`makecatalogs` re-validate** — one last check against reality.
3. **Targeted or bulk sync** — uploads only what changed, or everything with `--sync`. Uploads are additive: a checkout missing part of the package cache never deletes those packages from storage.
4. **MD5 hash metadata** — Azure uploads use `--put-md5` so future `--compare-hash=MD5` runs actually have something to compare.
5. **Orphan cleanup** — removes storage objects no pkgsinfo references, after a floor (`MUNKI_MIN_PKGSINFO_FOR_VALID`), a deployment check and a cap. Packages referenced on any remote branch are kept, because branch pushes upload them before the branch merges.

Flags: `--sync` (skip change detection), `--force` (double-confirm: `y` then literal `FORCE`), `--dry-run`, `--path <relative>` (targeted file).

### `post-merge`

Fires after every `git pull`, `git merge`, `git checkout <branch>`. Downloads the packages referenced by pkgsinfo that just changed.

1. **HTTP caching server probe** — if `MUNKI_CACHING_SERVERS` is set and reachable (and you're not on VPN), pulls from there first. Falls back to cloud on miss.
2. **Batched parallel download** — every cache miss goes into a single `azcopy copy --list-of-files` (Azure) or `aws s3 cp --recursive` (AWS) call. AzCopy/awscli parallelise internally.
3. **Orphan cleanup** — main branch only.

Flags: `--sync`, `--force`, `--dry-run`, `--path <relative>`.

### `post-rewrite`

Safety net for `git rebase` and `git commit --amend` — same sync logic as `post-merge`, guarded by a confirmation dialog (rebasing rarely needs a cloud re-sync).

### `post-checkout`

Skeleton — your implementation should fetch org-specific secrets (Azure Key Vault / AWS Secrets Manager), write config files, and do fresh-clone setup. The version shipped here is a reference only.

## Troubleshooting

**"COMMIT BLOCKED: NNN missing packages exceeds safe auto-download limit"**
Fresh clone or major branch switch. Run:
```sh
.githooks/post-merge --sync
```

**"COMMIT BLOCKED: N orphan package(s) found (cap is 10)"**
Hook found more orphans than the safety cap. Most common cause is a partial branch where pkgsinfo was deleted but the pkgs weren't, or a YAML parse error hiding `installer_item_location` from the orphan detector. The dialog lists the first 15 — investigate and `rm` manually if genuinely orphaned.

**"Another hook is running"**
A prior hook is still working, or it crashed. The lock auto-steals from dead PIDs on next attempt; if stuck, `rm -rf` the reported path.

**"COMMIT BLOCKED: staged file(s) > 50MB outside recognised binary paths"**
Either move the file under `deployment/pkgs/` and add a pkgsinfo, or `git restore --staged <file>`. Widen `MUNKI_ALLOW_BINARY_PATHS` if your org legitimately keeps big files elsewhere.

**Worktree still shows "NNN missing packages"**
Make sure `core.hooksPath` is set inside the worktree, and the `githooks/` path it points at is accessible from there.

## The pkgsinfo linter

`lib/pkgsinfo-lint.py` runs in `pre-commit` on every staged pkgsinfo YAML/plist file. Schema derived from Munki's own source, not guessed:

**Valid installer types** (current): `pkg_install` (default), `copy_from_dmg`, `stage_os_installer`, `nopkg`. Deprecated but tolerated: `appdmg`, `startosinstall`, `profile`, `apple_update_metadata`, `Adobe*`.

**Valid `RestartAction`**: `None`, `RequireRestart`, `RecommendRestart`, `RequireLogout` (PascalCase).

**Valid `supported_architectures`**: `x86_64`, `arm64` (not `x64` — that's Cimian).

**Blocked patterns**:
- Unknown top-level keys (`install_scritp`, typos).
- Wrong-case keys, catalogs, or enums.
- `nopkg` + install script with no `installcheck_script` and no `installs` → reinstalls every cycle.
- `RestartAction: RequireRestart` + `unattended_install: true` → a forced restart isn't unattended.
- Duplicate top-level YAML keys (silently drops earlier values).
- Empty `catalogs: []`.

**Intune app descriptors** under `pkgsinfo/apps/managed/` are not pkginfo, so they get their own rules: only the known keys; `source: apple_vpp`; a quoted `store_id`; an `assignment` with `intent` (`available` or `required`) and boolean `device_licensing` and `prevent_auto_update`; and `catalogs` as a cumulative prefix of `Development, Testing, Staging, Production`. Set `MUNKI_MANAGED_APP_CATEGORIES` to close the `category` vocabulary.

`nopkg` items marked `OnDemand` are exempt from the install-loop check: Munki runs them only when the user asks.

Catalogs and required-by-installer-type rules are validated. Full schema is at the top of `pkgsinfo-lint.py` — edit freely if your Munki deployment diverges.

## Extending

**New pkgsinfo key / installer type** → edit `VALID_TOP_KEYS` / `CURRENT_INSTALLER_TYPES` in `lib/pkgsinfo-lint.py`. Add a source reference (file + line) so future-you knows where the schema came from.

**New caching server** → set `MUNKI_CACHING_SERVERS="host1:host2"` in your env. No code change needed.

**New binary-cache path** → add to `MUNKI_ALLOW_BINARY_PATHS`. No code change needed.

**New structural check** → add a test in `issues_for_file` in `pkgsinfo-lint.py`. Always return a specific, human-readable error — no `validation failed`.

When bumping functionality that admins must have, bump `.min-version` and the hook's own `HOOK_VERSION=` stamp in the same commit.

## Tests

The tests build throwaway repos and fake the cloud CLIs, so they need no account:

```sh
for t in githooks/tests/*.zsh; do zsh "$t"; done
```

## Why bother?

The simplest version of this system — `git commit` → `azcopy sync` — is what most teams start with. It works until:

- Two admins push at once and azcopy races itself into an inconsistent state.
- Someone commits a YAML with a typo in `installer_type` and `makecatalogs` silently excludes it from the catalog.
- A fresh clone tries to run `makecatalogs` on 1000 pkgsinfo files and fails with "missing installer" for every one of them.
- `git rebase` during a conflict resolution accidentally forks a branch and a `pre-push` deletes blob storage files the other branch still references.

Each of the guards in these hooks exists because one of those scenarios actually happened. The lint, the cap, the lock, the version check, the worktree link — every one paid for itself in avoided incidents.

Read them, steal them, adapt them to your org. Issues and PRs welcome.
