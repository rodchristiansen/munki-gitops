#!/usr/bin/env zsh
# remote_branch_pkg_locations: every package location referenced on any
# remote branch, YAML and plist alike, so orphan cleanup on main keeps them.
set -euo pipefail

LIB="${0:A:h:h}/lib/common.sh"
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT
fail() { print -u2 "FAIL: $1"; exit 1; }

git -C "$FIXTURE" init -q -b main
git -C "$FIXTURE" config user.email hooks@example.invalid
git -C "$FIXTURE" config user.name 'Hook Test'
mkdir -p "$FIXTURE/deployment/pkgsinfo/apps"
printf 'name: A\ninstaller_item_location: "apps/A-1.pkg"\n' > "$FIXTURE/deployment/pkgsinfo/apps/A.yaml"
git -C "$FIXTURE" add deployment && git -C "$FIXTURE" commit -qm main
git -C "$FIXTURE" update-ref refs/remotes/origin/main HEAD

git -C "$FIXTURE" checkout -q -b feature
cat > "$FIXTURE/deployment/pkgsinfo/apps/B.plist" <<'PLIST'
<plist version="1.0"><dict>
<key>installer_item_location</key>
<string>/deployment/pkgs/apps/B-2.dmg</string>
</dict></plist>
PLIST
git -C "$FIXTURE" add deployment && git -C "$FIXTURE" commit -qm feature
git -C "$FIXTURE" update-ref refs/remotes/origin/feature HEAD
git -C "$FIXTURE" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git -C "$FIXTURE" checkout -q main

out=$(cd "$FIXTURE" && bash -c "source '$LIB'; remote_branch_pkg_locations")
[[ "$out" == $'apps/A-1.pkg\napps/B-2.dmg' ]] || fail "unexpected keep set: $out"
print 'ok - remote keep set'
