# Operations and validation

The operator path: migration, dreaming, maintenance, wiki, evals, deploy/install, and the tests that prove those paths exist.

## Migration

Main code:

- `src/dory_core/migration_plan.py`
- `src/dory_core/migration_engine.py`
- `src/dory_core/migration_llm.py`
- `src/dory_core/migration_prompts.py`
- `src/dory_core/migration_normalize.py`
- `src/dory_core/migration_resolve.py`
- `src/dory_core/timeline_migration.py`
- `src/dory_core/corpus_normalization.py`

CLI entrypoint: `dory migrate`

Behavior:

- Can estimate a run without writing.
- Can select folders or samples.
- Can run with concurrency.
- Can run deterministically or with LLM assistance.
- Stages supported legacy markdown plus structured/text inputs (`.json`, `.jsonl`, `.ndjson`, `.txt`, `.yaml`, `.yml`, `.toml`, `.csv`) after filtering junk roots like `.pytest_cache`, dependency caches, and build output.
- With `--llm`, each selected markdown file goes through one strict-schema extraction pass yielding classification, entity candidates, atoms, source quality, and `resolution_mode`.
- Writes per-document extraction artifacts under `inbox/migration-documents/<run_id>/`.
- Resolved documents go through a corpus-level entity clustering pass before claims and canonical pages are written.
- Deterministic fallback is evidence-first by default; unresolved docs no longer mint canonical pages from heuristic atom extraction.
- Bounded no-LLM promotion exists only for explicit contracts:
  - transcript-shaped session exports with clear assistant statements
  - typed JSON payloads with explicit supported families
  - known schema-tagged exports routed through explicit adapters
- Resolved docs route through entity resolution, then compile through the claim/canonical/wiki stack.
- When the LLM audit path succeeds, migration writes `inbox/migration-runs/<run_id>.audit.json`.
- When flagged pages can be safely tightened, migration also writes `inbox/migration-runs/<run_id>.repair.json`, applies one bounded grounded repair pass, and re-audits before writing the final markdown report and JSON run artifact.
- Quarantines low-confidence or explicitly quarantined material.
- Emits migration reports and run artifacts.

Validation:

- Acceptance coverage exercises the baseline `--no-llm` path plus a bounded fake-LLM `--llm` path that writes canonical output, per-document artifacts, and an audit artifact.
- Acceptance coverage also exercises the migration quarantine path, including `quarantined_count`, per-document artifact metadata, and the written quarantine markdown artifact.

CLI flags:

- `--llm/--no-llm` (default: llm enabled)
- `--jobs` (min 1)
- `--estimate`
- `--interactive`
- `--folder` (repeatable)
- `--sample` (min 1)
- `--pricing-file`

Timeline migration is a separate utility:

- `scripts/ops/migrate_timeline_v1.py --corpus-root <corpus>`
- `--write` flag for actual writes; without it, preview mode

Snapshot notes:

- CLI help doesn't expose `migrate-tui`.
- Older `migrate-tui` plan docs are historical/private material — not shipped publicly.
- Old TUI implementation files are deleted.

## Dreaming

Main code:

- `src/dory_core/dreaming/extract.py`
- `src/dory_core/dreaming/proposals.py`
- `src/dory_core/dreaming/recall.py`
- `src/dory_core/dreaming/events.py`
- `src/dory_core/ops.py`

CLI group: `dory dream`

Subcommands:

- `list`
- `apply` — `proposal_id` (required)
- `distill` — `session_path` (required), `--agent`
- `propose` — `distilled_id` (required)
- `reject` — `proposal_id` (required)

Batch surface:

- `dory ops dream-once` — `--session` (repeatable)

Runtime behavior:

- Distills session logs into `inbox/distilled/`.
- Generates semantic write proposals into `inbox/proposed/`.
- Promotes repeated recall hits into distilled notes.
- Proposal application routes through semantic writes, not raw markdown target selection.
- `dream-once` materializes recall-promotion distilled notes before proposal generation.

