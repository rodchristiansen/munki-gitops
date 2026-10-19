#!/bin/zsh
# Pull this caching server's TLS certificate, key and the repo's Basic Auth
# credential from Key Vault, and reload Apache if any of them changed.
#
# The certificate is this server's own self-signed one (terraform/caching-tls.tf),
# and Macs trust that certificate alone through a certificate profile, so the
# key read here can impersonate this server and nothing else. az signs in as a service
# principal with a certificate kept on the server, in its own az profile so it
# never touches another user's session. Runs daily from
# com.domain.ApacheTLSSync, and at load.

set -u

# ── Configuration ────────────────────────────────────────────────────────────
# Everything an organisation changes lives here. Nothing here is a secret: the
# service principal authenticates with the certificate at SP_PEM, which only
# root can read.
AZ=${AZ:-/opt/homebrew/bin/az}
SP_PEM=${SP_PEM:-/Library/Management/ApacheTLS/sp.pem}
SP_CLIENT_ID=${SP_CLIENT_ID:-00000000-0000-0000-0000-000000000000}
TENANT_ID=${TENANT_ID:-00000000-0000-0000-0000-000000000000}
VAULT=${VAULT:-example-repo-secrets}
DOMAIN=${DOMAIN:-example.org}
USER_SECRET=MunkiUsername
PASS_SECRET=MunkiPassword

TLS_DIR=${TLS_DIR:-/private/etc/apache2/tls}
STATE_DIR=${STATE_DIR:-/Library/Management/ApacheTLS}
HTTPD=${HTTPD:-/usr/sbin/httpd}
HTPASSWD=${HTPASSWD:-/usr/sbin/htpasswd}
HTTPD_CONF=${HTTPD_CONF:-/private/etc/apache2/httpd.conf}

# Never trace: the secrets below pass through shell variables.
set +x
setopt NO_XTRACE

log() { print -r -- "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# This server's name as clients reach it, which names its secrets: dots become
# dashes, because Key Vault secret names allow letters, digits and dashes only.
if [[ -z ${SERVER_FQDN:-} ]]; then
  SERVER_FQDN=$(scutil --get ComputerName 2>/dev/null || hostname -s)
  SERVER_FQDN="${SERVER_FQDN:l}.$DOMAIN"
fi
CERT_SECRET="CachingServerCertificate-${SERVER_FQDN//./-}"
KEY_SECRET="CachingServerKey-${SERVER_FQDN//./-}"

# Apache's workers run as this user and read the htpasswd on every request.
APACHE_USER=$(awk '$1 == "User" {print $2; exit}' "$HTTPD_CONF" 2>/dev/null)
APACHE_USER=${APACHE_USER:-_www}
AS_ROOT=$(( EUID == 0 ))

own() { (( AS_ROOT )) && chown "$@"; return 0; }

for f in "$AZ" "$SP_PEM"; do
  [[ -e $f ]] || { log "ERROR: missing $f"; exit 1; }
done

mkdir -p "$TLS_DIR"
# The workers must traverse the folder to reach the htpasswd; they cannot list
# it, and server.key stays readable by root alone.
chmod 711 "$TLS_DIR"
changed=0

export AZURE_CONFIG_DIR="$STATE_DIR/azure"
if ! "$AZ" account show >/dev/null 2>&1; then
  "$AZ" login --service-principal -u "$SP_CLIENT_ID" --certificate "$SP_PEM" \
    -t "$TENANT_ID" --allow-no-subscriptions >/dev/null 2>&1 \
    || { log "ERROR: az login as the caching-server service principal failed"; exit 1; }
fi

secret() { "$AZ" keyvault secret show --vault-name "$VAULT" -n "$1" --query value -o tsv 2>/dev/null; }

# install_if_changed <dst> <content> <mode>: replace dst atomically, and only
# when the content differs, so an unchanged run never reloads Apache.
install_if_changed() {
  local dst=$1 content=$2 mode=$3 tmp
  [[ -f $dst && "$(<$dst)" == "$content" ]] && return 0
  tmp=$(mktemp "$TLS_DIR/.sync.XXXXXX") || return 1
  if print -r -- "$content" > "$tmp" && chmod "$mode" "$tmp" && own root:wheel "$tmp" && mv -f "$tmp" "$dst"; then
    log "updated $dst"
    changed=1
  else
    rm -f "$tmp"; return 1
  fi
}

# ── Certificate and key ──────────────────────────────────────────────────────
cert=$(secret "$CERT_SECRET")
key=$(secret "$KEY_SECRET")
if [[ $cert == *"BEGIN CERTIFICATE"* && $key == *"PRIVATE KEY"* ]]; then
  install_if_changed "$TLS_DIR/server.crt" "$cert" 644 || log "ERROR: writing server.crt failed"
  install_if_changed "$TLS_DIR/server.key" "$key" 600 || log "ERROR: writing server.key failed"
else
  log "WARNING: could not read the certificate from $VAULT; keeping the existing one"
fi
unset key

# ── htpasswd ─────────────────────────────────────────────────────────────────
user=$(secret "$USER_SECRET")
pass=$(secret "$PASS_SECRET")
if [[ -n $user && -n $pass ]]; then
  # Rewrite only when the credential no longer verifies. bcrypt output differs
  # on every run, so comparing content would reload Apache daily.
  if ! print -r -- "$pass" | "$HTPASSWD" -v -i "$TLS_DIR/htpasswd" "$user" >/dev/null 2>&1; then
    tmp=$(mktemp "$TLS_DIR/.sync.XXXXXX")
    if print -r -- "$pass" | "$HTPASSWD" -B -i -c "$tmp" "$user" >/dev/null 2>&1 \
        && chmod 400 "$tmp" && own "$APACHE_USER:wheel" "$tmp" && mv -f "$tmp" "$TLS_DIR/htpasswd"; then
      log "htpasswd refreshed from $VAULT"
      changed=1
    else
      rm -f "$tmp"
      log "ERROR: htpasswd write failed"
    fi
  fi
else
  log "WARNING: could not read $USER_SECRET/$PASS_SECRET from $VAULT; keeping the existing htpasswd"
fi
unset user pass

# ── Reload ───────────────────────────────────────────────────────────────────
if (( changed )); then
  if "$HTTPD" -t >/dev/null 2>&1; then
    # Signal this httpd directly. On macOS, apachectl graceful reloads Apple's
    # org.apache.httpd job instead, and re-enables it.
    "$HTTPD" -k graceful && log "Apache reloaded"
  else
    log "ERROR: httpd -t failed after the TLS update; not reloading"
    "$HTTPD" -t
    exit 1
  fi
fi
exit 0
