#!/usr/bin/env bash
# DEPRECATED — Mac .app/.dmg packaging is no longer the distribution path.
# Use ./install.sh + scripts/start_webui.sh (local WebUI) instead. See docs/PACKAGING.md.
#
# Phase 4 legacy — macOS .app + .dmg + optional notarization (Apple Silicon only).
#
# Prerequisites (Mac):
#   - Xcode CLT, Apple Developer ID Application certificate
#   - (optional) notarytool credentials: NOTARY_PROFILE or Apple ID keychain profile
#   - brew install create-dmg   # or use hdiutil fallback below
#
# Usage:
#   export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   export NOTARY_PROFILE="pre-peer-checker-notary"   # optional
#   ./scripts/build_macos.sh
set -euo pipefail

echo "WARNING: build_macos.sh is DEPRECATED. Prefer ./install.sh and WebUI launchers." >&2

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS (Apple Silicon). On DGX use scripts/build_linux_smoke.sh."
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true

pip install -q 'pyinstaller>=6.3'
pip install -q -e '.[gui]'

VERSION="${VERSION:-0.1.0}"
DIST_APP="dist/PrePeerChecker.app"
DMG_PATH="dist/PrePeerChecker-${VERSION}.dmg"
IDENTITY="${CODESIGN_IDENTITY:-}"

rm -rf build/pre-peer-checker-gui dist/PrePeerChecker dist/PrePeerChecker.app

pyinstaller packaging/pyinstaller/pre-peer-checker-gui.spec \
  --distpath dist \
  --workpath build/pre-peer-checker-gui \
  --noconfirm

# PyInstaller may emit either PrePeerChecker.app or onedir + BUNDLE
if [[ ! -d "${DIST_APP}" ]]; then
  if [[ -d dist/PrePeerChecker/PrePeerChecker.app ]]; then
    mv dist/PrePeerChecker/PrePeerChecker.app "${DIST_APP}"
  elif [[ -d dist/PrePeerChecker ]]; then
    echo "WARN: onedir built without .app — packaging as folder (dev only)"
    DIST_APP="dist/PrePeerChecker"
  else
    echo "Build output missing"; exit 1
  fi
fi

entitlements="${ROOT}/packaging/macos/entitlements.plist"
if [[ -n "${IDENTITY}" ]]; then
  echo "Signing with: ${IDENTITY}"
  codesign --force --deep --options runtime \
    --entitlements "${entitlements}" \
    --sign "${IDENTITY}" \
    "${DIST_APP}"
  codesign --verify --deep --strict --verbose=2 "${DIST_APP}"
else
  echo "CODESIGN_IDENTITY unset — unsigned build (Gatekeeper will block distribution)"
fi

rm -f "${DMG_PATH}"
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg \
    --volname "Pre-peer-checker" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --app-drop-link 400 200 \
    "${DMG_PATH}" \
    "${DIST_APP}"
else
  TMP_DMG_DIR="$(mktemp -d)"
  cp -R "${DIST_APP}" "${TMP_DMG_DIR}/"
  ln -s /Applications "${TMP_DMG_DIR}/Applications"
  hdiutil create -volname "Pre-peer-checker" -srcfolder "${TMP_DMG_DIR}" \
    -ov -format UDZO "${DMG_PATH}"
  rm -rf "${TMP_DMG_DIR}"
fi

echo "DMG: ${DMG_PATH}"

if [[ -n "${IDENTITY}" ]] && [[ -n "${NOTARY_PROFILE:-}" ]]; then
  echo "Submitting for notarization (profile=${NOTARY_PROFILE})..."
  xcrun notarytool submit "${DMG_PATH}" --keychain-profile "${NOTARY_PROFILE}" --wait
  xcrun stapler staple "${DMG_PATH}"
  echo "Notarized + stapled: ${DMG_PATH}"
else
  echo "Skip notarization (set NOTARY_PROFILE after configuring notarytool)."
fi

echo "Done. Ship ${DMG_PATH} — offline, no API keys required at runtime."
