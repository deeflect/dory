#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-dory.sh [host|client|solo] [--repo-root PATH]

Roles:
  host    Configure the Dory server host and launchd/ops jobs
  client  Configure a Dory client machine for Claude, Codex, OpenClaw, Hermes, or Opencode
  solo    Configure one machine as both local host and local client

Client harnesses:
  - Claude Code
  - Codex
  - OpenClaw
  - Hermes
  - Opencode

Environment overrides:
  DORY_REPO_ROOT            Repo root used for generated paths
  DORY_CORPUS_ROOT          Corpus root for the host
  DORY_INDEX_ROOT           Index root for the host
  DORY_HTTP_URL             Remote Dory HTTP URL for client tooling
  DORY_CLIENT_DEVICE        Client device name
  DORY_CLIENT_SPOOL_ROOT    Local spool root for client session capture
  DORY_CLIENT_CHECKPOINTS_PATH
                            Local collector checkpoint state file
  DORY_CLIENT_POLL_SECONDS  Poll interval for auto-discovery shipping
  DORY_PYTHON_BIN           Python executable used by client services
  DORY_CLAUDE_PROJECTS_ROOT Override Claude Code source root
  DORY_CODEX_SESSIONS_ROOT  Override Codex sessions source root
  DORY_OPENCLAW_AGENTS_ROOT Override OpenClaw agents source root
  DORY_HERMES_SESSIONS_ROOT Override Hermes sessions source root
  DORY_HERMES_STATE_DB_PATH Override Hermes SQLite metadata path
  DORY_OPENCODE_DB_PATH     Override OpenCode database path
  DORY_CLIENT_SHIPPER_COMMAND
                            Shell command used by the client shipper service
EOF
}

write_env_line() {
  local key="$1"
  local value="$2"
  printf '%s=%q\n' "$key" "$value"
}

role=""
repo_root="${DORY_REPO_ROOT:-}"

account_home_for_current_user() {
  local user

  user="$(id -un 2>/dev/null || true)"
  if [[ -z "$user" ]]; then
    return 0
  fi

  eval "printf '%s\n' ~$user" 2>/dev/null || true
}

should_skip_service_for_mismatched_home() {
  local mode="$1"
  local helper="$2"
  local account_home

  if [[ "$mode" != "auto" ]]; then
    return 1
  fi

  account_home="$(account_home_for_current_user)"
  if [[ -n "$account_home" && "$HOME" != "$account_home" ]]; then
    printf 'Service install skipped: HOME does not match the account home for this user.\n'
    printf 'Run %s manually from the target user account session.\n' "$helper"
    return 0
  fi

  return 1
}

validate_service_mode() {
  local mode="$1"

  case "$mode" in
    false|False|0|no|No|true|True|1|yes|Yes|auto)
      ;;
    *)
      echo "Unsupported DORY_INSTALL_SERVICE value: $mode" >&2
      return 1
      ;;
  esac
}

service_install_disabled() {
  local mode="$1"

  case "$mode" in
    false|False|0|no|No)
      printf 'Service install skipped because DORY_INSTALL_SERVICE=%s\n' "$mode"
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

install_client_service_if_available() {
  local repo_root="$1"
  local mode="${DORY_INSTALL_SERVICE:-auto}"

  validate_service_mode "$mode"
  service_install_disabled "$mode" && return 0

  if command -v launchctl >/dev/null 2>&1; then
    should_skip_service_for_mismatched_home "$mode" \
      "$repo_root/scripts/ops/install-client-launchd.sh" && return 0
    bash "$repo_root/scripts/ops/install-client-launchd.sh" "$repo_root"
    return 0
  fi

  if command -v systemctl >/dev/null 2>&1; then
    should_skip_service_for_mismatched_home "$mode" \
      "$repo_root/scripts/ops/install-client-systemd.sh" && return 0

    if [[ -n "${XDG_RUNTIME_DIR:-}" ]] && systemctl --user show-environment >/dev/null 2>&1; then
      bash "$repo_root/scripts/ops/install-client-systemd.sh" "$repo_root"
      return 0
    fi

    if [[ "$mode" != "auto" ]]; then
      echo "systemctl exists, but the systemd user manager is not available for this shell." >&2
      return 1
    fi

    printf 'Service install skipped: systemd user manager is not available in this shell.\n'
    printf 'Run %s manually after logging into a systemd user session.\n' \
      "$repo_root/scripts/ops/install-client-systemd.sh"
  fi
}

