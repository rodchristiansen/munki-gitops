#!/usr/bin/env zsh
# Offline test of githooks/aws/pre-push-pr-packages with a fake aws CLI.
set -euo pipefail

HOOK="${0:A:h:h}/aws/pre-push-pr-packages"
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT

git -C "$FIXTURE" init -q -b main
git -C "$FIXTURE" config user.email hooks@example.invalid
git -C "$FIXTURE" config user.name 'Hook Test'
mkdir -p "$FIXTURE/deployment/pkgsinfo/apps" "$FIXTURE/bin" "$FIXTURE/state"
print initial > "$FIXTURE/README"
git -C "$FIXTURE" add README
git -C "$FIXTURE" commit -qm initial
BASE_SHA=$(git -C "$FIXTURE" rev-parse HEAD)
git -C "$FIXTURE" update-ref refs/remotes/origin/main "$BASE_SHA"

EXPECTED_HASH=$(print -n package | shasum -a 256 | awk '{print $1}')
OTHER_HASH=$(printf '0%.0s' {1..64})
cat > "$FIXTURE/deployment/pkgsinfo/apps/Test.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>name</key>
<string>Test</string>
<key>installer_item_hash</key>
<string>$EXPECTED_HASH</string>
<key>installer_item_location</key>
<string>apps/Test.pkg</string>
</dict></plist>
PLIST
git -C "$FIXTURE" add deployment
git -C "$FIXTURE" commit -qm package
HEAD_SHA=$(git -C "$FIXTURE" rev-parse HEAD)

# State: exists (true/false), etag, sha256 ("None" when unset).
cat > "$FIXTURE/bin/aws" <<'SH'
#!/bin/sh
S="$HOOK_TEST_STATE"
printf '%s\n' "$*" >> "$S/aws-args"
case "$*" in
  *"sts get-caller-identity"*) exit 0 ;;
  *"s3api head-object"*ContentLength*)
    [ "$(cat "$S/exists")" = true ] || exit 254
    printf '%s\t%s\n' "$(cat "$S/size")" "$(cat "$S/sha256")" ;;
  *"s3api head-object"*)
    [ "$(cat "$S/exists")" = true ] || { echo 'An error occurred (404) when calling the HeadObject operation: Not Found' >&2; exit 254; }
    printf '"%s"\t%s\n' "$(cat "$S/etag")" "$(cat "$S/sha256")" ;;
  *"s3api create-multipart-upload"*)
    printf '%s\n' "$*" | grep -q 'sha256=' || exit 1
    echo upload-1 ;;
  *"s3api upload-part"*)
    n=$(printf '%s\n' "$*" | sed -E 's/.*--part-number ([0-9]+).*/\1/')
    [ -f "$S/fail-part" ] && [ "$n" = "$(cat "$S/fail-part")" ] && exit 254
    if [ -f "$S/appear" ]; then echo true > "$S/exists"; cat "$S/race-sha" > "$S/sha256"; fi
    body=$(printf '%s\n' "$*" | sed -E 's/.*--body ([^ ]+).*/\1/')
    cat "$body" >> "$S/assembled"
    echo "\"etag$n\"" ;;
  *"s3api complete-multipart-upload"*)
    [ "$(cat "$S/exists")" = true ] && { echo 'PreconditionFailed (412)' >&2; exit 254; }
    file=$(printf '%s\n' "$*" | sed -E 's#.*--multipart-upload file://([^ ]+).*#\1#')
    cp "$file" "$S/parts.json"
    echo true > "$S/exists"
    grep -o 'sha256=[0-9a-f]*' "$S/aws-args" | head -1 | cut -d= -f2 > "$S/sha256"
    if [ -f "$S/short-object" ]; then echo 1 > "$S/size"; else wc -c < "$S/assembled" | tr -d ' ' > "$S/size"; fi
    : > "$S/completed" ;;
  *"s3api abort-multipart-upload"*) : > "$S/aborted" ;;
  *"s3api put-object"*)
    if [ -f "$S/fail-upload" ]; then echo true > "$S/exists"; cat "$S/race-sha" > "$S/sha256"; exit 254; fi
    : > "$S/uploaded" ;;
  *"s3api copy-object"*) : > "$S/backfilled" ;;
