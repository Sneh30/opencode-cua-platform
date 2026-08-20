#!/bin/bash
# Build the CUA Control macOS app bundle.
#
#   ./build.sh            -> native/build/CUA.app
#
# Double-click CUA.app to launch: it boots the control server, opencode
# serve, the CUA Gateway and Browser Adapter, and opens the control UI in
# its own window. STOP in the toolbar halts everything instantly.
#
# The app must be able to find the platform repo: it walks up from the
# bundle location, or honors CUA_PLATFORM_ROOT.

set -euo pipefail
cd "$(dirname "$0")"

APP="build/CUA.app"
rm -rf build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp Info.plist "$APP/Contents/Info.plist"

echo "compiling main.swift…"
swiftc -O -o "$APP/Contents/MacOS/CUA" main.swift

# Ad-hoc signature so the bundle runs on Apple Silicon.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

echo
echo "Built $APP"
echo "Double-click CUA.app to launch the platform."