# Surfaces and integrations

Current external surface map.

## CLI

Main file: `src/dory_cli/main.py`

Packaged commands:

- `dory`
- `dory-http`
- `dory-mcp`

Global flags on `dory`:

- `--corpus-root` (Path, optional)
- `--index-root` (Path, optional)
- `--auth-tokens-path` (Path, optional)

Top-level `dory` commands:

- `init`
- `wake` — `--budget` (default 600), `--agent` (default "codex"), `--profile`
- `active-memory` — `prompt` (required), `--agent`, `--cwd`, `--include-wake/--no-include-wake`
- `memory-write` — `content` (required), `--subject` (required), `--action` (default "write"), `--kind` (default "fact"), `--scope`, `--confidence`, `--reason`, `--source`, `--soft/--no-soft`, `--dry-run/--no-dry-run`, `--force-inbox/--no-force-inbox`, `--allow-canonical/--no-allow-canonical`
- `purge` — `target` (required), `--expected-hash`, `--reason`, `--dry-run/--no-dry-run`, `--allow-canonical/--no-allow-canonical`, `--include-related-tombstone/--no-include-related-tombstone`
- `research` — `question` (required), `--kind` (default "report"), `--corpus` (default "all"), `--limit` (default 8), `--save/--no-save`
- `migrate` — `legacy_root` (required), `--llm/--no-llm`, `--jobs`, `--estimate`, `--interactive`, `--folder` (repeatable), `--sample`, `--pricing-file`
- `search` — `query` (required), `-n/--limit` (default 10), `--corpus` (default "durable"), `--mode` (default "hybrid"), `--type` (repeatable), `--status` (repeatable), `--tag` (repeatable)
- `get` — `path` (required), `--from` (default 1), `--lines/-n`
- `status`
- `reindex` — `--force`
- `neighbors` — `path` (required), `--direction` (default "out"), `--depth` (default 1)
- `backlinks` — `path` (required)
- `lint`

Nested command groups:

- `auth`
  - `new` — `name` (required)
- `dream`
  - `list`
  - `apply` — `proposal_id` (required)
  - `distill` — `session_path` (required), `--agent`
  - `propose` — `distilled_id` (required)
  - `reject` — `proposal_id` (required)
- `maintain`
  - `inspect` — `path` (required), `--write-report`
  - `wiki-health` — `--write-report`
- `ops`
  - `dream-once` — `--session` (repeatable)
  - `maintain-once` — `--path` (repeatable)
  - `wiki-health` — `--write-report`
  - `wiki-refresh-once`
  - `wiki-refresh-indexes`
  - `eval-once` — `--reindex/--no-reindex` (default True), `--questions-root` (default `eval/public/questions`), `--runs-root`, `--top-k`
  - `watch` — `--debounce-seconds` (default 1.0), `--dream/--no-dream` (default True), `--poll-interval` (default 0.25)
- `eval`
  - `run` — `question_id` (optional), `--questions-root`, `--runs-root`, `--top-k`, `--list-only`

Note: `neighbors`, `backlinks`, `lint` are top-level commands, not nested under `link`. Treat CLI help and tests as authoritative.

Useful wrapper:

- `scripts/codex/dory` — runs `python -m dory_cli.main`, defaults corpus root to `data/corpus` and index root to `.dory/index`

## HTTP API

Main file: `src/dory_http/app.py`

| Method | Path | Request Model | Notes |
|--------|------|--------------|-------|
| POST | `/v1/wake` | `WakeReq` | |
| POST | `/v1/search` | `SearchReq` | |
| POST | `/v1/active-memory` | `ActiveMemoryReq` | |
| POST | `/v1/research` | `ResearchReq` | returns composite `{artifact, research}` |
| POST | `/v1/migrate` | `MigrateReq` | response via `asdict()` on dataclass |
| GET | `/v1/get` | query params: `path`, `from`, `lines` | no Pydantic body |
| POST | `/v1/write` | `WriteReq` | |
| POST | `/v1/purge` | `PurgeReq` | hard-delete exact scratch/generated artifacts with hash guard |
| POST | `/v1/memory-write` | `MemoryWriteReq` | |
| POST | `/v1/recall-event` | `RecallEventReq` | |
| GET | `/v1/public-artifacts` | none | |
| POST | `/v1/session-ingest` | `SessionIngestReq` | |
| POST | `/v1/link` | `LinkReq` | |
| GET | `/v1/status` | none | |
| GET | `/v1/tools` | none | live MCP tool schema for HTTP bridges |
| GET | `/healthz` | none | unauthenticated container healthcheck |
| GET | `/metrics` | none | plain text, Prometheus format |
| GET | `/v1/stream` | query params: `reindex`, `force` | SSE with `status`, `reindex`, `error`, `done` events |
| GET | `/wiki` | none | browser wiki index |
| GET/POST | `/wiki/login` | form fields | cookie-backed wiki login |
| GET | `/wiki/search` | query params: `q`, `limit` | browser wiki search |
| GET | `/wiki/{page}` | path param | browser wiki page renderer |

