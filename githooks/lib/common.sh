#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
#  common.sh  –  shared helpers for Munki git hooks (bash, macOS)
#
#  Cloud-agnostic. Sourced by every Azure and AWS hook.
#
#  Provides:
#    check_hook_version       – warn if this hook is older than .min-version
#    should_skip_hook         – respect env bypass flags
#    link_worktree_sync_caches – symlink gitignored caches in linked worktrees
#    check_staged_binary_sizes – reject >50MB files outside pkgs/icons/
#    acquire_hook_lock        – exclusive lock across worktrees
#    release_hook_lock        – automatic via EXIT trap
#
#  Every hook should source this near the top:
#
#      HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
#      source "$HOOK_DIR/../lib/common.sh"
#      check_hook_version "pre-commit" "2026.04.19"
#      should_skip_hook && exit 0
#      link_worktree_sync_caches
#      acquire_hook_lock "pre-commit" || exit 1
#      check_staged_binary_sizes || exit 1
# ──────────────────────────────────────────────────────────────────────────────

# ── hook-version check ──────────────────────────────────────────────────────
# Compares the hook's own stamped version against .githooks/.min-version.
# Lexicographic YYYY.MM.DD comparison — no date math needed.
# Warns but never blocks (blocking would fail exactly the pull that would fix it).
check_hook_version() {
  local hook_name="$1"
  local hook_version="$2"

  local repo_root
  repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0

  # Support hooks living in githooks/azure/, githooks/aws/, or .githooks/
  local min_file=""
  for cand in \
    "$repo_root/.githooks/.min-version" \
    "$repo_root/githooks/.min-version"; do
    [[ -r "$cand" ]] && { min_file="$cand"; break; }
  done
  [[ -n "$min_file" ]] || return 0

  local min_version
  min_version=$(tr -d '[:space:]' < "$min_file")
  [[ -n "$min_version" ]] || return 0

  if [[ "$hook_version" < "$min_version" ]]; then
    echo "" >&2
    echo "WARNING: Your git hook ($hook_name, v$hook_version) is older than required (v$min_version)." >&2
    echo "         Pull latest: git pull --rebase origin main" >&2
    echo "" >&2
  fi
  return 0
}

