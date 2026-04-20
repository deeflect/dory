#!/usr/bin/env bash
# Install or update Dory policy + MCP server entries across every agent on this
# machine. Idempotent — safe to re-run after pulling updates. See
# docs/agent-integration.md for the full story.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
snippet_path="${script_dir}/dory-policy.md"
bridge_path="${repo_root}/scripts/claude-code/dory-mcp-http-bridge.py"

# Client config resolution (in order):
#   1. Environment variables
#   2. ~/.config/dory/env
#   3. Hard defaults (URL only)
dory_env_file="$HOME/.config/dory/env"
if [[ -f "$dory_env_file" && ( -z "${DORY_HTTP_URL:-}" || -z "${DORY_CLIENT_AUTH_TOKEN:-}" ) ]]; then
  # shellcheck disable=SC1090
  set -a; source "$dory_env_file"; set +a
fi
DORY_HTTP_URL="${DORY_HTTP_URL:-http://127.0.0.1:8766}"
DORY_CLIENT_AUTH_TOKEN="${DORY_CLIENT_AUTH_TOKEN:-}"
DORY_AGENT_NAME="${DORY_AGENT_NAME:-}"

dry_run=0
skip_claude=0
skip_codex=0
skip_opencode=0

usage() {
  cat <<EOF
Usage: install.sh [--dry-run] [--skip-claude] [--skip-codex] [--skip-opencode]

Registers the Dory MCP server and appends a policy snippet into each agent's
global rules file. Idempotent.

Env:
  DORY_HTTP_URL   Dory HTTP endpoint. Sourced in order: env → ~/.config/dory/env
                  → default http://127.0.0.1:8766
  DORY_CLIENT_AUTH_TOKEN
                  Optional bearer token. Sourced in order: env →
                  ~/.config/dory/env

Files touched:
  ~/.claude.json                      mcpServers.dory
  ~/.claude/CLAUDE.md                 appended snippet
  ~/.codex/config.toml                [mcp_servers.dory] block
  ~/.codex/AGENTS.md                  appended snippet
  ~/.config/opencode/opencode.json    mcp.dory entry
  ~/.config/opencode/AGENTS.md        appended snippet

Policy snippet source:
  ${snippet_path}
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --skip-claude) skip_claude=1 ;;
    --skip-codex) skip_codex=1 ;;
    --skip-opencode) skip_opencode=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
skip() { say "skip: $*"; }
do_or_dry() {
  if [[ $dry_run -eq 1 ]]; then
    say "dry-run: $*"
  else
    eval "$@"
  fi
}

require_file() {
  local path="$1" label="$2"
  if [[ ! -f "$path" ]]; then
    echo "missing $label: $path" >&2
    exit 1
  fi
}

require_file "$snippet_path" "policy snippet"
require_file "$bridge_path" "MCP bridge"

# ---------------------------------------------------------------------------
# Append policy snippet between <!-- dory-policy:START --> / END markers.
# ---------------------------------------------------------------------------
install_snippet() {
  local target="$1" label="$2"
  local snippet_body
  snippet_body="$(cat "$snippet_path")"

  mkdir -p "$(dirname "$target")"
  if [[ ! -f "$target" ]]; then
    if [[ $dry_run -eq 1 ]]; then
      say "dry-run: create $target with snippet ($label)"
      return
    fi
    {
      printf '# Global agent rules\n\n'
      printf '%s\n' "$snippet_body"
    } > "$target"
    say "created $label at $target"
    return
  fi

  if grep -q '<!-- dory-policy:START -->' "$target" && grep -q '<!-- dory-policy:END -->' "$target"; then
    if [[ $dry_run -eq 1 ]]; then
      say "dry-run: refresh snippet in $target ($label)"
      return
    fi
    python3 - "$target" "$snippet_path" <<'PY'
import sys, re
target, snippet_path = sys.argv[1], sys.argv[2]
with open(snippet_path) as f:
    snippet = f.read().rstrip() + "\n"
with open(target) as f:
    text = f.read()
pattern = re.compile(r"<!-- dory-policy:START -->.*?<!-- dory-policy:END -->\s*", re.DOTALL)
new_text = pattern.sub(snippet, text, count=1)
if not new_text.endswith("\n"):
    new_text += "\n"
with open(target, "w") as f:
    f.write(new_text)
PY
    say "refreshed $label snippet in $target"
    return
  fi

  if [[ $dry_run -eq 1 ]]; then
    say "dry-run: append snippet to $target ($label)"
    return
  fi
  {
    printf '\n'
    cat "$snippet_path"
  } >> "$target"
  say "appended $label snippet to $target"
}

