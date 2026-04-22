# Getting started

Install Dory, run it, and point your agents at it. For implementation details, see [current-state](current-state/README.md).

Dory is one shared memory service, not a per-agent library. Run one corpus, one index. Point Claude Code, Codex, opencode, OpenClaw, Hermes, or any MCP/HTTP client at the same surface.

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
- Embedding provider - Gemini key for the default provider, or an OpenAI-compatible local/LAN embedding endpoint
- OpenRouter API key - optional, for dreaming, maintenance, and LLM-assisted retrieval

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

- corpus -> `data/corpus`
- index -> `.dory/index`
- auth tokens -> `.dory/auth-tokens.json`

Write and read a test memory:

```bash
uv run dory memory-write "Atlas is the active focus this week." \
  --subject atlas --kind decision --force-inbox

uv run dory search "active focus"
uv run dory wake --profile coding --budget 1200
```

`--force-inbox` is there on purpose. A fresh corpus doesn't know what `atlas` is, so Dory parks the note under `inbox/semantic/` instead of guessing a canonical target. Once canonical project or person pages exist, run with `--dry-run`, check the route, then add `--allow-canonical` to commit.

Before search, semantic writes, reindex, HTTP, or MCP with the default Gemini embedding provider, set one of:

```bash
export DORY_GEMINI_API_KEY=...
export GOOGLE_API_KEY=...
```

For a local/LAN OpenAI-compatible embedding endpoint instead:

```bash
export DORY_EMBEDDING_PROVIDER=local
export DORY_LOCAL_EMBEDDING_BASE_URL=http://127.0.0.1:8000/v1
export DORY_LOCAL_EMBEDDING_MODEL=qwen3-embed
export DORY_LOCAL_EMBEDDING_API_KEY=...
export DORY_EMBEDDING_DIMENSIONS=1024
export DORY_EMBEDDING_BATCH_SIZE=16
```

For Docker against a local/LAN OpenAI-compatible server on the host, use the local override:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

The override points containers at `http://host.docker.internal:8000/v1`, enables local embeddings and local reranking, and defaults embedding batches to 16.

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

