#!/bin/bash
# Build the opencode-operator macOS app bundle (dev build).
#
#   ./build.sh            -> native/build/opencode-operator.app
#
# Double-click to launch: it boots the control server, opencode serve, the
# CUA Gateway and Browser Adapter, and opens the control UI in its own
# window. STOP in the toolbar halts everything instantly.
#
# Dev builds run against a platform repo found by walking up from the
# bundle location (or CUA_PLATFORM_ROOT). The full self-contained,
# double-clickable distribution — vendored runtimes + embedded platform +
# bundled GUI-agent model — is produced by ./bundle.sh instead.
#
# Signing: ad-hoc signature (no keychain identity, no signing prompts).
# The TCC Screen Recording / Accessibility grants for the app-launched
# context are stable per-built-bundle — a rebuild produces a new bundle
# identity, so after a rebuild re-enable the entries once in System
# Settings. The control server itself stops polling and shows a banner
# when the grant is missing, so there is no prompt storm.

set -euo pipefail
cd "$(dirname "$0")"

APP="build/opencode-operator.app"
rm -rf build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp Info.plist "$APP/Contents/Info.plist"
[ -f assets/AppIcon.icns ] && cp assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

echo "compiling main.swift…"
swiftc -O -o "$APP/Contents/MacOS/opencode-operator" main.swift

# Ad-hoc signature so the bundle runs on Apple Silicon.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

echo
echo "Built $APP"
echo "Double-click opencode-operator.app to launch the platform."