install_ops_service_if_available() {
  local repo_root="$1"
  local mode="${DORY_INSTALL_SERVICE:-auto}"

  validate_service_mode "$mode"
  service_install_disabled "$mode" && return 0

  if command -v launchctl >/dev/null 2>&1; then
    should_skip_service_for_mismatched_home "$mode" \
      "$repo_root/scripts/ops/install-ops-launchd.sh" && return 0
    bash "$repo_root/scripts/ops/install-ops-launchd.sh" "$repo_root"
  fi
}

install_host() {
  local repo_root="$1"
  local config_dir="${DORY_CONFIG_DIR:-$HOME/.config/dory}"
  local corpus_root="${DORY_CORPUS_ROOT:-$repo_root/data/corpus}"
  local index_root="${DORY_INDEX_ROOT:-$repo_root/.dory/index}"
  local http_host="${DORY_HTTP_HOST:-127.0.0.1}"
  local http_port="${DORY_HTTP_PORT:-8766}"
  local auth_tokens_path="${DORY_AUTH_TOKENS_PATH:-$repo_root/.dory/auth-tokens.json}"
  local env_file="${config_dir}/host.env"

  mkdir -p "$config_dir"
  {
    write_env_line DORY_REPO_ROOT "$repo_root"
    write_env_line DORY_CORPUS_ROOT "$corpus_root"
    write_env_line DORY_INDEX_ROOT "$index_root"
    write_env_line DORY_HTTP_HOST "$http_host"
    write_env_line DORY_HTTP_PORT "$http_port"
    write_env_line DORY_AUTH_TOKENS_PATH "$auth_tokens_path"
  } > "$env_file"

  install_ops_service_if_available "$repo_root"

  printf 'Host configuration written to %s\n' "$env_file"
  printf 'Host server command:\n'
  printf '  DORY_CORPUS_ROOT=%s DORY_INDEX_ROOT=%s uv run dory-http --corpus-root %s --index-root %s --host %s --port %s\n' \
    "$corpus_root" "$index_root" "$corpus_root" "$index_root" "$http_host" "$http_port"
}