Backend selection:

- Dream distillation/proposal generation uses `DORY_DREAM_LLM_PROVIDER`.
- Supported values are `openrouter`, `local`, and `auto`; the local path uses the OpenAI-compatible `DORY_LOCAL_LLM_*` endpoint.

## Daily Digests

Main code:

- `src/dory_core/digest_writer.py`
- `src/dory_core/digest_mining.py`

Batch surface:

- `dory ops daily-digest-once` — `--date`, `--today`, `--overwrite`, `--dry-run`, `--min-age-minutes`, `--limit`, `--reindex/--no-reindex`
- `dory mine-digests` — extracts durable claims out of existing daily/weekly digest files into the claim store

Runtime behavior:

- `daily-digest-once` consumes shipped session Markdown under `logs/sessions/**`.
- The default scheduled behavior writes yesterday's `digests/daily/YYYY-MM-DD.md`.
- It skips sessions modified more recently than the configured age guard so active sessions are not summarized mid-write.
- It refuses to overwrite an existing digest unless `--overwrite` is passed.
- It uses the configured dream LLM provider for digest generation and reindexes the written digest path by default.
- `mine-digests` is the follow-up claim extraction pass; it does not create the digest file.

## Maintenance

Main code:

- `src/dory_core/maintenance.py`
- `src/dory_core/ops.py`

CLI commands:

- `dory maintain inspect` — `path` (required), `--write-report`
- `dory maintain wiki-health` — `--write-report`
- `dory maintain backfill-privacy-metadata` — `--path` (repeatable), `--refresh`, `--apply`
- `dory ops maintain-once` — `--path` (repeatable)

Behavior:

- `inspect` asks OpenRouter for cleanup suggestions on metadata and placement.
- Reports written under `inbox/maintenance/`.
- `wiki-health` scans generated wiki pages for stale pages, contradictions, low confidence, open questions, missing evidence, and missing timelines.
- Privacy metadata convention:
  - `visibility`: `private | internal | public`
  - `sensitivity`: `personal | financial | legal | contact | credentials | health | none`
  - personal/raw/imported docs should carry both fields so agents and maintenance reports can distinguish boundary rules from raw sensitive evidence.
- Stale-page detection covers both explicit `status: stale|superseded` markers and age-based expiry from frontmatter `updated`.
- `wiki-health` accepts both claim-style and canonical current-state sections when checking evidence coverage.
- Only concrete evidence refs count as coverage; placeholder labels like `Derived from claim store` don't count.
- Ignores placeholder contradiction/open-question lines like `No contradictions found.`.
- Flags event mismatches when `Timeline` and `Evidence` describe different event-type sets.
- Flags `state_conflict` when a page still presents live current-state claims but its event model only shows retirement/invalidation.
- Flags `claim_mismatch` when a compiled page's current-state section disagrees with active claims in `.dory/claim-store.db`.
- Flags `claim_event_mismatch` and `claim_evidence_mismatch` when the page's rendered event types or evidence paths drift from the claim-event ledger.
- Flags `missing_privacy_metadata` for personal/raw/imported docs without `visibility` and `sensitivity`.
- `backfill-privacy-metadata` uses the latest `inbox/maintenance/wiki-health.json` by default, dry-runs unless `--apply` is passed, and only inserts missing `visibility` / `sensitivity` frontmatter fields.
- `maintain-once` runs the inspector across canonical defaults.

## Compiled wiki

Main code:

- `src/dory_core/compiled_wiki.py`
- `src/dory_core/wiki_indexes.py`
- `src/dory_core/ops.py`

Behavior:

- `compiled_wiki.py` renders claim-backed wiki pages from active claims plus claim events.
- `dory ops wiki-refresh-once` prefers claim-store-backed page rendering when claim history exists:
  - the refresh job in `ops.py` maps canonical sources to entity IDs, loads claim history and claim events, and uses `render_compiled_page_from_claim_records()` when it has structured claim data
  - when no claim history exists, refresh falls back to a bounded summary-based page built from the canonical source body
