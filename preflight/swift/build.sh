#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
PAYLOAD="$ROOT/payload/"

TEAM_ID="7THDHBV74"
CERT_CN_APP="Developer ID Application: ACME (${TEAM_ID})"
CERT_CN_PKG="Developer ID Installer: ACME (${TEAM_ID})"
PRIMARY_KC="${HOME}/Library/Keychains/signing.keychain"
PROFILE="notarization_credentials"

swift build --package-path "$ROOT/src" -c release --arch arm64 --arch x86_64
BIN="$ROOT/src/.build/apple/Products/Release/preflight"

IDENTITY_SHA=$(security find-certificate -c "${CERT_CN_APP}" -a -Z "${PRIMARY_KC}" | awk '/SHA-1/{print $3;exit}')

/usr/bin/codesign --force --timestamp --options runtime --deep \
                  --keychain "${PRIMARY_KC}" --sign "${IDENTITY_SHA}" "${BIN}"

ZIP="${BIN}.zip"
/usr/bin/ditto -c -k --keepParent "${BIN}" "${ZIP}"
/usr/bin/xcrun notarytool submit "${ZIP}" --keychain-profile "${PROFILE}" --wait
rm -f "${ZIP}"

mkdir -p "${PAYLOAD}"
install -m 755 "${BIN}" "${PAYLOAD}/preflight"

munkipkg "${ROOT}"