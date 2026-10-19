# apache-tls

HTTPS for a local caching server, with a self-signed certificate of its own
and the same Basic Auth credential as the cloud repo. A Mac whose profile
trusts that certificate reaches the mirror over https with the credential
Munki already sends; a Mac without the profile yet keeps using port 80.

| Path | What it is |
|---|---|
| `terraform/caching-tls.tf` | One self-signed certificate per server, its Key Vault secrets, and read access that lets each server see only its own key. |
| `payload/Management/ApacheConfig/repo-tls.conf` | A 443 vhost that serves only `/deployment`, behind Basic Auth. Inert until its files exist. |
| `payload/Management/ApacheTLS/apache-tls-sync.sh` | Pulls this server's certificate and key, and the credential, from Key Vault daily, and reloads Apache only when one changed. |
| `payload/LaunchDaemons/com.domain.ApacheTLSSync.plist` | Runs the sync at load and at 03:17 daily. |
| `scripts/postinstall` | Renders the vhost for this server and starts the job. |
| `tests/test-apache-tls-sync.zsh` | Offline test of the sync, with fake `az`, `htpasswd` and `httpd`. |

## Why no private root

Only your own managed Macs need to trust these servers, so no public CA is
involved: no DNS validation, no renewal client on the servers, no host names
in Certificate Transparency logs.

The obvious alternative, a private root CA in a profile, is the wrong shape.
A root every Mac trusts can sign a certificate for any host name, including
your cloud repo, your identity provider and any bank, and its key would sit
in Terraform state. hashicorp/tls cannot add name constraints to limit it.
So each server gets its own self-signed certificate instead: its name only,
not a CA, valid for 397 days. A Mac that trusts it trusts that one host.

## The keys are in Terraform state

Terraform generates each server's private key, so the keys are in its state.
Keep the state in a backend with encryption at rest, limit read access to the
identity that applies this configuration, and treat read access to the state
as the ability to impersonate a caching server.

## Rolling it out

1. Apply the Terraform. Put each server's certificate from the
   `caching_server_cert_pem` output in a certificate profile, and scope it to
   the Macs that use that server.
2. Give each server its own service principal that signs in with a
   certificate, and put its PEM at `/Library/Management/ApacheTLS/sp.pem`,
   readable by root only. Set the ids, vault and domain at the top of
   `apache-tls-sync.sh`.
3. Set `DOC_ROOT` and `DOMAIN` in `scripts/postinstall`, build the package
   with munkipkg and install it on each mirror. Port 80 is untouched.
4. In the preflight, set `MIRROR_SCHEMES=(https http)`. Macs that have the
   profile move to https on their next run; the rest stay on http.
5. When every Mac has the profile, set `MIRROR_SCHEMES=(https)` and close
   port 80.

## Rotation

A certificate re-issues on the first `terraform apply` inside its last 30
days. A re-issued self-signed certificate is a new trust anchor, so a Mac
must have it before the server presents it. Apply, add the new certificate
to the profile beside the old one, wait for the profile to reach the fleet,
and only then let the sync run on the server. Macs that miss the profile fall
back to http in the meantime when `http` is still in `MIRROR_SCHEMES`, and
to the cloud repo when it is not.

## The credential on port 80

Munki sends `AdditionalHttpHeaders` with every request, plain http included.
The preflight turns on `AdditionalHttpHeadersHttpsOnly`, which keeps them off
http, but upstream Munki does not have that preference yet and ignores it.
On upstream Munki, an http mirror receives the credential in cleartext during
step 4, so keep that window short, or serve port 80 from a network where that
is acceptable.
