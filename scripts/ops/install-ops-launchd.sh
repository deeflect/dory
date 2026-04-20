#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
CORPUS_ROOT="${DORY_CORPUS_ROOT:-$REPO_ROOT/data/corpus}"
INDEX_ROOT="${DORY_INDEX_ROOT:-$REPO_ROOT/.dory/index}"
AGENTS_DIR="${HOME}/Library/LaunchAgents"

mkdir -p "$AGENTS_DIR"

cat > "${AGENTS_DIR}/ai.dory.dream-once.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.dory.dream-once</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd "$REPO_ROOT" && uv run dory --corpus-root "$CORPUS_ROOT" --index-root "$INDEX_ROOT" ops dream-once</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>2</integer>
    <key>Minute</key><integer>17</integer>
  </dict>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
PLIST

cat > "${AGENTS_DIR}/ai.dory.maintain-once.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.dory.maintain-once</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd "$REPO_ROOT" && uv run dory --corpus-root "$CORPUS_ROOT" --index-root "$INDEX_ROOT" ops maintain-once</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>3</integer>
    <key>Minute</key><integer>17</integer>
  </dict>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
PLIST

launchctl unload "${AGENTS_DIR}/ai.dory.dream-once.plist" >/dev/null 2>&1 || true
launchctl unload "${AGENTS_DIR}/ai.dory.maintain-once.plist" >/dev/null 2>&1 || true
launchctl load "${AGENTS_DIR}/ai.dory.dream-once.plist"
launchctl load "${AGENTS_DIR}/ai.dory.maintain-once.plist"

echo "Installed launchd jobs:"
echo "  - ai.dory.dream-once"
echo "  - ai.dory.maintain-once"