Notes:

- HTTP auth is fail-closed by default: bearer tokens are enforced unless `DORY_ALLOW_NO_AUTH=true`. `/healthz` stays unauthenticated for container healthchecks. Wiki routes use their own web session login. Browser wiki login requires `DORY_WEB_PASSWORD`; if unset, `/wiki/login` form submission returns 503.
- `/v1/search` can use `src/dory_core/retrieval_planner.py` when an OpenRouter client is configured and the relevant `DORY_QUERY_*` feature flags are enabled, including strict-schema result selection over the final candidate set. `/v1/active-memory` uses the same planner/composer, but picks its backend from `DORY_ACTIVE_MEMORY_LLM_PROVIDER` (`off` / `local` / `openrouter` / `auto`); the `local` path targets any OpenAI-compatible endpoint via `DORY_LOCAL_LLM_*`. Planner/composer/selection failures fall back to deterministic behavior instead of failing the request.
- Semantic `memory-write` responses imply a semantic evidence artifact write on successful resolved mutations. Parity coverage: `tests/integration/http/test_memory_write_http.py`.
- No route declares a `response_model`, so OpenAPI response schemas aren't auto-generated.
- Error responses use FastAPI's default `{"detail": "..."}` format, not the contract's `{"error": {"code": ..., "message": ...}}` (see known drift below).
- `/v1/stream` query params `reindex` and `force` trigger an optional reindex during the stream.

## Native MCP bridge

Main files:

- `src/dory_mcp/server.py`
- `src/dory_mcp/tools.py`

Transports:

- stdio
- TCP

Implemented MCP tools (10):

| Tool | Required | Optional |
|---|---|---|
| `dory_wake` | — | `budget_tokens`, `agent`, `profile`, `include_recent_sessions`, `include_pinned_decisions` |
| `dory_active_memory` | `prompt`, `agent` | `cwd`, `timeout_ms`, `budget_tokens`, `include_wake` |
| `dory_research` | `question` | `kind`, `corpus`, `limit`, `save` |
| `dory_search` | `query` | `k`, `mode`, `corpus`, `scope`, `include_content`, `min_score` |
| `dory_get` | `path` | `from`, `lines` |
| `dory_memory_write` | `action`, `kind`, `subject`, `content` | `scope`, `confidence`, `source`, `soft`, `dry_run`, `force_inbox`, `allow_canonical`, `agent`, `session_id`, `reason` |
| `dory_write` | `kind`, `target` | `content`, `soft`, `dry_run`, `frontmatter`, `agent`, `session_id`, `expected_hash`, `reason` |
| `dory_purge` | `target` | `expected_hash`, `reason`, `dry_run`, `allow_canonical`, `include_related_tombstone` |
| `dory_link` | `op` | `path`, `direction`, `depth` |
| `dory_status` | — | — |

Notes:

