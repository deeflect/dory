# Getting started

Install Dory, run it, and point your agents at it. This guide is for people who want to use Dory — not study the internals.

Dory is a shared memory service, not a per-agent library. You run one corpus and one index, then point Claude Code, Codex, opencode, OpenClaw, Hermes, or any MCP/HTTP client at the same surface.

## Pick a setup

| Setup | When to use | Command |
|---|---|---|
| Repo-local CLI | Testing or hacking on the repo | `uv run dory ...` |
| Same-host daemon | Agents live on the same machine | `uv run dory-http ...` |
| Docker service | Always-on daemon with a bind-mounted corpus | `docker compose up -d --build` |
| Private remote host | Several machines share one host | Docker or `dory-http` behind LAN/VPN/proxy |
| Client-only machine | Ships sessions to an existing host | `bash scripts/ops/install-dory.sh client` |
| Solo machine | One machine is both host and client | `bash scripts/ops/install-dory.sh solo` |

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Docker (only for the Docker path)
- Gemini API key — required for HTTP/MCP/search/semantic writes/reindex/evals today
- OpenRouter API key — optional, for dreaming, maintenance, and LLM-assisted retrieval

## Local CLI install

```bash
git clone <dory-repo-url>
cd dory
uv sync --frozen
mkdir -p data/corpus
export DORY_CORPUS_ROOT="$PWD/data/corpus"
export DORY_INDEX_ROOT="$PWD/.dory/index"
export DORY_AUTH_TOKENS_PATH="$PWD/.dory/auth-tokens.json"
uv run dory init
```

This guide uses these local paths:

- corpus → `data/corpus`
- index → `.dory/index`
- auth tokens → `.dory/auth-tokens.json`

Write and read a test memory:

```bash
uv run dory memory-write "Atlas is the active focus this week." \
  --subject atlas --kind decision --force-inbox

uv run dory search "active focus"
uv run dory wake --profile coding --budget 1200
```

`--force-inbox` on a fresh corpus is deliberate: Dory doesn't know what `atlas` is yet, so it captures the note under `inbox/semantic/` instead of guessing a canonical target. Once you have canonical project or person pages, run with `--dry-run` first, check the route, then add `--allow-canonical` to update the canonical page.

Before search, semantic writes, reindex, HTTP, or MCP, set one of:

```bash
export DORY_GEMINI_API_KEY=...
export GOOGLE_API_KEY=...
```

## HTTP daemon

```bash
uv run dory-http \
  --corpus-root data/corpus \
  --index-root .dory/index \
  --host 127.0.0.1 \
  --port 8766
```

Point clients at it:

```bash
export DORY_HTTP_URL=http://127.0.0.1:8766
```

Issue a bearer token:

```bash
export DORY_CLIENT_AUTH_TOKEN="$(uv run dory auth new codex)"
```

Health check:

```bash
curl http://127.0.0.1:8766/healthz
```

Authenticated status:

```bash
curl "$DORY_HTTP_URL/v1/status" \
  -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN"
```

Local throwaway dev only:

```bash
export DORY_ALLOW_NO_AUTH=true
```

Never run no-auth mode on a shared host.

## Docker

```bash
cp .env.example .env
mkdir -p data/corpus
docker compose up -d --build
```

Set `DORY_GEMINI_API_KEY` or `GOOGLE_API_KEY` in `.env` before starting the container. On Linux hosts, the container runs as UID `10000`; if Docker cannot write the bind mount, run:

```bash
sudo chown -R 10000:10000 data/corpus
```

Key `.env` values:

```bash
DORY_DATA_ROOT=./data/corpus
DORY_HTTP_BIND=127.0.0.1
DORY_HTTP_PORT=8766
DORY_ALLOW_NO_AUTH=false
DORY_WEB_PASSWORD=
DORY_GEMINI_API_KEY=...
DORY_OPENROUTER_API_KEY=
```

`DORY_DATA_ROOT` is the host directory mounted into the container at `/var/lib/dory`. Any host path works as long as Docker can write to it.

Create a bearer token inside the container so it lands in the mounted token store:

```bash
export DORY_CLIENT_AUTH_TOKEN="$(docker compose exec -T doryd dory auth new codex)"
```

Compose binds HTTP to `127.0.0.1:8766` by default. For LAN, VPN, or reverse-proxy setups:

```bash
DORY_HTTP_BIND=0.0.0.0
```

Only do that behind a trusted network boundary.

## Wire up agents

From the repo root:

```bash
export DORY_HTTP_URL=http://127.0.0.1:8766
export DORY_CLIENT_AUTH_TOKEN="$(uv run dory auth new codex)"
./scripts/agent-policy/install.sh
```

The installer drops Dory rules into supported agent config files and wires the HTTP-backed MCP bridge. It's idempotent:

```bash
./scripts/agent-policy/install.sh --dry-run
./scripts/agent-policy/install.sh --skip-claude
./scripts/agent-policy/install.sh --skip-codex
./scripts/agent-policy/install.sh --skip-opencode
```

If the host runs in Docker, use the `docker compose exec` token command from the previous section instead of `uv run dory auth new`.

Agents should follow this read loop:

1. `wake` at session start or task switch
2. `search` before claims about projects, people, decisions, or current environment
3. `get` for exact files before quoting or editing
4. `memory-write` for durable facts, preferences, decisions, or project state
5. `link` when backlinks or graph relationships matter

Exact tool names and parameters → [agent-integration.md](agent-integration.md).

## Session shipping

If you want Dory to ingest local agent sessions:

```bash
bash scripts/ops/install-dory.sh client
```

Or, if the same machine is both host and client:

```bash
bash scripts/ops/install-dory.sh solo
```

The shipper scans known harness stores, scrubs obvious noise and secrets, spools locally when offline, and sends session evidence to the host. Durable memory promotion happens later through dream/maintenance flows.

## Browser wiki and Obsidian

Generate the compiled wiki:

```bash
uv run dory --corpus-root data/corpus --index-root .dory/index ops wiki-refresh-once
```

The HTTP server serves it at:

```text
http://127.0.0.1:8766/wiki
```

Login needs `DORY_WEB_PASSWORD` or a valid token flow.

For Obsidian, open the corpus directory as a vault. With local defaults that's `data/corpus`.

## Common commands

```bash
uv run dory status
uv run dory reindex
uv run dory wake --profile coding --budget 1200
uv run dory search "what are we working on"
uv run dory get core/active.md
uv run dory memory-write "Ship the public eval suite." --subject dory --kind decision --dry-run --force-inbox
uv run dory ops dream-once
uv run dory ops maintain-once
uv run dory ops eval-once
```

## Where next

- [Agent integration](agent-integration.md) — MCP/HTTP/CLI wiring per client
- [Deployment runbook](../references/runbook.md) — ops, backup, recovery, validation
- [Client runbook](../references/client-runbook.md) — session shipping
- [Current-state docs](current-state/README.md) — implementation details and known drift
