#!/bin/bash
# Assemble handTrack.app from the Swift Shell and the Python Engine.
#
# A .app is just a directory with a required layout — no Xcode involved. We build the
# binary with SwiftPM, lay out the bundle by hand, then ad-hoc sign it so macOS has a
# stable identity to attach Camera and Accessibility grants to.
#
# The bundle carries the Engine's source but NOT a Python runtime, so it currently
# depends on the interpreter at HANDTRACK_PYTHON (default: the repo's venv). Embedding
# Python is the deferred step that turns this into something shareable with people who
# have never installed it — see Q13.
#
# If Accessibility is switched on but the app still reports itself untrusted, the entry
# is stale. Clear it and relaunch so the app can re-add itself:
#     tccutil reset Accessibility com.brohsha.handTrack
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/build/handTrack.app"
CONFIG="${CONFIG:-debug}"
SIGN_IDENTITY="${SIGN_IDENTITY:-handTrack Local Dev}"

echo "==> Building Shell ($CONFIG)"
cd "$ROOT/shell"
swift build -c "$CONFIG"
BINARY="$(swift build -c "$CONFIG" --show-bin-path)/HandTrackShell"

echo "==> Assembling bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BINARY" "$APP/Contents/MacOS/HandTrackShell"

# Engine source, minus caches and the venv.
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests' \
    "$ROOT/engine/" "$APP/Contents/Resources/engine/"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>handTrack</string>
    <key>CFBundleDisplayName</key>
    <string>handTrack</string>
    <key>CFBundleIdentifier</key>
    <string>com.brohsha.handTrack</string>
    <key>CFBundleExecutable</key>
    <string>HandTrackShell</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <!-- Menu bar only: no Dock icon, no app switcher entry. -->
    <key>LSUIElement</key>
    <true/>
    <key>NSCameraUsageDescription</key>
    <string>handTrack uses the camera to see your hand and move the cursor.</string>
</dict>
</plist>
PLIST

# A stable identity keeps macOS seeing one app across rebuilds, so Camera and
# Accessibility grants survive. Falls back to ad-hoc, where every rebuild looks like a
# brand new app and both grants are silently dropped.
if security find-identity -v -p codesigning 2>/dev/null | grep -q "$SIGN_IDENTITY"; then
    echo "==> Signing as '$SIGN_IDENTITY' (permissions will survive rebuilds)"
    codesign --force --deep --sign "$SIGN_IDENTITY" "$APP"
else
    echo "==> Signing ad-hoc — '$SIGN_IDENTITY' is not a trusted code-signing identity."
    echo "    Permissions will be dropped on every rebuild. To fix that permanently:"
    echo "    sudo security add-trusted-cert -d -r trustRoot -p codeSign \\"
    echo "        -k /Library/Keychains/System.keychain ~/.handtrack-dev-cert.pem"
    codesign --force --deep --sign - "$APP"
fi

echo "==> Built $APP"

# The repo's build directory is an output, not a home. Installing puts the app where a
# Mac app belongs, and — since macOS ties permissions partly to the path — keeps the
# grants attached to the copy actually being used.
if [ "${INSTALL:-0}" = "1" ]; then
    echo "==> Installing to /Applications"
    rm -rf "/Applications/handTrack.app"
    cp -R "$APP" "/Applications/handTrack.app"
    echo "==> Installed /Applications/handTrack.app"
    echo "    Launch with: open -a handTrack"
else
    echo "    Launch with: open $APP"
    echo "    Install with: INSTALL=1 $0"
fi
echo "    (launching the inner binary directly tests the terminal's permissions, not the app's)"
