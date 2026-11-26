#!/bin/zsh
# Build Odjezdy (Release) with Xcode and install it to /Applications, then launch it.
# Optional: DEVELOPMENT_TEAM=<your team id> scripts/install.sh  (keeps permissions across rebuilds)
set -euo pipefail
cd "$(dirname "$0")/.."
xcodebuild -project Odjezdy.xcodeproj -scheme Odjezdy -configuration Release \
  -destination 'generic/platform=macOS' \
  -derivedDataPath build/xcode -allowProvisioningUpdates \
  ${DEVELOPMENT_TEAM:+DEVELOPMENT_TEAM=$DEVELOPMENT_TEAM} build | grep -E "error|BUILD" || true
APP=build/xcode/Build/Products/Release/Odjezdy.app
[[ -d "$APP" ]] || { echo "Build failed"; exit 1; }
pkill -x Odjezdy 2>/dev/null || true
rm -rf /Applications/Odjezdy.app
cp -R "$APP" /Applications/
open /Applications/Odjezdy.app
echo "Installed /Applications/Odjezdy.app"