- Refresh prunes orphaned generated wiki pages under managed families (`people`, `projects`, `concepts`, `decisions`) while leaving non-generated wiki files alone.
- The renderer groups `Evidence` by event type (`Added`, `Replaced`, `Retired`, `Invalidated`) when claim events are provided.
- `wiki/index.md`, `wiki/hot.md`, `wiki/log.md` are generated shell pages for navigation, recent context, and recent activity.
- `wiki/hot.md` includes `Last Updated`, `Current Focus`, `Key Recent Facts`, `Recent Changes`, `Recent Pages`, and `Active Threads` sections. When `.dory/claim-store.db` exists, facts and changes come from recent claim events instead of page summaries alone.
- Family indexes and recent-page summaries prefer claim-store statements and claim-event recency over page-body summary scraping when the claim store exists.
- `wiki/log.md` includes a `Recent Claim Changes` section derived from the claim-event ledger before wiki/session activity listings.

CLI commands:

- `dory ops wiki-refresh-once`
- `dory ops wiki-refresh-indexes`
- `dory maintain wiki-health`

Current source inputs for compiled pages:

- `core/active.md`
- `people/*.md`
- `projects/*/state.md`
- `concepts/*.md`
- `decisions/*.md`

Outputs live under `wiki/`.

Notes:

- Compiled wiki rendering consumes claim-event evidence directly via `compiled_wiki.py`.
- Pages render a simple event-driven `Timeline` section when claim events are present.
- Canonical tombstone pages for semantic `forget` are republished from claim history plus claim events before wiki refresh or maintenance scans.
- Family wiki indexes sort pages by claim-event-derived freshness when `.dory/claim-store.db` exists, otherwise by frontmatter `updated`, and display frontmatter titles instead of raw file stems.
- `wiki-health` compares rendered page text against claim-store state, but its mismatch checks are still largely text-heuristic set/subset comparisons, not a full structured round-trip.

## Watch and incremental ops

Main code:

- `src/dory_core/watch.py`
- `src/dory_core/ops.py`

CLI:

- `dory ops watch` — `--debounce-seconds` (default 1.0), `--dream/--no-dream` (default True), `--poll-interval` (default 0.25)

Behavior:

- Buffers markdown filesystem events.
- Reindexes non-session markdown changes.
- Syncs changed `logs/sessions/**` files into `session_plane.db` instead of embedding them into durable memory.
- Can pass changed session files into dreaming when configured with a dream runner.

## Eval harness

Main code:

- `src/dory_cli/eval.py`
- `src/dory_core/eval_judge.py`
- `eval/public/questions/`
- Private question roots passed explicitly with `--questions-root`
- `eval/INDEX.md`
- `eval/validate.py`

CLI:

- `dory eval run` — `question_id` (optional), `--questions-root`, `--runs-root`, `--top-k`, `--list-only`
- `dory ops eval-once` — `--reindex/--no-reindex` (default True), `--runs-root`, `--top-k`

Behavior:

- Loads the YAML question bank.
- Runs retrieval/wake/judging.
- Writes timestamped run directories with `results.json` and `summary.md`.
- `ops eval-once` can reindex before evaluation.
- Judging uses OpenRouter LLM with pass/partial/fail outcomes.
- Default eval question root is the public-safe synthetic suite under `eval/public/questions/`.
- Private evals must be selected explicitly with `--questions-root` and reported publicly only as aggregate pass/partial/fail counts, coverage, and failure themes.

## Deployment and install assets

Main files:

- `Dockerfile`
- `docker-compose.yml`
- `references/runbook.md`
- `references/client-runbook.md`

Bootstrap/install scripts:

