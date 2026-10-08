#!/usr/bin/env zsh
# Offline tests for the Munki preflight. Fakes curl, defaults and plutil on
# PATH, sources the script without running main, and checks the decisions it
# makes. Runs on macOS and Linux.
set -euo pipefail

PREFLIGHT="${0:A:h:h}/payload/preflight"
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT
mkdir -p "$FIXTURE/bin" "$FIXTURE/state"
export FAKE_STATE="$FIXTURE/state"

# curl: -I requests print $FAKE_STATE/headers-<host>; downloads copy
# $FAKE_STATE/csv to the -o target and print the status in $FAKE_STATE/status.
cat > "$FIXTURE/bin/curl" <<'SH'
#!/bin/sh
out=""; url=""; head=false; config=""
while [ $# -gt 0 ]; do
  case "$1" in
    -K) config="$2"; shift ;;
    -o) out="$2"; shift ;;
    -Is) head=true ;;
    -w|-m) shift ;;
    http*) url="$1" ;;
  esac
  shift
done
[ -n "$config" ] && cp "$config" "$FAKE_STATE/last-config"
host=$(printf '%s' "$url" | sed -E 's#^[a-z]+://([^/]+)/.*#\1#')
if $head; then
  [ -f "$FAKE_STATE/headers-$host" ] || exit 7
  cat "$FAKE_STATE/headers-$host"; exit 0
fi
printf '%s\n' "$url" > "$FAKE_STATE/last-url"
cp "$FAKE_STATE/csv" "$out"
cat "$FAKE_STATE/status"
exit "$(cat "$FAKE_STATE/curl-rc" 2>/dev/null || echo 0)"
SH

# defaults: a flat key=value store per domain under $FAKE_STATE/defaults.
cat > "$FIXTURE/bin/defaults" <<'SH'
#!/bin/sh
verb="$1"; domain=$(basename "$2" .plist); key="$3"
store="$FAKE_STATE/defaults-$domain"
touch "$store"
case "$verb" in
  read)  line=$(grep "^$key=" "$store" | tail -1); [ -n "$line" ] || exit 1; printf '%s\n' "${line#*=}" ;;
  write) [ "$4" = "-string" ] && val="$5" || val="$4"; grep -v "^$key=" "$store" > "$store.tmp" || true; mv "$store.tmp" "$store"; printf '%s=%s\n' "$key" "$val" >> "$store" ;;
esac
SH

# plutil -extract AdditionalHttpHeaders.N raw: one header per line in
# $FAKE_STATE/headers.
cat > "$FIXTURE/bin/plutil" <<'SH'
#!/bin/sh
n=$(printf '%s' "$2" | sed 's/.*\.//')
line=$(sed -n "$((n + 1))p" "$FAKE_STATE/headers" 2>/dev/null)
[ -n "$line" ] || exit 1
printf '%s\n' "$line"
SH
chmod +x "$FIXTURE/bin/"*
export PATH="$FIXTURE/bin:$PATH"

fail() { print -u2 "FAIL: $1"; exit 1; }

MUNKI_PREFLIGHT_SOURCE_ONLY=1 source "$PREFLIGHT"
PREFS="$FIXTURE/ManagedInstalls"
SECURE_PREFS="$FIXTURE/secure/ManagedInstalls.plist"
INVENTORY_YAML="$FIXTURE/Inventory.yaml"
MIRROR_HOSTS=(m1.test m2.test)
CLOUD_REPO_URL="https://cloud.test/deployment"

# Headers reach curl through its config file, quoted, never as arguments.
mkdir -p "$FIXTURE/secure"; : > "$SECURE_PREFS"
print -r -- 'Authorization: Basic c2VjcmV0' > "$FAKE_STATE/headers"
load_request_headers
grep -qx 'header = "Authorization: Basic c2VjcmV0"' "$CURL_CONFIG" || fail 'header not written to the curl config'

# Repository: a stale first mirror is skipped for a fresh second one.
now=$(LC_ALL=C date -u '+%a, %d %b %Y %T GMT')
old=$(LC_ALL=C date -u -r $(( $(date +%s) - 72 * 3600 )) '+%a, %d %b %Y %T GMT' 2>/dev/null \
  || LC_ALL=C date -u -d '@'$(( $(date +%s) - 72 * 3600 )) '+%a, %d %b %Y %T GMT')