esac
exit 0
SH
chmod +x "$FIXTURE/bin/aws"

export HOOK_TEST_STATE="$FIXTURE/state" MUNKI_S3_BUCKET=bucket MUNKI_S3_PREFIX=repo
REF_LINE="refs/heads/test $HEAD_SHA refs/heads/test $BASE_SHA"
run_hook() {
  (cd "$FIXTURE" && print -r -- "$REF_LINE" | PATH="$FIXTURE/bin:$PATH" zsh -f "$HOOK") >/dev/null 2>&1
}
reset_remote() {
  print -r -- "${1:-false}" > "$HOOK_TEST_STATE/exists"
  print -r -- "${2:-None}" > "$HOOK_TEST_STATE/sha256"
  print -r -- "${3:-0123abcd}" > "$HOOK_TEST_STATE/etag"
  rm -f "$HOOK_TEST_STATE/uploaded" "$HOOK_TEST_STATE/backfilled" "$HOOK_TEST_STATE/aws-args" \
    "$HOOK_TEST_STATE/fail-upload" "$HOOK_TEST_STATE/race-sha" "$HOOK_TEST_STATE/assembled" \
    "$HOOK_TEST_STATE/completed" "$HOOK_TEST_STATE/aborted" "$HOOK_TEST_STATE/fail-part" \
    "$HOOK_TEST_STATE/parts.json" "$HOOK_TEST_STATE/short-object" "$HOOK_TEST_STATE/appear" \
    "$HOOK_TEST_STATE/size"
}
fail() { print -u2 "FAIL: $1"; exit 1; }

reset_remote false
run_hook && fail 'a package absent locally and remotely must block'

mkdir -p "$FIXTURE/deployment/pkgs/apps"
print -n package > "$FIXTURE/deployment/pkgs/apps/Test.pkg"
reset_remote false
run_hook || fail 'a local package should upload'
[[ -f "$HOOK_TEST_STATE/uploaded" ]] || fail 'expected an upload'
grep -q -- "--key repo/deployment/pkgs/apps/Test.pkg" "$HOOK_TEST_STATE/aws-args" || fail 'wrong object key'
grep -q -- "--if-none-match \*" "$HOOK_TEST_STATE/aws-args" || fail 'upload must be create-only'
grep -q -- "sha256=$EXPECTED_HASH" "$HOOK_TEST_STATE/aws-args" || fail 'upload must carry its SHA-256'

reset_remote true "$EXPECTED_HASH"
run_hook || fail 'a matching remote package should pass'
[[ ! -f "$HOOK_TEST_STATE/uploaded" ]] || fail 'a matching remote package must not re-upload'

reset_remote true "$OTHER_HASH"
run_hook && fail 'a different remote package at the same key must block'

LOCAL_MD5=$(openssl dgst -md5 "$FIXTURE/deployment/pkgs/apps/Test.pkg" | awk '{print $NF}')
reset_remote true None "$LOCAL_MD5"
run_hook || fail 'a legacy single-part object proven by ETag should pass'
[[ -f "$HOOK_TEST_STATE/backfilled" ]] || fail 'expected the legacy SHA-256 backfill'

reset_remote true None "$LOCAL_MD5-2"
run_hook && fail 'a multipart ETag proves nothing and must block'

reset_remote false
: > "$HOOK_TEST_STATE/fail-upload"
print -r -- "$EXPECTED_HASH" > "$HOOK_TEST_STATE/race-sha"
run_hook || fail 'losing a create race to the same bytes should pass'
reset_remote false
: > "$HOOK_TEST_STATE/fail-upload"
print -r -- "$OTHER_HASH" > "$HOOK_TEST_STATE/race-sha"
run_hook && fail 'losing a create race to different bytes must block'