- `scripts/ops/install-dory.sh`
- `scripts/ops/install-ops-launchd.sh`
- `scripts/ops/install-client-launchd.sh`
- `scripts/ops/install-client-systemd.sh`
- `scripts/ops/install-backup-cron.sh`
- `scripts/ops/backup.sh`
- `scripts/ops/client-session-shipper.py`

Installer roles:

- `host`
- `client`
- `solo`

Install intent:

- host config for HTTP/index/corpus
- client shipper for local session capture
- solo mode for a machine acting as both local host and client

## Deployment drift to watch

- `Dockerfile` exposes `8765` and `8766`, but its default command is only `dory-http`, not a combined HTTP+MCP supervisor.
- `docker-compose.yml` binds HTTP on `127.0.0.1:8766` by default, with `DORY_HTTP_BIND` and `DORY_HTTP_PORT` overrides for LAN, VPN, reverse proxy, or firewall deployments.
- `DorySettings` defaults `http_port` to `8000`, so docs should distinguish config defaults from deployment examples.
- Docker runs as fixed UID/GID `10000:10000`; host bind mounts must be writable by that identity, or use a filesystem that maps ownership appropriately.
- `docker-compose.yml` uses bridge networking and a configurable `DORY_DATA_ROOT` bind mount so public users can run Dory on the same machine, a LAN box, a VPS, or any Docker host without inheriting a project-specific path.
- Healthcheck hits unauthenticated `/healthz`, so it doesn't need bearer auth.
- `Dockerfile` installs dependencies into `/app/.venv` at build time with `uv sync --frozen --no-dev`.

## Best tests by area

Migration:

- `tests/integration/acceptance/test_memory_schema_migration_acceptance.py`
- `tests/integration/cli/test_migrate_command.py`
- `tests/integration/core/test_migration_engine.py`

Dreaming:

- `tests/integration/cli/test_dream_commands.py`
- `tests/integration/cli/test_dream_generation_commands.py`
- `tests/integration/core/test_distillation_write.py`
- `tests/integration/core/test_proposal_generation.py`

Maintenance and wiki:

- `tests/integration/cli/test_compiled_wiki_commands.py`
- `tests/integration/core/test_compiled_wiki_search.py`
- `tests/unit/test_compiled_wiki.py`
- `tests/unit/test_wiki_indexes.py`
- `tests/unit/test_maintenance.py`

Ops and watch:

- `tests/integration/cli/test_ops_commands.py`
- `tests/integration/core/test_watch_reindex.py`

Evals:

- `tests/integration/cli/test_eval_runner.py`
- `tests/integration/cli/test_eval_rerank.py`

Install assets:

- `tests/integration/cli/test_install_dory_script.py`

Docker and runbooks:

- `tests/integration/ops/test_docker_assets.py`
- `tests/integration/ops/test_runbook_paths.py`

Search and indexing:

- `tests/integration/core/test_search_engine.py`

Semantic provenance and claim publishing:

- `tests/integration/core/test_semantic_evidence_artifacts.py`
- `tests/integration/core/test_event_driven_canonical_pages.py`
- `tests/unit/test_claim_store.py`
- `tests/unit/test_claim_store_events.py`
- `tests/integration/core/test_search_realish_queries.py`
- `tests/integration/core/test_session_fallback_search.py`
- `tests/integration/core/test_reindex_pipeline.py`
- `tests/integration/core/test_reindex_invalid_docs.py`

Write and semantic write:

- `tests/integration/core/test_write_flow.py`
- `tests/integration/core/test_semantic_write_flow.py`

MCP:

- `tests/integration/mcp/test_stdio_server.py`
- `tests/integration/mcp/test_tcp_server.py`
- `tests/integration/mcp/test_tool_schema.py`
- `tests/integration/mcp/test_http_bridge.py`
- `tests/integration/mcp/test_cross_agent_visibility.py`

## Update checklist

When you change any operator-facing behavior, update:

1. This file.
2. The relevant command help or runbook.
3. At least one integration or acceptance test reference in the docs if coverage changed.