Set `DORY_GEMINI_API_KEY` or `GOOGLE_API_KEY` in `.env` before starting the container when using the default Gemini embedding provider. For local/LAN embeddings, set `DORY_EMBEDDING_PROVIDER=local` and the `DORY_LOCAL_EMBEDDING_*` values instead. On Linux hosts, the container runs as UID `10000`; if Docker cannot write the bind mount, run:

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
DORY_EMBEDDING_PROVIDER=gemini
DORY_EMBEDDING_MODEL=gemini-embedding-001
DORY_EMBEDDING_DIMENSIONS=768
DORY_EMBEDDING_BATCH_SIZE=100
DORY_LOCAL_EMBEDDING_BASE_URL=http://127.0.0.1:8000/v1
DORY_LOCAL_EMBEDDING_MODEL=qwen3-embed
DORY_LOCAL_EMBEDDING_API_KEY=
DORY_LOCAL_EMBEDDING_QUERY_INSTRUCTION=Given a web search query, retrieve relevant passages that answer the query
DORY_QUERY_RERANKER_ENABLED=false
DORY_QUERY_RERANKER_PROVIDER=openrouter
DORY_LOCAL_RERANKER_BASE_URL=http://127.0.0.1:8000/v1
DORY_LOCAL_RERANKER_MODEL=qwen3-rerank
DORY_LOCAL_RERANKER_API_KEY=
DORY_OPENROUTER_API_KEY=
DORY_ACTIVE_MEMORY_LLM_PROVIDER=off
DORY_ACTIVE_MEMORY_LLM_STAGES=compose
DORY_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
DORY_LOCAL_LLM_MODEL=qwen3.5:4b
DORY_LOCAL_LLM_TIMEOUT_SECONDS=5
DORY_LOCAL_LLM_MAX_TOKENS=512
DORY_LOCAL_LLM_API_KEY=
```

`DORY_DATA_ROOT` is the host directory mounted into the container at `/var/lib/dory`. Any host path works as long as Docker can write to it.

Notes:

- Image build uses host networking so the resolver works in locked-down networks. Leave `DORY_DOCKER_DNS_SERVERS` blank at runtime unless bridge DNS can't resolve your providers.
- `GEMINI_API_KEY` and `OPENROUTER_API_KEY` are accepted as compatibility aliases.

Optional active-memory LLM. `DORY_ACTIVE_MEMORY_LLM_PROVIDER`:

- `off` - deterministic retrieval only (default)
- `local` - OpenAI-compatible local/LAN endpoint (set `DORY_LOCAL_LLM_*`)
- `openrouter` - hosted
- `auto` - local first, OpenRouter fallback

`DORY_ACTIVE_MEMORY_LLM_STAGES` picks which stages the LLM touches: `plan` (query expansion), `compose` (evidence compression), or `both`. `compose` is the safest default for small local models. Dory skips LLM stages if the request deadline is tight. `DORY_LOCAL_LLM_BASE_URL` accepts either the service root or its `/v1` path.

Dreaming and daily digest generation use `DORY_DREAM_LLM_PROVIDER`. Set it to `local` for the same OpenAI-compatible LAN endpoint, `openrouter` for hosted generation, or `auto` to try local first.

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

The installer drops Dory rules into supported agent config files, wires the HTTP-backed MCP bridge, validates the bundled skills, and symlinks `skills/dory-*` into common global skill folders. It is idempotent and safe to run again:

```bash
./scripts/agent-policy/install.sh --dry-run
./scripts/agent-policy/install.sh --skip-claude
./scripts/agent-policy/install.sh --skip-codex
./scripts/agent-policy/install.sh --skip-opencode
./scripts/agent-policy/install.sh --skip-skills
```

If the host runs in Docker, use the `docker compose exec` token command from the previous section instead of `uv run dory auth new`.

Agents should follow this read loop:

1. `wake` at session start or task switch
2. `search` before claims about projects, people, decisions, or current environment
3. `get` for exact files before quoting or editing
4. `memory-write` for durable facts, preferences, decisions, or project state
5. `link` when backlinks or graph relationships matter

Exact tool names and parameters → [agent-integration.md](agent-integration.md).

OpenClaw plugin source lives under `packages/openclaw-dory/`; external OpenClaw installs should load the parent `packages` directory so OpenClaw can discover `package.json` and `openclaw.plugin.json`.

Hermes provider source lives under `plugins/hermes-dory/`. Use `memory_mode: hybrid` when you want automatic prefetched context plus tools, `context` for context only, or `tools` when you want manual tool calls with no automatic context injection.

## Session shipping

If you want Dory to ingest local agent sessions:

```bash
bash scripts/ops/install-dory.sh client
```

Or, if the same machine is both host and client:

```bash
bash scripts/ops/install-dory.sh solo
```

The shipper scans known harness stores, scrubs obvious noise and secrets, spools locally when offline, and ships session evidence to the host. It flushes a bounded number of queued jobs per pass, records retry metadata for transient failures, and moves malformed or validation-rejected jobs to `dead-letter/` under the spool root. Promotion into durable memory happens later through dream and maintenance runs.

Daily digest of shipped session evidence:

```bash
uv run dory --corpus-root data/corpus --index-root .dory/index ops daily-digest-once
```

Defaults: writes yesterday's `digests/daily/YYYY-MM-DD.md`, includes every matching session, skips sessions touched in the last 30 minutes, won't overwrite an existing digest, and reindexes only the written path. Multi-session days are processed as packed digest batches: small sessions are combined, oversized sessions are sent alone, and the batch digests are merged into the daily digest. Use `--today`, `--date YYYY-MM-DD`, `--dry-run`, `--limit`, or `--overwrite` for manual runs.

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
uv run dory ops daily-digest-once
uv run dory ops maintain-once
uv run dory ops eval-once
```

`dory status` includes index health fields such as `index_present`, `index_stale`, `index_missing_files`, `vector_drift`, and `last_reindex_at`. `dory reindex` prints progress to stderr by default; pass `--no-progress` for quiet JSON-only output.

## Where next

- [Agent integration](agent-integration.md) - MCP/HTTP/CLI wiring per client
- [Deployment runbook](../references/runbook.md) - ops, backup, recovery, validation
- [Client runbook](../references/client-runbook.md) - session shipping
- [Current-state docs](current-state/README.md) - implementation details and known drift
