#!/usr/bin/env zsh
# Offline test of githooks/azure/pre-push-pr-packages with fake az and azcopy.
set -euo pipefail

HOOK="${0:A:h:h}/azure/pre-push-pr-packages"
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT

git -C "$FIXTURE" init -q -b main
git -C "$FIXTURE" config user.email hooks@example.invalid
git -C "$FIXTURE" config user.name 'Hook Test'
mkdir -p "$FIXTURE/deployment/pkgsinfo/apps/managed" "$FIXTURE/bin" "$FIXTURE/state"
print initial > "$FIXTURE/README"
git -C "$FIXTURE" add README
git -C "$FIXTURE" commit -qm initial
BASE_SHA=$(git -C "$FIXTURE" rev-parse HEAD)
git -C "$FIXTURE" update-ref refs/remotes/origin/main "$BASE_SHA"

EXPECTED_HASH=$(print -n package | shasum -a 256 | awk '{print $1}')
OTHER_HASH=$(printf '0%.0s' {1..64})
cat > "$FIXTURE/deployment/pkgsinfo/apps/Test.yaml" <<YAML
name: Test
installer_item_hash: $EXPECTED_HASH
installer_item_location: apps/Test.pkg
YAML
cat > "$FIXTURE/deployment/pkgsinfo/apps/Script.yaml" <<YAML
name: Script
installer_type: nopkg
YAML
cat > "$FIXTURE/deployment/pkgsinfo/apps/managed/Example.yaml" <<YAML
name: Example
source: apple_vpp
YAML
git -C "$FIXTURE" add deployment
git -C "$FIXTURE" commit -qm package
HEAD_SHA=$(git -C "$FIXTURE" rev-parse HEAD)

cat > "$FIXTURE/bin/az" <<'SH'
#!/bin/sh
case "$*" in
  *"account get-access-token"*) exit 0 ;;
  *"storage blob exists"*) cat "$HOOK_TEST_STATE/exists" ;;
  *"storage blob show"*"metadata.sha256"*) cat "$HOOK_TEST_STATE/sha256" ;;
  *"storage blob show"*"contentSettings.contentMd5"*) cat "$HOOK_TEST_STATE/md5" ;;
  *"storage blob show"*"properties.etag"*) printf 'etag-1\n' ;;
  *"storage blob metadata update"*)
    for arg in "$@"; do
      case "$arg" in sha256=*) printf '%s\n' "${arg#sha256=}" > "$HOOK_TEST_STATE/sha256" ;; esac
    done
    : > "$HOOK_TEST_STATE/backfilled" ;;
esac
exit 0
SH
cat > "$FIXTURE/bin/azcopy" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$HOOK_TEST_STATE/azcopy-args"
if [ -f "$HOOK_TEST_STATE/fail-upload" ]; then
  cat "$HOOK_TEST_STATE/race-sha" > "$HOOK_TEST_STATE/sha256"
  exit 1
fi
: > "$HOOK_TEST_STATE/uploaded"
exit 0
SH
chmod +x "$FIXTURE/bin/az" "$FIXTURE/bin/azcopy"

export HOOK_TEST_STATE="$FIXTURE/state"
# A new branch (remote sha all zeros) diffs from its merge-base with origin/main.
REF_LINE="refs/heads/test $HEAD_SHA refs/heads/test 0000000000000000000000000000000000000000"

run_hook() {
  (cd "$FIXTURE" && print -r -- "$REF_LINE" | PATH="$FIXTURE/bin:$PATH" zsh -f "$HOOK") >/dev/null 2>&1
}
reset_remote() {
  print -r -- "${1:-false}" > "$HOOK_TEST_STATE/exists"
  print -r -- "${2:-}" > "$HOOK_TEST_STATE/sha256"
  print -r -- "${3:-}" > "$HOOK_TEST_STATE/md5"
  rm -f "$HOOK_TEST_STATE/uploaded" "$HOOK_TEST_STATE/backfilled" \
    "$HOOK_TEST_STATE/azcopy-args" "$HOOK_TEST_STATE/fail-upload" "$HOOK_TEST_STATE/race-sha"
}
fail() { print -u2 "FAIL: $1"; exit 1; }

reset_remote false
run_hook && fail 'a package absent locally and remotely must block'

