#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-client-systemd.sh [REPO_ROOT]

Installs a systemd user unit that runs the Dory client shipper.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

repo_root="${1:-${DORY_REPO_ROOT:-$(git rev-parse --show-toplevel)}}"
config_dir="${DORY_CONFIG_DIR:-$HOME/.config/dory}"
env_file="${DORY_CLIENT_ENV_FILE:-$config_dir/client.env}"
units_dir="${HOME}/.config/systemd/user"
service_path="${units_dir}/ai.dory.client-shipper.service"

if [[ ! -f "$env_file" ]]; then
  echo "Missing client env file: $env_file" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

mkdir -p "$units_dir"
cat > "$service_path" <<EOF
[Unit]
Description=Dory client shipper
After=network-online.target

[Service]
Type=simple
Environment=DORY_REPO_ROOT=${repo_root}
Environment=DORY_HTTP_URL=${DORY_HTTP_URL:-}
Environment=DORY_CLIENT_DEVICE=${DORY_CLIENT_DEVICE:-}
Environment=DORY_CLIENT_SPOOL_ROOT=${DORY_CLIENT_SPOOL_ROOT:-}
Environment=DORY_SESSION_SPOOL_ROOT=${DORY_SESSION_SPOOL_ROOT:-${DORY_CLIENT_SPOOL_ROOT:-}}
Environment=DORY_CLIENT_CHECKPOINTS_PATH=${DORY_CLIENT_CHECKPOINTS_PATH:-}
Environment=DORY_CLIENT_POLL_SECONDS=${DORY_CLIENT_POLL_SECONDS:-}
Environment=DORY_PYTHON_BIN=${DORY_PYTHON_BIN:-}
Environment=DORY_CLIENT_AUTH_TOKEN=${DORY_CLIENT_AUTH_TOKEN:-}
Environment=DORY_CLIENT_HARNESSES=${DORY_CLIENT_HARNESSES:-}
Environment=DORY_CLAUDE_PROJECTS_ROOT=${DORY_CLAUDE_PROJECTS_ROOT:-}
Environment=DORY_CODEX_SESSIONS_ROOT=${DORY_CODEX_SESSIONS_ROOT:-}
Environment=DORY_OPENCLAW_AGENTS_ROOT=${DORY_OPENCLAW_AGENTS_ROOT:-}
Environment=DORY_HERMES_SESSIONS_ROOT=${DORY_HERMES_SESSIONS_ROOT:-}
Environment=DORY_HERMES_STATE_DB_PATH=${DORY_HERMES_STATE_DB_PATH:-}
Environment=DORY_OPENCODE_DB_PATH=${DORY_OPENCODE_DB_PATH:-}
ExecStart=/bin/bash -lc 'exec ${DORY_CLIENT_SHIPPER_COMMAND}'
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ai.dory.client-shipper.service

echo "Installed systemd client shipper:"
echo "  - $service_path"
