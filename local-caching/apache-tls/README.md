# apache-tls

HTTPS for a local caching server, with a certificate from a private root and
the same Basic Auth credential as the cloud repo. A Mac that trusts the root
reaches the mirror over https with the credential Munki already sends; a Mac
that does not yet trust it keeps using port 80 until the root reaches it.

| Path | What it is |
|---|---|
| `terraform/caching-tls.tf` | The private root, one server certificate for every mirror name, the Key Vault secrets and read access scoped to them. |
| `payload/Management/ApacheConfig/repo-tls.conf` | A 443 vhost that serves only `/deployment`, behind Basic Auth. Inert until its files exist. |
| `payload/Management/ApacheTLS/apache-tls-sync.sh` | Pulls the certificate, key and credential from Key Vault daily, and reloads Apache only when one changed. |
| `payload/LaunchDaemons/com.domain.ApacheTLSSync.plist` | Runs the sync at load and at 03:17 daily. |
| `scripts/postinstall` | Renders the vhost for this server and starts the job. |
| `tests/test-apache-tls-sync.zsh` | Offline test of the sync, with fake `az`, `htpasswd` and `httpd`. |

## Why a private root

Only your own managed Macs need to trust the certificate. A public CA would
need DNS validation and a renewal client on every server, and would put the
mirror names in Certificate Transparency logs. A private root needs neither:
Terraform issues the leaf, Key Vault holds it, and the servers copy it.

The root's private key is in Terraform state, so read access to that state is
the ability to issue a certificate your Macs trust. Keep the state encrypted,
access-controlled and out of any pipeline that does not need it.

## Rolling it out

1. Apply the Terraform. Put the `caching_root_cert_pem` output in a
   certificate profile and scope it to every Mac that uses the mirrors.
2. Give the servers a service principal that signs in with a certificate, and
   put its PEM at `/Library/Management/ApacheTLS/sp.pem`, readable by root
   only. Set the ids and vault name at the top of `apache-tls-sync.sh`.
3. Set `DOC_ROOT` and `DOMAIN` in `scripts/postinstall`, build the package with
   munkipkg and install it on each mirror. Port 80 is untouched.
4. In the preflight, set `MIRROR_SCHEMES=(https http)`. Macs that have the root
   move to https on their next run; the rest stay on http.
5. When every Mac has the profile, set `MIRROR_SCHEMES=(https)` and close
   port 80.

## The credential on port 80

Munki sends `AdditionalHttpHeaders` with every request, plain http included.
The preflight turns on `AdditionalHttpHeadersHttpsOnly`, which keeps them off
http, but upstream Munki does not have that preference yet and ignores it.
On upstream Munki, an http mirror receives the credential in cleartext during
step 4, so keep that window short, or serve port 80 from a network where that
is acceptable.
