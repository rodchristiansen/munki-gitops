#!/usr/bin/env zsh
# Offline test for apache-tls-sync.sh. Fakes az, htpasswd and httpd, and checks
# that the first run installs everything and reloads, that an unchanged second
# run does not reload, and that a missing certificate keeps the old one.
set -euo pipefail

SYNC="${0:A:h:h}/payload/Management/ApacheTLS/apache-tls-sync.sh"
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT
mkdir -p "$FIXTURE/bin" "$FIXTURE/secrets" "$FIXTURE/state"
export FAKE="$FIXTURE"

cat > "$FIXTURE/bin/az" <<'SH'
#!/bin/sh
case "$1" in
  account) exit 0 ;;
  keyvault)
    while [ $# -gt 0 ]; do [ "$1" = "-n" ] && name="$2"; shift; done
    [ -f "$FAKE/secrets/$name" ] || exit 3
    cat "$FAKE/secrets/$name" ;;
esac
SH
# htpasswd: -v succeeds when the file holds "user:pass"; -c writes it.
cat > "$FIXTURE/bin/htpasswd" <<'SH'
#!/bin/sh
read -r pw
case "$1" in
  -v) [ -f "$3" ] && grep -qx "$4:$pw" "$3" ;;
  -B) printf '%s:%s\n' "$5" "$pw" > "$4" ;;
esac
SH
cat > "$FIXTURE/bin/httpd" <<'SH'
#!/bin/sh
[ "$1" = "-k" ] && echo reload >> "$FAKE/reloads"
exit 0
SH
chmod +x "$FIXTURE/bin/"*
: > "$FIXTURE/sp.pem"

printf -- '-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n' > "$FIXTURE/secrets/CachingServerCertificate-mirror1-example-org"
printf -- '-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n' > "$FIXTURE/secrets/CachingServerKey-mirror1-example-org"
print repo > "$FIXTURE/secrets/MunkiUsername"
print s3cret > "$FIXTURE/secrets/MunkiPassword"

run() {
  AZ="$FIXTURE/bin/az" SP_PEM="$FIXTURE/sp.pem" TLS_DIR="$FIXTURE/tls" STATE_DIR="$FIXTURE/state" \
    HTTPD="$FIXTURE/bin/httpd" HTPASSWD="$FIXTURE/bin/htpasswd" HTTPD_CONF="$FIXTURE/none" SERVER_FQDN=mirror1.example.org \
    zsh "$SYNC" >/dev/null
}
fail() { print -u2 "FAIL: $1"; exit 1; }
reloads() { [[ -f "$FIXTURE/reloads" ]] && wc -l < "$FIXTURE/reloads" | tr -d ' ' || print 0; }

run
grep -q leaf "$FIXTURE/tls/server.crt" || fail 'certificate not installed'
[[ "$(stat -f %Lp "$FIXTURE/tls/server.key" 2>/dev/null || stat -c %a "$FIXTURE/tls/server.key")" == 600 ]] || fail 'key not 0600'
grep -qx 'repo:s3cret' "$FIXTURE/tls/htpasswd" || fail 'htpasswd not written'
[[ "$(reloads)" == 1 ]] || fail 'first run did not reload'

run
[[ "$(reloads)" == 1 ]] || fail 'an unchanged run reloaded Apache'

print rotated > "$FIXTURE/secrets/MunkiPassword"
run
grep -qx 'repo:rotated' "$FIXTURE/tls/htpasswd" || fail 'rotated password not picked up'
[[ "$(reloads)" == 2 ]] || fail 'rotation did not reload'

rm "$FIXTURE/secrets/CachingServerCertificate-mirror1-example-org"
run
grep -q leaf "$FIXTURE/tls/server.crt" || fail 'lost the certificate when Key Vault was unreadable'
[[ "$(reloads)" == 2 ]] || fail 'reloaded with nothing changed'

print 'ok - apache-tls-sync'
