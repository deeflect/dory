# Deployment runbook

Use this after [docs/getting-started.md](../docs/getting-started.md). Getting-started covers first install and client wiring; this runbook covers operating an existing Dory host.

## Deployment paths

Foreground daemon:

```bash
uv run dory-http --corpus-root data/corpus --index-root .dory/index --host 127.0.0.1 --port 8766
```

Docker:

```bash
cp .env.example .env
mkdir -p data/corpus
# Edit .env and set either Gemini embedding auth or local embedding endpoint variables.
docker compose up -d --build
```

Client or solo bootstrap:

```bash
bash scripts/ops/install-dory.sh client
bash scripts/ops/install-dory.sh solo
```

Environment-specific paths:

- `DORY_DATA_ROOT` — Docker host storage
- `DORY_CORPUS_ROOT` — markdown source of truth
- `DORY_INDEX_ROOT` — disposable SQLite index state
- `DORY_AUTH_TOKENS_PATH` — bearer tokens
- `DORY_HTTP_URL` — client connection

## Restart

```bash
docker compose up -d --build
```

## Rollback

1. Check out the previous working git commit.
2. Run `docker compose up -d --build`.
3. If you publish your own image, pin that tag in your deployment override and restart from it.

## Reindex recovery

If the sidecar index is corrupted or stale, delete `~/dory/.index/` and rebuild:

```bash
export DORY_GEMINI_API_KEY=...
# or:
export DORY_EMBEDDING_PROVIDER=local
export DORY_LOCAL_EMBEDDING_BASE_URL=http://127.0.0.1:8000/v1
export DORY_LOCAL_EMBEDDING_MODEL=qwen3-embed
uv run dory --corpus-root ~/dory --index-root ~/dory/.index reindex
```

## Restore check

1. Confirm the git backup remote is reachable.
2. Restore the markdown tree into a temp directory.
3. Rebuild the index with the reindex command above.
4. `GET /v1/status` or `uv run dory --corpus-root ~/dory --index-root ~/dory/.index status`.

## Backup

Push the markdown repo to its backup remote:

```bash
DORY_CORPUS_ROOT=~/dory bash scripts/ops/backup.sh
```

Install a nightly cron entry:

```bash
bash scripts/ops/install-backup-cron.sh
```

Default schedule is `17 3 * * *`. Pass a custom cron expression as the first argument.

## Operator jobs

One-shot:

```bash
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops dream-once
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops maintain-once
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops wiki-refresh-once
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops wiki-refresh-indexes
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops eval-once
uv run dory --corpus-root ~/dory --index-root ~/dory/.index maintain wiki-health
uv run dory --corpus-root ~/dory migrate ~/legacy-brain
uv run dory --corpus-root ~/dory migrate --estimate --sample 25 ~/legacy-brain
uv run dory --corpus-root ~/dory migrate --interactive ~/legacy-brain
```

Foreground watcher:

```bash
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops watch --debounce-seconds 1.0
```

macOS launchd helpers:

```bash
bash scripts/ops/install-ops-launchd.sh
```

## Client session ingest

Interactive bootstrapper for host or client:

```bash
bash scripts/ops/install-dory.sh host
bash scripts/ops/install-dory.sh client
bash scripts/ops/install-dory.sh solo
```

The client flow writes a local Dory config, registers Claude Code MCP when available, and installs the local session shipper service for the selected OS.

Solo writes both host and client configs so one machine runs Dory locally and also auto-discovers local sessions from Claude, Codex, and opencode.

Full client setup: [client-runbook.md](client-runbook.md).

## Legacy corpus migration

Move a large markdown corpus into the new Dory schema without switching any harness memory backend:

```bash
uv run dory --corpus-root ~/dory migrate ~/legacy-brain
uv run dory --corpus-root ~/dory migrate --estimate --sample 25 ~/legacy-brain
uv run dory --corpus-root ~/dory migrate --interactive ~/legacy-brain
```

What it does:

- stages legacy files
- classifies them into the new schema
- extracts memory atoms
- bootstraps canonical pages
- preserves original evidence
- writes a report to `references/reports/migrations/`
- quarantines ambiguous cases into `inbox/quarantine/`

`--estimate` → non-interactive preflight with selected file counts, folder stats, token totals, and pricing when configured.

`migrate --interactive` → operator console with scope controls, live progress, and report pointers.