# ---------------------------------------------------------------------------
# MCP: Claude Code — ~/.claude.json top-level mcpServers.dory
# ---------------------------------------------------------------------------
install_claude_mcp() {
  local config="$HOME/.claude.json"
  if [[ ! -f "$config" ]]; then
    skip "Claude config not found at $config"
    return
  fi
  if [[ $dry_run -eq 1 ]]; then
    say "dry-run: ensure mcpServers.dory in $config"
    return
  fi
  python3 - "$config" "$bridge_path" "$DORY_HTTP_URL" "$DORY_CLIENT_AUTH_TOKEN" <<'PY'
import json, sys, shutil
config_path, bridge, http_url, auth_token = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(config_path) as f:
    data = json.load(f)
servers = data.setdefault("mcpServers", {})
target = {
    "command": "python3",
    "args": [bridge],
    "env": {"DORY_HTTP_URL": http_url},
}
if auth_token:
    target["env"]["DORY_CLIENT_AUTH_TOKEN"] = auth_token
if servers.get("dory") == target:
    print("claude mcp: unchanged")
    sys.exit(0)
shutil.copy(config_path, config_path + ".dory-bak")
servers["dory"] = target
with open(config_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print("claude mcp: written (backup at .dory-bak)")
PY
}

# ---------------------------------------------------------------------------
# MCP: Codex CLI — ~/.codex/config.toml [mcp_servers.dory]
# ---------------------------------------------------------------------------
install_codex_mcp() {
  local config="$HOME/.codex/config.toml"
  if [[ ! -d "$HOME/.codex" ]]; then
    skip "Codex home not found at $HOME/.codex"
    return
  fi
  if [[ $dry_run -eq 1 ]]; then
    say "dry-run: ensure [mcp_servers.dory] in $config"
    return
  fi
  touch "$config"
  python3 - "$config" "$bridge_path" "$DORY_HTTP_URL" "$DORY_CLIENT_AUTH_TOKEN" <<'PY'
import sys, re, shutil
config_path, bridge, http_url, auth_token = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
env_block = f'DORY_HTTP_URL = "{http_url}"\n'
if auth_token:
    env_block += f'DORY_CLIENT_AUTH_TOKEN = "{auth_token}"\n'
block_body = (
    "# dory:START — managed by palace/scripts/agent-policy/install.sh\n"
    "[mcp_servers.dory]\n"
    "enabled = true\n"
    'command = "python3"\n'
    f'args = ["{bridge}"]\n'
    "\n"
    "[mcp_servers.dory.env]\n"
    f"{env_block}"
    "# dory:END\n"
)
with open(config_path) as f:
    text = f.read()
pattern = re.compile(r"# dory:START.*?# dory:END\n?", re.DOTALL)
if pattern.search(text):
    new_text = pattern.sub(block_body, text, count=1)
elif re.search(r"^\[mcp_servers\.dory\]", text, re.MULTILINE):
    print("codex mcp: existing unmanaged [mcp_servers.dory] present; skipping")
    sys.exit(0)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    new_text = text + block_body
if new_text == text:
    print("codex mcp: unchanged")
    sys.exit(0)
shutil.copy(config_path, config_path + ".dory-bak")
with open(config_path, "w") as f:
    f.write(new_text)
print("codex mcp: written (backup at .dory-bak)")
PY
}

# ---------------------------------------------------------------------------
# MCP: opencode — ~/.config/opencode/opencode.json mcp.dory
# ---------------------------------------------------------------------------
install_opencode_mcp() {
  local config="$HOME/.config/opencode/opencode.json"
  if [[ ! -f "$config" ]]; then
    skip "opencode config not found at $config"
    return
  fi
  if [[ $dry_run -eq 1 ]]; then
    say "dry-run: ensure mcp.dory in $config"
    return
  fi
  python3 - "$config" "$bridge_path" "$DORY_HTTP_URL" "$DORY_CLIENT_AUTH_TOKEN" <<'PY'
import json, sys, shutil
config_path, bridge, http_url, auth_token = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(config_path) as f:
    data = json.load(f)
mcp = data.setdefault("mcp", {})
target = {
    "type": "local",
    "command": ["python3", bridge],
    "environment": {"DORY_HTTP_URL": http_url},
    "enabled": True,
}
if auth_token:
    target["environment"]["DORY_CLIENT_AUTH_TOKEN"] = auth_token
if mcp.get("dory") == target:
    print("opencode mcp: unchanged")
    sys.exit(0)
shutil.copy(config_path, config_path + ".dory-bak")
mcp["dory"] = target
with open(config_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print("opencode mcp: written (backup at .dory-bak)")
PY
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
say "snippet source: $snippet_path"
say "bridge path:    $bridge_path"
say "Dory HTTP URL:  $DORY_HTTP_URL"
say "Auth token set: $( [[ -n "$DORY_CLIENT_AUTH_TOKEN" ]] && printf yes || printf no )"
[[ $dry_run -eq 1 ]] && say "--- DRY RUN — no changes will be written ---"

if [[ $skip_claude -eq 0 ]]; then
  install_snippet "$HOME/.claude/CLAUDE.md" "Claude Code rules"
  install_claude_mcp
else skip "Claude Code"; fi

if [[ $skip_codex -eq 0 ]]; then
  install_snippet "$HOME/.codex/AGENTS.md" "Codex rules"
  install_codex_mcp
else skip "Codex"; fi

if [[ $skip_opencode -eq 0 ]]; then
  install_snippet "$HOME/.config/opencode/AGENTS.md" "opencode rules"
  install_opencode_mcp
else skip "opencode"; fi

say "done. OpenClaw / Hermes are code-based clients — see docs/agent-integration.md for HTTP setup."
