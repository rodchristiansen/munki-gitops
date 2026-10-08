# Munki preflight

A zsh preflight that Munki runs as root before every run. It replaces the
earlier Python and Swift samples: zsh ships with macOS, so there is no
interpreter to install and no binary to sign and notarise before a change can
ship.

Each run it:

1. **Picks the repository.** The first local mirror whose test catalog answers
   200 *and* was modified in the last 48 hours wins; otherwise the cloud repo.
   A mirror whose sync has died still answers 200 for old files, so
   reachability alone is not enough.
2. **Looks the Mac up in inventory.** Downloads `deployment/enroll/computers.csv`
   (published by `enrollment/consumers/munki.py`) from that same repository,
   finds the row for this serial and writes `/Library/Management/Inventory.yaml`.
   Columns are matched by header name, and a download only counts when curl
   exits 0 as well as returning 200. A transfer cut off by a timeout still
   reports 200.
3. **Sets the Mac's identity.** ComputerName, HostName and LocalHostName, plus
   the Remote Desktop info fields.
4. **Points the Mac at its manifest.** `ClientIdentifier` becomes
   `usage/catalog/area/location/hostname`, the per-device manifest under the
   same tree the Entra group ladder is built from. A device on a provisioning
   manifest is left alone until provisioning moves it. A device whose
   inventory status means it is leaving the fleet moves to the decommission
   manifest, and stays there while inventory is unreachable.

Every failure logs and exits 0. Munki aborts the run when a preflight fails,
and a broken inventory lookup must never stop software from installing.

## Configure

Edit the block at the top of `payload/preflight`: mirror hosts, the cloud repo
URL, the provisioning manifests, the retire statuses and the decommission
manifest. Keep the retire statuses in step with `RETIRE_STATUSES` in
`enrollment/shared/hierarchy.py`.

The script reuses Munki's own `AdditionalHttpHeaders`, so a repo behind Basic
or Bearer auth needs no second credential. The headers reach curl through a
0600 config file, never on its command line, where any local user could read
them from the process list. They are only sent over https: a mirror on plain
http is probed without them, and the inventory CSV, which decides this Mac's
names and manifest, is always read over https, from the cloud repo when the
selected mirror is http. Each inventory value that becomes part of
`ClientIdentifier` must be a plain path segment (letters, digits, `.`, `_`,
`-`), so a bad row cannot point the Mac at another tree's manifest.

## Override

A device that a provisioning re-run knocked back onto a provisioning manifest
can be moved back by hand:

```sh
sudo /usr/local/munki/preflight --force
```

Or remotely, through MDM or Remote Desktop. The flag file is removed after the
next successful enrolment:

```sh
sudo touch /Library/Management/.force_production_enrollment
```

## Build

[munkipkg](https://github.com/munki/munki-pkg) builds the signed package from
`build-info.yaml`. Set your Developer ID Installer identity and notarisation
keychain profile there first.

```sh
munkipkg preflight/munki
```

## Test

The tests fake curl, defaults and plutil, so they need no Munki and no network:

```sh
zsh preflight/munki/tests/test-preflight.zsh
```
