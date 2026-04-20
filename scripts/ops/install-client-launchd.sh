#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-client-launchd.sh [REPO_ROOT]

Installs a launchd user agent that runs the Dory client shipper.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

repo_root="${1:-${DORY_REPO_ROOT:-$(git rev-parse --show-toplevel)}}"
config_dir="${DORY_CONFIG_DIR:-$HOME/.config/dory}"
env_file="${DORY_CLIENT_ENV_FILE:-$config_dir/client.env}"
agents_dir="${HOME}/Library/LaunchAgents"
plist_path="${agents_dir}/ai.dory.client-shipper.plist"
log_dir="${DORY_CLIENT_LOG_DIR:-$HOME/Library/Logs/dory}"

if [[ ! -f "$env_file" ]]; then
  echo "Missing client env file: $env_file" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

mkdir -p "$agents_dir" "$log_dir"
cat > "$plist_path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.dory.client-shipper</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>exec ${DORY_CLIENT_SHIPPER_COMMAND}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DORY_REPO_ROOT</key><string>${repo_root}</string>
    <key>DORY_HTTP_URL</key><string>${DORY_HTTP_URL:-}</string>
    <key>DORY_CLIENT_DEVICE</key><string>${DORY_CLIENT_DEVICE:-}</string>
    <key>DORY_CLIENT_SPOOL_ROOT</key><string>${DORY_CLIENT_SPOOL_ROOT:-}</string>
    <key>DORY_SESSION_SPOOL_ROOT</key><string>${DORY_SESSION_SPOOL_ROOT:-${DORY_CLIENT_SPOOL_ROOT:-}}</string>
    <key>DORY_CLIENT_CHECKPOINTS_PATH</key><string>${DORY_CLIENT_CHECKPOINTS_PATH:-}</string>
    <key>DORY_CLIENT_POLL_SECONDS</key><string>${DORY_CLIENT_POLL_SECONDS:-}</string>
    <key>DORY_PYTHON_BIN</key><string>${DORY_PYTHON_BIN:-}</string>
    <key>DORY_CLIENT_AUTH_TOKEN</key><string>${DORY_CLIENT_AUTH_TOKEN:-}</string>
    <key>DORY_CLIENT_HARNESSES</key><string>${DORY_CLIENT_HARNESSES:-}</string>
    <key>DORY_CLAUDE_PROJECTS_ROOT</key><string>${DORY_CLAUDE_PROJECTS_ROOT:-}</string>
    <key>DORY_CODEX_SESSIONS_ROOT</key><string>${DORY_CODEX_SESSIONS_ROOT:-}</string>
    <key>DORY_OPENCLAW_AGENTS_ROOT</key><string>${DORY_OPENCLAW_AGENTS_ROOT:-}</string>
    <key>DORY_HERMES_SESSIONS_ROOT</key><string>${DORY_HERMES_SESSIONS_ROOT:-}</string>
    <key>DORY_HERMES_STATE_DB_PATH</key><string>${DORY_HERMES_STATE_DB_PATH:-}</string>
    <key>DORY_OPENCODE_DB_PATH</key><string>${DORY_OPENCODE_DB_PATH:-}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${log_dir}/client-shipper.out.log</string>
  <key>StandardErrorPath</key>
  <string>${log_dir}/client-shipper.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$plist_path" >/dev/null 2>&1 || true
launchctl load "$plist_path"

echo "Installed launchd client shipper:"
echo "  - $plist_path"