- Native MCP schemas expose the finalized tool fields: search mode aliases, wake profiles, active-memory limits, dry-run write guards, purge guards.
- `dory_write` is the exact-path write surface; `dory_memory_write` is semantic.
- `dory_get` mirrors the HTTP metadata payload (`from`, `lines_returned`, `total_lines`, `frontmatter`, `hash`, `content`).
- Native `dory_search` and `dory_active_memory` share retrieval/runtime behavior with CLI and HTTP because they call the same `SearchEngine` and `ActiveMemoryEngine`. LLM-assisted query planning/reranking is opt-in.
- No authentication is enforced on MCP connections (HTTP has bearer auth; MCP doesn't).

## Claude Code bridge

Main file: `scripts/claude-code/dory-mcp-http-bridge.py`

Not the same implementation as the native MCP server. Separate bridge that forwards tool calls over HTTP.

Implemented bridge tools (10):

| Tool | Key differences from native |
|---|---|
| `dory_wake` | adds defaults: budget=1200, profile="coding", agent="claude-code", sessions=0, pinned=True |
| `dory_search` | adds `mode` enum (`bm25\|text\|keyword\|lexical\|vector\|semantic\|hybrid\|recall\|exact`), default k=5 |
| `dory_research` | HTTP-backed research call with bounded artifact options |
| `dory_active_memory` | HTTP-backed staged active-memory call with defaults and optional `include_wake` |
| `dory_get` | accepts native `from` and legacy `from_line`; adds defaults |
| `dory_link` | adds `op` enum (`neighbors\|backlinks\|lint`), `direction` enum (`out\|in`) |
| `dory_memory_write` | adds `kind` enum plus `dry_run`, `force_inbox`, `allow_canonical` |
| `dory_write` | exact-path write with `dry_run` support |
| `dory_purge` | hard-delete exact scratch/generated artifacts with dry-run/hash guards |
| `dory_status` | shorter description |

Known issues:

- Bridge fetches live tool schemas from `/v1/tools` and falls back to bundled schema if the server can't provide them.
- Already-open agent sessions may need restart after schema changes — MCP hosts can cache tool schemas for the running process.
- Bridge forwards `DORY_HTTP_TOKEN` / `DORY_CLIENT_AUTH_TOKEN` as `Authorization: Bearer ...`.
- Bridge returns structured HTTP/transport error envelopes from `_perform_request()` instead of flattening to naked strings.
- Bridge defaults to `http://127.0.0.1:8766`; installed agents should set `DORY_HTTP_URL` / `~/.config/dory/env` for remote or TLS deployments.
- Bridge has a 30-second HTTP timeout; native MCP has no timeout.
- Bridge inherits HTTP retrieval-planner behavior for `dory_search` and `dory_active_memory`; it doesn't implement its own planner.

## Hermes integration

Main files:

- `plugins/hermes-dory/provider.py`
- `plugins/hermes-dory/config.example.yaml`
- `plugins/hermes-dory/README.md`

Provider methods:

- `wake`
- `search`
- `get`
- `write`
- `memory_write`
- `link`
- `status`
- `prefetch`
- `build_memory_section`
- `store_memory`
- `sync_memories`

Search modes:

- Accepts `hybrid`, `recall`, `bm25`, `text`, `keyword`, `vector`, `exact`
- Accepts legacy compatibility names `lexical` and `semantic`
- Legacy names normalized before HTTP:
  - `text`, `keyword`, `lexical` → `bm25`
  - `semantic` → `vector`

Known issues:

- When no external client is provided, the provider keeps a reusable owned `httpx.Client`.
- `RuntimeError` is raised on HTTP errors instead of a domain-specific exception.
- Hermes parity tests now assert semantic artifact creation on `memory_write(write|forget)` in `tests/integration/http/test_hermes_shim_contract.py`.

## OpenClaw integration

Main files:

- `packages/openclaw-dory/src/index.ts`
- `packages/openclaw-dory/openclaw.plugin.json`
- `packages/openclaw-dory/package.json`

Registers:

- `memory_search`
- `memory_get`
- `memory_write`

Also implements:

- status probing
- recall-event submission
- public artifact listing
- Dory-backed flush planning

Search-mode normalization:

- OpenClaw-side modes like `query` and `vsearch` are mapped to API values `bm25` and `vector`.

Known issues:

- `sessionKey` is accepted by the interface but not forwarded to the Dory HTTP API (search is unscoped).
- `sessionKey` degradation is explicit in debug metadata rather than silently ignored.
- `probeEmbeddingAvailability()` and `probeVectorAvailability()` fail closed when Dory can't prove vectors are available.
- `search()` debug hooks surface backend `warnings` such as query-expansion fallback.
- Planner fallback warnings from Dory search surface through the same debug-warning channel.
- `status()` is still a cached snapshot, but `custom.statusSource`, `custom.statusAgeMs`, and `custom.statusStale` make freshness explicit. `sync()` refreshes status opportunistically.

## Common request models

Shared typed request/response models live in `src/dory_core/types.py`.

Key enums:

- Search mode input: `bm25 | text | keyword | lexical | vector | semantic | hybrid | recall | exact` — aliases normalize before execution
- Search corpus: `durable | sessions | all`
- Write kind: `append | create | replace | forget`
- Semantic write action: `write | replace | forget`
- Semantic write kind: `fact | preference | state | decision | note`
- Wake profile: `default | casual | coding | writing | privacy`

## Best surface validation tests

- `tests/integration/http/test_http_routes.py`
- `tests/integration/http/test_memory_write_http.py`
- `tests/integration/cli/test_semantic_write_commands.py`
- `tests/integration/cli/test_purge_command.py`
- `tests/integration/core/test_purge_flow.py`
- `tests/integration/http/test_session_ingest_http.py`
- `tests/integration/http/test_research_http.py`
- `tests/integration/mcp/test_stdio_server.py`
- `tests/integration/mcp/test_tcp_server.py`
- `tests/integration/mcp/test_http_bridge.py`
- `tests/integration/mcp/test_tool_schema.py`
- `tests/integration/acceptance/test_phase4_multiface.py`

## Surface drift to watch

- CLI `neighbors`/`backlinks`/`lint` are top-level, not `dory link` subcommands as the spec defines.
- MCP has no authentication; HTTP does.
- Claude Code bridge keeps `from_line` as a legacy alias but accepts native `from`.
- Native MCP and HTTP validate link paths against the corpus root before graph operations.
- HTTP error responses don't match the API contract's `{"error": {"code": "..."}}` format.
- No HTTP route declares a `response_model`, so OpenAPI docs are incomplete.
