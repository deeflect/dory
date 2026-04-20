# Client runbook

Set up a client machine to contribute cleaned session logs to a shared Dory host.

A client doesn't own canonical memory. It captures and cleans session evidence, ships it to the host, and lets the host handle digestion and durable memory promotion later.

If one machine should run both Dory and the local session collector, use `solo` instead of `client`.

## Install

From the Dory repo:

```bash
bash scripts/ops/install-dory.sh client
bash scripts/ops/install-dory.sh solo
```

The `client` flow will:

- write a local client config under `~/.config/dory/`
- register Claude Code MCP if `claude` is installed
- install the local session shipper service for the current OS
- configure harness auto-discovery for the selected local stores

`solo` additionally writes the local host config and points the client at the local Dory HTTP instance on loopback.

## Harness setup

| Harness | Wiring | Auto-discovery path |
|---|---|---|
| Claude Code | MCP bridge at `scripts/claude-code/dory-mcp-http-bridge.py` | `~/.claude/projects/**/*.jsonl` |
| Codex | Repo-level `AGENTS.md` + `scripts/codex/dory` wrapper | `~/.codex/sessions/**/*.jsonl` |
| OpenClaw | Native session collector + `packages/openclaw-dory/` plugin | `~/.openclaw/agents/*/sessions/*.jsonl` |
| Hermes | Native session collector + `plugins/hermes-dory/` provider | `~/.hermes/sessions/**/*.jsonl` |
| opencode | Repo-level policy file + client shipper settings | `~/.local/share/opencode/opencode.db` |

## Session flow

1. The local collector scans selected harness stores on a timer.
2. It strips obvious noise and secrets.
3. The cleaned session log ships to the Dory host.
4. The host stores it under `logs/sessions/...`.
5. Nightly digestion turns useful parts into distilled notes and proposed durable memory.

## Troubleshooting

- **Shipper can't reach the host** — it should keep retrying from the local spool.
- **Claude Code doesn't show Dory tools** — restart Claude Code after the MCP registration step.
- **Harness in a nonstandard path** — override the source root in the client env, then restart the shipper service.
- **Shipper service not running** — check the generated service file:
  - macOS: `~/Library/LaunchAgents/ai.dory.client-shipper.plist`
  - Linux: `~/.config/systemd/user/ai.dory.client-shipper.service`