Expected layout after a successful run:

- `core/`
- `people/`
- `projects/`
- `concepts/`
- `decisions/`
- `logs/sessions/`
- `sources/*`
- `digests/daily/`
- `digests/weekly/`
- `references/reports/migrations/`

After migration, regenerate the compiled wiki:

```bash
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops wiki-refresh-once
```

## Research and artifacts

Save a markdown artifact instead of a transient answer:

```bash
uv run dory research "What are we working on right now?" --kind report
uv run dory research "Summarize Rooster." --kind briefing
uv run dory research "What do we know about Crawstr?" --kind wiki-note
```

Destinations:

- `report` → `references/reports/YYYY-MM-DD-<slug>.md`
- `briefing` → `references/briefings/YYYY-MM-DD-<slug>.md`
- `wiki-note` → `wiki/concepts/<slug>.md`

## Semantic writes

Preferred memory writes are semantic and fuzzy-routed, not path-first:

```bash
uv run dory --corpus-root ~/dory --index-root ~/dory/.index memory-write \
  "Rooster is the active focus this week." \
  --subject rooster \
  --kind decision
```

Equivalent surfaces:

- HTTP: `POST /v1/memory-write`
- MCP: `dory_memory_write`
- Claude bridge: `dory_memory_write`
- OpenClaw plugin tool: `memory_write`
- Hermes provider: `memory_write(...)`

Use legacy `write` only for compatibility and debug flows where a path-first markdown mutation is intentional.

## OpenClaw parity checks

Parity surfaces:

- `POST /v1/recall-event`
- `GET /v1/public-artifacts`
- `/v1/status` → `openclaw` diagnostics block
- non-null OpenClaw plugin flush plan
- recall-promotion candidate tracking feeding `ops dream-once`

Not parity-complete yet:

- builtin/local fallback if Dory HTTP is unavailable

Timeline migration preview / write:

```bash
uv run python scripts/ops/migrate_timeline_v1.py --corpus-root ~/dory
uv run python scripts/ops/migrate_timeline_v1.py --corpus-root ~/dory --write
```

## Live validation

```bash
uv run dory --corpus-root ~/dory --index-root ~/dory/.index reindex
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops eval-once
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops dream-once
uv run dory --corpus-root ~/dory --index-root ~/dory/.index ops maintain-once
uv run dory-http --corpus-root ~/dory --index-root ~/dory/.index --host 127.0.0.1 --port 8766
export DORY_CLIENT_AUTH_TOKEN="$(uv run dory --corpus-root ~/dory --index-root ~/dory/.index auth new runbook)"
curl http://127.0.0.1:8766/v1/status -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN"
curl -X POST http://127.0.0.1:8766/v1/search \
  -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"who is Alex","k":3}'
```

`ops dream-once` does two things:

- distills new session logs into `inbox/distilled/*`
- materializes recall-promotion notes from repeated OpenClaw recall hits, then proposes durable writes

Dream proposal/apply is semantic-first:

- proposal JSON stores `action`, `kind`, fuzzy `subject`, `content`
- `dream apply` routes through Dory semantic memory writes instead of path-first markdown mutations

## Health

- HTTP status: `GET /v1/status`
- Metrics: `GET /metrics`
- Logs: `docker compose logs -f doryd`
- Embedding provider: default Gemini uses `DORY_GEMINI_API_KEY` or `GOOGLE_API_KEY`; local/LAN OpenAI-compatible embeddings use `DORY_EMBEDDING_PROVIDER=local` with `DORY_LOCAL_EMBEDDING_*`
- OpenRouter auth for dreaming / maintenance: `DORY_OPENROUTER_API_KEY` or `OPENROUTER_API_KEY`
- Optional active-memory LLM: `DORY_ACTIVE_MEMORY_LLM_PROVIDER` (`off` / `local` / `openrouter` / `auto`). For `local`, point `DORY_LOCAL_LLM_BASE_URL` at an OpenAI-compatible endpoint (e.g. `http://127.0.0.1:11434/v1`) and set `DORY_LOCAL_LLM_MODEL`. `DORY_ACTIVE_MEMORY_LLM_STAGES` picks `plan`, `compose`, or `both`; `compose` is the safest default for small local models. Dory skips the LLM path when the request deadline is too tight, and active-memory stays read-only with budget-clamped evidence either way.