# ── hook-bypass check ──────────────────────────────────────────────────────
# Env vars that skip the hook silently. Any one of them short-circuits.
#   GIT_NO_VERIFY=1            – git's own broad bypass
#   SKIP_MUNKI_HOOKS=1         – Munki-specific bypass (all hooks)
#   SKIP_POST_MERGE=1          – skip only post-merge
#   SKIP_PRE_PUSH=1            – skip only pre-push
#   DISABLE_CUSTOM_HOOKS=1     – kill switch
#
# Also detects `git pull --no-verify` and `git merge --no-verify` via the
# parent process command line.
should_skip_hook() {
  [[ "${GIT_NO_VERIFY:-}" == "1" ]] && return 0
  [[ "${SKIP_MUNKI_HOOKS:-}" == "1" ]] && return 0
  [[ "${DISABLE_CUSTOM_HOOKS:-}" == "1" ]] && return 0

  # Per-hook skip flags — caller should pass its own name as $1
  if [[ $# -gt 0 ]]; then
    local varname="SKIP_${1^^}"
    varname="${varname//-/_}"
    if [[ "${!varname:-}" == "1" ]]; then
      return 0
    fi
  fi

  if command -v ps >/dev/null 2>&1; then
    local parent_cmd
    parent_cmd=$(ps -o command= -p $PPID 2>/dev/null | head -1)
    if echo "$parent_cmd" | grep -qE "git.*(pull|merge|rebase).*--no-verify"; then
      return 0
    fi
  fi
  return 1
}

# ── worktree cache linking ──────────────────────────────────────────────────
# When a hook fires inside a linked worktree (created via `git worktree add`),
# gitignored binary caches — `deployment/pkgs` (potentially hundreds of GB),
# `deployment/icons`, `deployment/catalogs` — don't exist in the worktree.
# They live only under the primary worktree, populated from cloud storage.
#
# Without this helper, pre-commit's makecatalogs flags every pkgsinfo as
# referencing a missing pkg, and commits get blocked. Re-syncing from the
# cloud wastes bandwidth and storage.
#
# Fix: create zero-copy symlinks from the primary worktree. Idempotent.
# No-op when called from the primary worktree or outside a git repo.
#
# Which paths get linked is configurable via MUNKI_WORKTREE_LINK_PATHS
# (colon-separated, relative to repo root). Defaults cover the common case.
link_worktree_sync_caches() {
  local repo_root git_common current_gitdir primary_root
  repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0
  git_common="$(git rev-parse --git-common-dir 2>/dev/null)" || return 0
  current_gitdir="$(git rev-parse --git-dir 2>/dev/null)" || return 0

  # Primary worktree: --git-dir equals --git-common-dir. Bail if not linked.
  [[ -z "$git_common" || "$current_gitdir" == "$git_common" ]] && return 0

  # --git-common-dir points to <primary>/.git; primary root is its parent.
  primary_root="$(cd "$git_common" 2>/dev/null && cd .. && pwd)" || return 0
  [[ -d "$primary_root" && "$primary_root" != "$repo_root" ]] || return 0

  local link_paths="${MUNKI_WORKTREE_LINK_PATHS:-deployment/pkgs:deployment/icons:deployment/catalogs}"

  local linked_count=0
  local announced=false

  _mwlsc_announce() {
    [[ "$announced" == "true" ]] && return 0
    echo ""
    echo "Worktree detected — linking gitignored caches from primary:"
    echo "  Primary:  $primary_root"
    echo "  Worktree: $repo_root"
    announced=true
  }

  _mwlsc_link_one() {
    local rel="$1"
    local src="$primary_root/$rel"
    local dst="$repo_root/$rel"
    [[ -d "$src" ]] || return 0
    [[ -L "$dst" ]] && return 0   # already linked
    [[ -e "$dst" ]] && return 0   # existing regular dir — never clobber

    mkdir -p "$(dirname "$dst")"
    ln -s "$src" "$dst"

    if [[ -L "$dst" ]]; then
      _mwlsc_announce
      echo "  linked: $rel"
      linked_count=$((linked_count + 1))
    fi
  }

  IFS=':' read -ra paths <<< "$link_paths"
  for rel in "${paths[@]}"; do
    [[ -n "$rel" ]] && _mwlsc_link_one "$rel"
  done

  if [[ "$linked_count" -gt 0 ]]; then
    echo "  Total linked: $linked_count"
    echo ""
  fi
  return 0
}

# ── binary-size guard ──────────────────────────────────────────────────────
# Reject staged files > MUNKI_MAX_FILE_SIZE_MB (default 50) unless they live
# under a recognised binary-cache path. Cheap belt-and-suspenders so a careless
# `git add` doesn't blow up the repo size.
#
# Override allow paths with MUNKI_ALLOW_BINARY_PATHS (colon-separated regexes).
# Defaults cover deployment/pkgs/ and deployment/icons/.
check_staged_binary_sizes() {
  local max_mb="${MUNKI_MAX_FILE_SIZE_MB:-50}"
  local max_bytes=$((max_mb * 1024 * 1024))

  local allow_paths="${MUNKI_ALLOW_BINARY_PATHS:-^deployment/pkgs/:^deployment/icons/}"
  IFS=':' read -ra allow_regexes <<< "$allow_paths"

  local offenders=()
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    [[ ! -f "$f" ]] && continue

    local allowed=false
    for re in "${allow_regexes[@]}"; do
      [[ -z "$re" ]] && continue
      if [[ "$f" =~ $re ]]; then allowed=true; break; fi
    done
    [[ "$allowed" == "true" ]] && continue

    local sz
    sz=$(stat -f %z "$f" 2>/dev/null || stat -c %s "$f" 2>/dev/null || echo 0)
    if (( sz > max_bytes )); then
      local sz_mb=$((sz / 1024 / 1024))
      offenders+=("${sz_mb}MB  $f")
    fi
  done < <(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)

  if (( ${#offenders[@]} > 0 )); then
    echo "" >&2
    echo "COMMIT BLOCKED: staged file(s) > ${max_mb}MB outside recognised binary paths:" >&2
    printf '  • %s\n' "${offenders[@]}" >&2
    echo "" >&2
    echo "If this is intentional:" >&2
    echo "  • Move the file under deployment/pkgs/ (and add a pkgsinfo entry)" >&2
    echo "  • Or unstage it:  git restore --staged <file>" >&2
    echo "  • Or widen MUNKI_ALLOW_BINARY_PATHS / MUNKI_MAX_FILE_SIZE_MB" >&2
    echo "" >&2
    return 1
  fi
  return 0
}

# ── concurrency lock ───────────────────────────────────────────────────────
# Prevents overlap between pre-commit and pre-push. Uses the primary git dir
# so worktrees share one lock.
#
# Stale-lock detection: if the PID stored in the lock isn't running, steal
# the lock instead of blocking indefinitely — handles the case where a
# previous hook crashed before releasing.
_hook_lock_file=""

acquire_hook_lock() {
  local hook_name="${1:-hook}"
  local git_common
  git_common="$(git rev-parse --git-common-dir 2>/dev/null)" || return 0

  mkdir -p "$git_common/logs"
  local lock_dir="$git_common/logs/.hook.lockdir"

  # Stale-lock detection — if the PID that holds the lock isn't running,
  # take it over. Survives hook crashes without manual cleanup.
  if [[ -d "$lock_dir" ]]; then
    local stored_pid
    stored_pid=$(cat "$lock_dir/pid" 2>/dev/null || echo "")
    if [[ -n "$stored_pid" ]] && ! kill -0 "$stored_pid" 2>/dev/null; then
      echo "  Stealing stale hook lock (pid $stored_pid no longer running)" >&2
      rm -rf "$lock_dir" 2>/dev/null || true
    fi
  fi

  if ! mkdir "$lock_dir" 2>/dev/null; then
    local other_pid
    other_pid=$(cat "$lock_dir/pid" 2>/dev/null || echo "?")
    echo "" >&2
    echo "Another hook is running (pid $other_pid, lock: $lock_dir)." >&2
    echo "Try again in a moment. If stuck: rm -rf '$lock_dir'" >&2
    echo "" >&2
    return 1
  fi

  echo $$ > "$lock_dir/pid" 2>/dev/null || true
  _hook_lock_file="$lock_dir"
  trap release_hook_lock EXIT INT TERM
  return 0
}

release_hook_lock() {
  [[ -z "$_hook_lock_file" ]] && return 0
  rm -rf "$_hook_lock_file" 2>/dev/null || true
  _hook_lock_file=""
}