printf 'HTTP/1.1 200 OK\r\nLast-Modified: %s\r\n' "$old" > "$FAKE_STATE/headers-m1.test"
printf 'HTTP/1.1 200 OK\r\nLast-Modified: %s\r\n' "$now" > "$FAKE_STATE/headers-m2.test"
if [[ "$(uname)" == Darwin ]]; then
  REPO_URL=""; configure_repository 2>/dev/null
  [[ "$REPO_URL" == "https://m2.test/deployment" ]] || fail "expected the fresh mirror, got $REPO_URL"
  grep -q Authorization "$FAKE_STATE/last-config" || fail 'https probe sent no credential'

  # A plain-http mirror is probed without the credential.
  MIRROR_SCHEME=http
  REPO_URL=""; configure_repository 2>/dev/null
  [[ "$REPO_URL" == "http://m2.test/deployment" ]] || fail "expected the http mirror, got $REPO_URL"
  if grep -q Authorization "$FAKE_STATE/last-config"; then fail 'credential sent over http'; fi
  MIRROR_SCHEME=https

  rm -f "$FAKE_STATE/headers-m2.test"
  REPO_URL=""; configure_repository 2>/dev/null
  [[ "$REPO_URL" == "$CLOUD_REPO_URL" ]] || fail "expected the cloud fallback, got $REPO_URL"
fi
REPO_URL="$CLOUD_REPO_URL"

# Inventory: columns are found by name, whatever order the projection uses.
cat > "$FAKE_STATE/csv" <<'CSV'
serial,catalog,area,location,asset,usage,status,allocation,username,platform,hostname,fleet
OTHER0001,Staff,IT,B1101,A-1,Assigned,Active,Someone Else,se@example.org,Macintosh,SOMEONE,
SAMPLEMAC001,Staff,IT,B1101,A-2,Assigned,Active,Alex Rivera,arivera@example.org,Macintosh,ALEXRIVERA,
SAMPLEMAC009,Curriculum,Design,,A-9,Shared,Active,Lab 9,,Macintosh,LAB9,
SAMPLEMAC010,Staff,IT,B1101,A-10,Assigned,Retired,Old Mac,,Macintosh,OLDMAC,
CSV
print 200 > "$FAKE_STATE/status"
fetch_inventory_row SAMPLEMAC001 2>/dev/null || fail 'row not found'
[[ "${ROW[hostname]}" == ALEXRIVERA && "${ROW[area]}" == IT && "${ROW[fleet]}" == "" ]] || fail 'row fields misread'
[[ "$(cat "$FAKE_STATE/last-url")" == "$CLOUD_REPO_URL/enroll/computers.csv" ]] || fail 'CSV fetched from the wrong source'
[[ "$(derive_client_identifier)" == "Assigned/Staff/IT/B1101/ALEXRIVERA" ]] || fail 'wrong ClientIdentifier'

fetch_inventory_row SAMPLEMAC009 2>/dev/null
[[ "$(derive_client_identifier)" == "Shared/Curriculum/Design/LAB9" ]] || fail 'empty location not dropped'

# Inventory is never read from a plain-http mirror.
REPO_URL="http://m2.test/deployment"
fetch_inventory_row SAMPLEMAC001 2>/dev/null || fail 'row not found via the cloud fallback'
[[ "$(cat "$FAKE_STATE/last-url")" == "$CLOUD_REPO_URL/enroll/computers.csv" ]] || fail 'CSV read over http'
REPO_URL="$CLOUD_REPO_URL"

# A row value that is not a plain path segment never reaches ClientIdentifier.
fetch_inventory_row SAMPLEMAC001 2>/dev/null
ROW[area]="../Other"
if derive_client_identifier 2>/dev/null; then fail 'path traversal accepted'; fi

# A truncated download (curl timed out after a 200) is not a download.
print 28 > "$FAKE_STATE/curl-rc"
if fetch_inventory_row SAMPLEMAC001 2>/dev/null; then fail 'truncated download accepted'; fi
rm -f "$FAKE_STATE/curl-rc"

if fetch_inventory_row NOTINCSV 2>/dev/null; then fail 'unknown serial matched'; fi

# Inventory.yaml round-trips the status the decommission path reads.
fetch_inventory_row SAMPLEMAC010 2>/dev/null
write_inventory_yaml
[[ "$(read_inventory_yaml status)" == Retired ]] || fail 'status not round-tripped'
in_list "Returned Lease End" "${RETIRE_STATUSES[@]}" || fail 'multi-word status not matched'

# ClientIdentifier ignores a manifest extension when comparing.
defaults write "$PREFS" ClientIdentifier "Assigned/Staff/IT/B1101/ALEXRIVERA.yaml"
set_client_identifier "Assigned/Staff/IT/B1101/ALEXRIVERA" 2>/dev/null
[[ "$(defaults read "$PREFS" ClientIdentifier)" == "Assigned/Staff/IT/B1101/ALEXRIVERA.yaml" ]] || fail 'rewrote an equivalent ClientIdentifier'
set_client_identifier "Decommission" 2>/dev/null
[[ "$(defaults read "$PREFS" ClientIdentifier)" == Decommission ]] || fail 'ClientIdentifier not changed'

[[ "$(clean_hostname 'Lab Mac (2)')" == LabMac ]] || fail 'hostname not cleaned'

print 'ok - preflight'