mkdir -p "$FIXTURE/deployment/pkgs/apps"
print -n package > "$FIXTURE/deployment/pkgs/apps/Test.pkg"
reset_remote false
run_hook || fail 'a local package should upload'
[[ -f "$HOOK_TEST_STATE/uploaded" ]] || fail 'expected an upload'
grep -q -- '--overwrite=false' "$HOOK_TEST_STATE/azcopy-args" || fail 'upload must be create-only'
grep -q -- "--metadata=sha256=$EXPECTED_HASH" "$HOOK_TEST_STATE/azcopy-args" || fail 'upload must carry its SHA-256'
[[ $(wc -l < "$HOOK_TEST_STATE/azcopy-args") -eq 1 ]] || fail 'nopkg and managed descriptors must not upload'

print -n other > "$FIXTURE/deployment/pkgs/apps/Test.pkg"
reset_remote false
run_hook && fail 'a local package with the wrong SHA-256 must block'
print -n package > "$FIXTURE/deployment/pkgs/apps/Test.pkg"

reset_remote true "$EXPECTED_HASH"
run_hook || fail 'a matching remote package should pass'
[[ ! -f "$HOOK_TEST_STATE/uploaded" ]] || fail 'a matching remote package must not re-upload'

reset_remote true "$OTHER_HASH"
run_hook && fail 'a different remote package at the same path must block'
[[ ! -f "$HOOK_TEST_STATE/uploaded" ]] || fail 'a collision must not overwrite'

LOCAL_MD5=$(openssl dgst -md5 -binary "$FIXTURE/deployment/pkgs/apps/Test.pkg" | base64 | tr -d '\n')
reset_remote true '' "$LOCAL_MD5"
run_hook || fail 'a legacy blob proven by MD5 should pass'
[[ -f "$HOOK_TEST_STATE/backfilled" ]] || fail 'expected the legacy SHA-256 backfill'

reset_remote true '' 'not-the-md5'
run_hook && fail 'an unproven legacy blob must block'

rm -f "$FIXTURE/deployment/pkgs/apps/Test.pkg"
git -C "$FIXTURE" update-ref refs/remotes/origin/main "$HEAD_SHA"
REF_LINE="refs/heads/test $HEAD_SHA refs/heads/test $BASE_SHA"
reset_remote true
run_hook || fail 'origin/main should prove a legacy blob'
[[ -f "$HOOK_TEST_STATE/backfilled" ]] || fail 'expected the backfill from origin/main'
git -C "$FIXTURE" update-ref refs/remotes/origin/main "$BASE_SHA"
REF_LINE="refs/heads/test $HEAD_SHA refs/heads/test 0000000000000000000000000000000000000000"

print -n package > "$FIXTURE/deployment/pkgs/apps/Test.pkg"
reset_remote false
: > "$HOOK_TEST_STATE/fail-upload"
print -r -- "$EXPECTED_HASH" > "$HOOK_TEST_STATE/race-sha"
run_hook || fail 'losing a create race to the same bytes should pass'
reset_remote false
: > "$HOOK_TEST_STATE/fail-upload"
print -r -- "$OTHER_HASH" > "$HOOK_TEST_STATE/race-sha"
run_hook && fail 'losing a create race to different bytes must block'

for bad in '../../etc/passwd' '/etc/passwd' 'apps/../../x.pkg' 'apps//x.pkg'; do
  sed -i.bak "s#^installer_item_location:.*#installer_item_location: $bad#" "$FIXTURE/deployment/pkgsinfo/apps/Test.yaml"
  rm -f "$FIXTURE/deployment/pkgsinfo/apps/Test.yaml.bak"
  git -C "$FIXTURE" commit -qam "unsafe $bad"
  REF_LINE="refs/heads/test $(git -C "$FIXTURE" rev-parse HEAD) refs/heads/test 0000000000000000000000000000000000000000"
  reset_remote false
  run_hook && fail "unsafe location '$bad' must block"
  [[ ! -f "$HOOK_TEST_STATE/uploaded" ]] || fail "unsafe location '$bad' uploaded"
done

PRE_PUSH="${0:A:h:h}/azure/pre-push"
lock_line=$(grep -n 'acquire_hook_lock "pre-push"' "$PRE_PUSH" | cut -d: -f1)
helper_line=$(grep -n '"$HOOK_DIR/pre-push-pr-packages"' "$PRE_PUSH" | head -1 | cut -d: -f1)
[[ -n "$helper_line" && "$lock_line" -lt "$helper_line" ]] || fail 'pre-push must call the helper after taking the lock'
grep -q 'exec "$HOOK_DIR/pre-push-pr-packages"' "$PRE_PUSH" && fail 'exec would skip the lock release trap'

print 'ok - azure pre-push-pr-packages'
