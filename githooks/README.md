# Munki Git Hooks

A battle-tested set of git hooks that turn a Munki repo into a GitOps-native deployment system. Every commit validates `pkgsinfo`, every pull downloads the packages it just referenced, every push syncs the repo to cloud storage. The hooks catch the kind of silent-failure mistakes `makecatalogs` lets through, protect against the failure modes that have bitten real deployments, and stay out of the way when nothing's changed.

Two parallel implementations ship here:

- **`azure/`** — Azure Blob Storage via `azcopy`.
- **`aws/`** — S3 via `aws s3 cp`/`sync`.

Both use the same hook names, same flags, same env-var bypass, same safety guards. Pick the cloud you're on; the admin UX is identical.

> Installed across a production Munki fleet of 800+ devices and 1200+ pkgsinfo files. The guards have caught real problems; the docs below explain what, why, and how to reproduce.

## Contents

| Path                              | Purpose                                                         |
|-----------------------------------|------------------------------------------------------------------|
| `.min-version`                    | Enforced floor — every hook warns if older than this YYYY.MM.DD. |
| `lib/common.sh`                   | Shared helpers: version check, worktree linker, size guard, lock. |
| `lib/pkgsinfo-lint.py`            | Structural pkgsinfo validator (Munki-native schema).             |
| `azure/pre-commit`                | Validate pkgsinfo, auto-download missing pkgs, block bad commits. |
| `azure/pre-push`                  | Sync changes to Azure Blob, remove orphans from blob storage.    |
| `azure/post-merge`                | Download packages referenced by newly-pulled pkgsinfo.           |
| `azure/post-rewrite`              | Safety net for `git rebase` / `git commit --amend`.              |
| `azure/post-checkout`             | (Optional) per-admin secrets bootstrap scaffolding.              |
| `aws/*`                           | Parallel implementation against S3.                              |

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
5. **Structural pkgsinfo linter** — catches typos, wrong-case keys, invalid `installer_type`, `nopkg` install-loop traps, `RequireRestart`+`unattended_install` combos. 47 valid top-keys, full enum validation. See `lib/pkgsinfo-lint.py` for the full schema.
6. **`makecatalogs` validation** — parse errors, missing required keys, invalid item locations, empty catalogs.
7. **Missing-pkg auto-download** — pulls the referenced installer items from cloud storage; blocks only if still missing after download.
8. **Orphan pkg cleanup** — main branch only, capped at 10 deletions (prevents catastrophic mass-delete on partial branches).

### `pre-push`

1. **Branch gate** — only syncs when pushing `main`; other branches push freely.
2. **Fast-forward check** — pulls if behind; aborts on non-FF.
3. **`makecatalogs` re-validate** — one last check against reality.
4. **Targeted or bulk sync** — uploads only the files referenced by changed pkgsinfo, or everything with `--sync`.
5. **MD5 hash metadata** — Azure uploads use `--put-md5` so future `--compare-hash=MD5` runs actually have something to compare.
6. **Azure/S3 orphan cleanup** — removes blob/object storage entries not referenced by any committed pkgsinfo (main only).

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

Catalogs and required-by-installer-type rules are validated. Full schema is at the top of `pkgsinfo-lint.py` — edit freely if your Munki deployment diverges.

## Extending

**New pkgsinfo key / installer type** → edit `VALID_TOP_KEYS` / `CURRENT_INSTALLER_TYPES` in `lib/pkgsinfo-lint.py`. Add a source reference (file + line) so future-you knows where the schema came from.

**New caching server** → set `MUNKI_CACHING_SERVERS="host1:host2"` in your env. No code change needed.

**New binary-cache path** → add to `MUNKI_ALLOW_BINARY_PATHS`. No code change needed.

**New structural check** → add a test in `issues_for_file` in `pkgsinfo-lint.py`. Always return a specific, human-readable error — no `validation failed`.

When bumping functionality that admins must have, bump `.min-version` and the hook's own `HOOK_VERSION=` stamp in the same commit.

## Why bother?

The simplest version of this system — `git commit` → `azcopy sync` — is what most teams start with. It works until:

- Two admins push at once and azcopy races itself into an inconsistent state.
- Someone commits a YAML with a typo in `installer_type` and `makecatalogs` silently excludes it from the catalog.
- A fresh clone tries to run `makecatalogs` on 1000 pkgsinfo files and fails with "missing installer" for every one of them.
- `git rebase` during a conflict resolution accidentally forks a branch and a `pre-push` deletes blob storage files the other branch still references.

Each of the guards in these hooks exists because one of those scenarios actually happened. The lint, the cap, the lock, the version check, the worktree link — every one paid for itself in avoided incidents.

Read them, steal them, adapt them to your org. Issues and PRs welcome.