# Multipart: a 3-byte part size splits the 7-byte package into three parts.
export MUNKI_S3_MULTIPART_THRESHOLD=4 MUNKI_S3_PART_SIZE=3
reset_remote false
run_hook || fail 'a large package should go up as a multipart upload'
[[ -f "$HOOK_TEST_STATE/completed" ]] || fail 'expected a completed multipart upload'
[[ ! -f "$HOOK_TEST_STATE/uploaded" ]] || fail 'a large package must not use a single PUT'
[[ ! -f "$HOOK_TEST_STATE/aborted" ]] || fail 'a successful multipart upload must not abort'
[[ "$(cat "$HOOK_TEST_STATE/assembled")" == package ]] || fail 'parts must reassemble to the package'
python3 -c 'import json,sys; p=json.load(open(sys.argv[1]))["Parts"]; assert [x["PartNumber"] for x in p]==[1,2,3] and p[0]["ETag"]=="\"etag1\"" and all(x["ChecksumSHA256"] for x in p)' \
  "$HOOK_TEST_STATE/parts.json" || fail 'the part list must be valid with ETags and checksums'
grep -q -- "create-multipart-upload.*sha256=$EXPECTED_HASH" "$HOOK_TEST_STATE/aws-args" || fail 'multipart must carry its SHA-256'
grep -q -- "create-multipart-upload.*--checksum-algorithm SHA256" "$HOOK_TEST_STATE/aws-args" || fail 'multipart must checksum parts'
grep -q -- "upload-part.*--checksum-sha256" "$HOOK_TEST_STATE/aws-args" || fail 'every part must carry its checksum'
grep -q -- "complete-multipart-upload.*--if-none-match \*" "$HOOK_TEST_STATE/aws-args" || fail 'multipart must be create-only'

reset_remote false
print 2 > "$HOOK_TEST_STATE/fail-part"
run_hook && fail 'a failed part must block'
[[ -f "$HOOK_TEST_STATE/aborted" ]] || fail 'a failed part must abort the upload'
[[ ! -f "$HOOK_TEST_STATE/completed" ]] || fail 'a failed part must not complete'

# The object appears while parts are uploading, so the completing call is refused.
reset_remote false
: > "$HOOK_TEST_STATE/appear"
print -r -- "$OTHER_HASH" > "$HOOK_TEST_STATE/race-sha"
run_hook && fail 'a completing call that loses to different bytes must block'
[[ -f "$HOOK_TEST_STATE/aborted" ]] || fail 'a refused completion must abort the upload'
[[ ! -f "$HOOK_TEST_STATE/completed" ]] || fail 'create-only completion must not overwrite'
reset_remote false
: > "$HOOK_TEST_STATE/appear"
print -r -- "$EXPECTED_HASH" > "$HOOK_TEST_STATE/race-sha"
run_hook || fail 'losing a multipart race to the same bytes should pass'

reset_remote false
: > "$HOOK_TEST_STATE/short-object"
run_hook && fail 'an object whose size does not match after upload must block'
unset MUNKI_S3_MULTIPART_THRESHOLD MUNKI_S3_PART_SIZE

sed -i.bak 's#<string>apps/Test.pkg</string>#<string>../outside.pkg</string>#' "$FIXTURE/deployment/pkgsinfo/apps/Test.plist"
rm -f "$FIXTURE/deployment/pkgsinfo/apps/Test.plist.bak"
git -C "$FIXTURE" commit -qam unsafe
REF_LINE="refs/heads/test $(git -C "$FIXTURE" rev-parse HEAD) refs/heads/test $BASE_SHA"
reset_remote false
run_hook && fail 'an unsafe location must block'
[[ ! -f "$HOOK_TEST_STATE/uploaded" ]] || fail 'an unsafe location uploaded'

print 'ok - aws pre-push-pr-packages'