install_client() {
  local repo_root="$1"
  local config_dir="${DORY_CONFIG_DIR:-$HOME/.config/dory}"
  local env_file="${config_dir}/client.env"
  local http_url="${DORY_HTTP_URL:-http://127.0.0.1:8766}"
  local device="${DORY_CLIENT_DEVICE:-$(hostname -s 2>/dev/null || hostname)}"
  local spool_root="${DORY_CLIENT_SPOOL_ROOT:-$HOME/.local/share/dory/spool}"
  local checkpoints_path="${DORY_CLIENT_CHECKPOINTS_PATH:-$spool_root/checkpoints.json}"
  local poll_seconds="${DORY_CLIENT_POLL_SECONDS:-15}"
  local token="${DORY_CLIENT_AUTH_TOKEN:-}"
  local harnesses="${DORY_CLIENT_HARNESSES:-claude codex opencode openclaw hermes}"
  local python_bin="${DORY_PYTHON_BIN:-$(command -v python3)}"
  local shipper_command="${DORY_CLIENT_SHIPPER_COMMAND:-$python_bin $repo_root/scripts/ops/client-session-shipper.py --watch --harnesses \"$harnesses\" --poll-seconds \"$poll_seconds\" --spool-root \"$spool_root\" --checkpoints-path \"$checkpoints_path\"}"

  mkdir -p "$config_dir"
  {
    write_env_line DORY_REPO_ROOT "$repo_root"
    write_env_line DORY_HTTP_URL "$http_url"
    write_env_line DORY_CLIENT_DEVICE "$device"
    write_env_line DORY_CLIENT_SPOOL_ROOT "$spool_root"
    write_env_line DORY_SESSION_SPOOL_ROOT "$spool_root"
    write_env_line DORY_CLIENT_CHECKPOINTS_PATH "$checkpoints_path"
    write_env_line DORY_CLIENT_POLL_SECONDS "$poll_seconds"
    write_env_line DORY_PYTHON_BIN "$python_bin"
    write_env_line DORY_CLAUDE_PROJECTS_ROOT "${DORY_CLAUDE_PROJECTS_ROOT:-}"
    write_env_line DORY_CODEX_SESSIONS_ROOT "${DORY_CODEX_SESSIONS_ROOT:-}"
    write_env_line DORY_OPENCLAW_AGENTS_ROOT "${DORY_OPENCLAW_AGENTS_ROOT:-}"
    write_env_line DORY_HERMES_SESSIONS_ROOT "${DORY_HERMES_SESSIONS_ROOT:-}"
    write_env_line DORY_HERMES_STATE_DB_PATH "${DORY_HERMES_STATE_DB_PATH:-}"
    write_env_line DORY_OPENCODE_DB_PATH "${DORY_OPENCODE_DB_PATH:-}"
    write_env_line DORY_CLIENT_SHIPPER_COMMAND "$shipper_command"
    write_env_line DORY_CLIENT_AUTH_TOKEN "$token"
    write_env_line DORY_CLIENT_HARNESSES "$harnesses"
  } > "$env_file"

  install_client_service_if_available "$repo_root"

  if [[ " $harnesses " == *" claude "* ]] && command -v claude >/dev/null 2>&1; then
    claude mcp add-json --scope user dory "$(cat <<EOF
{"command":"python3","args":["$repo_root/scripts/claude-code/dory-mcp-http-bridge.py"],"env":{"DORY_HTTP_URL":"$http_url","DORY_CLIENT_AUTH_TOKEN":"$token"}}
EOF
)"
  fi

  printf 'Client configuration written to %s\n' "$env_file"
  printf 'Selected harnesses: %s\n' "$harnesses"
  printf 'Session shipper command:\n'
  printf '  %s\n' "$shipper_command"
  printf 'Use %s for Codex/OpenCode policy and the Claude MCP bridge already in the repo.\n' "$repo_root/AGENTS.md"
}

install_solo() {
  local repo_root="$1"
  local config_dir="${DORY_CONFIG_DIR:-$HOME/.config/dory}"
  local corpus_root="${DORY_CORPUS_ROOT:-$repo_root/data/corpus}"
  local index_root="${DORY_INDEX_ROOT:-$repo_root/.dory/index}"
  local http_host="${DORY_HTTP_HOST:-127.0.0.1}"
  local http_port="${DORY_HTTP_PORT:-8766}"
  local http_url="${DORY_HTTP_URL:-http://127.0.0.1:${http_port}}"
  local auth_tokens_path="${DORY_AUTH_TOKENS_PATH:-$repo_root/.dory/auth-tokens.json}"
  local device="${DORY_CLIENT_DEVICE:-$(hostname -s 2>/dev/null || hostname)}"
  local spool_root="${DORY_CLIENT_SPOOL_ROOT:-$HOME/.local/share/dory/spool}"
  local checkpoints_path="${DORY_CLIENT_CHECKPOINTS_PATH:-$spool_root/checkpoints.json}"
  local poll_seconds="${DORY_CLIENT_POLL_SECONDS:-15}"
  local token="${DORY_CLIENT_AUTH_TOKEN:-}"
  local harnesses="${DORY_CLIENT_HARNESSES:-claude codex opencode openclaw hermes}"
  local python_bin="${DORY_PYTHON_BIN:-$(command -v python3)}"
  local shipper_command="${DORY_CLIENT_SHIPPER_COMMAND:-$python_bin $repo_root/scripts/ops/client-session-shipper.py --watch --harnesses \"$harnesses\" --poll-seconds \"$poll_seconds\" --spool-root \"$spool_root\" --checkpoints-path \"$checkpoints_path\"}"
  local host_env="${config_dir}/host.env"
  local client_env="${config_dir}/client.env"

  mkdir -p "$config_dir"
  {
    write_env_line DORY_REPO_ROOT "$repo_root"
    write_env_line DORY_CORPUS_ROOT "$corpus_root"
    write_env_line DORY_INDEX_ROOT "$index_root"
    write_env_line DORY_HTTP_HOST "$http_host"
    write_env_line DORY_HTTP_PORT "$http_port"
    write_env_line DORY_AUTH_TOKENS_PATH "$auth_tokens_path"
  } > "$host_env"

  {
    write_env_line DORY_REPO_ROOT "$repo_root"
    write_env_line DORY_HTTP_URL "$http_url"
    write_env_line DORY_CLIENT_DEVICE "$device"
    write_env_line DORY_CLIENT_SPOOL_ROOT "$spool_root"
    write_env_line DORY_SESSION_SPOOL_ROOT "$spool_root"
    write_env_line DORY_CLIENT_CHECKPOINTS_PATH "$checkpoints_path"
    write_env_line DORY_CLIENT_POLL_SECONDS "$poll_seconds"
    write_env_line DORY_PYTHON_BIN "$python_bin"
    write_env_line DORY_CLAUDE_PROJECTS_ROOT "${DORY_CLAUDE_PROJECTS_ROOT:-}"
    write_env_line DORY_CODEX_SESSIONS_ROOT "${DORY_CODEX_SESSIONS_ROOT:-}"
    write_env_line DORY_OPENCLAW_AGENTS_ROOT "${DORY_OPENCLAW_AGENTS_ROOT:-}"
    write_env_line DORY_HERMES_SESSIONS_ROOT "${DORY_HERMES_SESSIONS_ROOT:-}"
    write_env_line DORY_HERMES_STATE_DB_PATH "${DORY_HERMES_STATE_DB_PATH:-}"
    write_env_line DORY_OPENCODE_DB_PATH "${DORY_OPENCODE_DB_PATH:-}"
    write_env_line DORY_CLIENT_SHIPPER_COMMAND "$shipper_command"
    write_env_line DORY_CLIENT_AUTH_TOKEN "$token"
    write_env_line DORY_CLIENT_HARNESSES "$harnesses"
  } > "$client_env"

  install_ops_service_if_available "$repo_root"
  install_client_service_if_available "$repo_root"

  if [[ " $harnesses " == *" claude "* ]] && command -v claude >/dev/null 2>&1; then
    claude mcp add-json --scope user dory "$(cat <<EOF
{"command":"python3","args":["$repo_root/scripts/claude-code/dory-mcp-http-bridge.py"],"env":{"DORY_HTTP_URL":"$http_url","DORY_CLIENT_AUTH_TOKEN":"$token"}}
EOF
)"
  fi

  printf 'Solo configuration written to %s and %s\n' "$host_env" "$client_env"
  printf 'Local host command:\n'
  printf '  DORY_CORPUS_ROOT=%s DORY_INDEX_ROOT=%s uv run dory-http --corpus-root %s --index-root %s --host %s --port %s\n' \
    "$corpus_root" "$index_root" "$corpus_root" "$index_root" "$http_host" "$http_port"
  printf 'Local session shipper command:\n'
  printf '  %s\n' "$shipper_command"
  printf 'Selected harnesses: %s\n' "$harnesses"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    host|client|solo)
      role="$1"
      shift
      ;;
    --repo-root)
      repo_root="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$repo_root" ]]; then
  repo_root="$(git rev-parse --show-toplevel)"
fi

if [[ -z "$role" ]]; then
  printf 'Select role [host/client/solo]: '
  read -r role
fi

case "$role" in
  host)
    install_host "$repo_root"
    ;;
  client)
    install_client "$repo_root"
    ;;
  solo)
    install_solo "$repo_root"
    ;;
  *)
    echo "Unknown role: $role" >&2
    usage >&2
    exit 1
    ;;
esac
