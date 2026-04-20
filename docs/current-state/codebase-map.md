# Codebase map

Where the real code for each concern lives.

## Top-level layout

- `src/dory_core/`
  - core domain logic: search, write, indexing, migration, session ingest, dreaming, maintenance, wiki generation, status
- `src/dory_cli/`
  - Typer CLI entrypoints and command wiring
- `src/dory_http/`
  - FastAPI server and HTTP auth/metrics
- `src/dory_mcp/`
  - native MCP bridge for stdio and TCP
- `packages/openclaw-dory/`
  - OpenClaw plugin package and TypeScript adapter
- `plugins/hermes-dory/`
  - Hermes provider implementation over HTTP
- `scripts/`
  - install/bootstrap scripts, session shipper, Claude bridge, Codex wrapper, migration helpers
- `tests/`
  - unit, integration, and acceptance coverage
- `references/`
  - runbooks and supporting docs
- `eval/`
  - public synthetic eval questions, private eval boundary docs, categories, validator, and run outputs

## Entry points

- `pyproject.toml`
  - console scripts: `dory`, `dory-http`, `dory-mcp`
- `scripts/codex/dory`
  - repo-local wrapper that injects `--corpus-root` and `--index-root`
- `scripts/claude-code/dory-mcp-http-bridge.py`
  - Claude Code bridge that forwards tool calls to HTTP (separate from native MCP)

## `src/dory_core/` map

### Read path

- `wake.py`
  - frozen wake block builder
- `search.py`
  - durable search, recall search, hybrid ranking, query expansion, retrieval-plan-aware session merge, and warnings
- `active_memory.py`
  - optional staged pre-reply memory retrieval over generated wiki shell pages, wake, durable search, and session recall; explicit calls always execute the flow and can use an LLM planner/composer
- `research.py`
  - grounded research artifact generation with answer/evidence sections
- `link.py`
  - wikilink extraction, known-entity edge extraction, graph queries
- `query_expansion.py`
  - LLM-powered query rewriting with light plain-text fallback parsing for improved retrieval recall
- `retrieval_planner.py`
  - strict-schema LLM planner/composer for hybrid search and active-memory retrieval, plus candidate result selection
- `rerank.py`
  - rerank mode resolution

### Write path

- `write.py`
  - low-level path-first markdown writes with validation, quarantine, reindex, and edge sync
- `semantic_write.py`
  - registry-backed subject resolution, semantic evidence artifact creation, claim mutation, canonical rewrites, and tombstone republishing layered over `write.py`
- `canonical_pages.py`
  - canonical page scaffolding, active-claim rendering, event-driven timeline/evidence rendering, retired tombstone rendering
- `entity_registry.py`
  - durable entity registry used by semantic resolution and alias routing
- `claim_store.py`
  - SQLite-backed current claims and claim-event ledger with provenance-rich event reads
- `artifacts.py`
  - report/briefing/wiki-note/proposal artifact path resolution and writing
- `maintenance.py`
  - LLM-backed maintenance inspection plus wiki-health auditing against claim/event/evidence coverage
- `wiki_indexes.py`
  - generated wiki shell pages (`hot.md`, `index.md`, `log.md`) plus family indexes over compiled wiki content, preferring claim-store summaries/recency when available

### Storage and indexing


- `markdown_store.py`
  - corpus scan and markdown parsing
- `chunking.py`
  - heading-aware chunk generation with tiktoken-based token counting and sliding-window overlap between adjacent chunks
- `index/reindex.py`
  - full and partial reindex orchestration
- `index/sqlite_store.py`
  - durable metadata/chunk/FTS/cache storage
- `index/sqlite_vector_store.py`
  - SQLite-backed chunk vector store; vector search is still brute-force cosine over stored vectors, with legacy JSON import fallback for older indexes
- `index/migrations.py`
  - SQLite schema bootstrap/migrations
- `embedding.py`
  - Gemini embedder and runtime embedder construction
- `token_counting.py`
  - tiktoken-based and heuristic token counting with per-agent encoding

### Session plane

- `session_ingest.py`
  - session markdown ingest and sidecar upsert
- `session_plane.py`
  - separate SQLite FTS store for session evidence with coverage/recency ranking and focused snippets
- `session_shipper.py`
  - shipping helpers used by local collectors
- `session_collectors.py`, `session_capture.py`, `session_cleaner.py`
  - harness discovery and session cleaning support

### Migration and normalization


- `migration_engine.py`
  - staged legacy-corpus migration pipeline with per-document artifacts, corpus-level entity clustering, markdown normalization for supported structured/text legacy inputs, evidence-first fallback, bounded explicit structured-export adapters, claim-store-backed compilation, and final audit/repair artifacts
- `migration_plan.py`
  - preflight planning and estimates
- `migration_llm.py`
  - strict-schema LLM extraction, entity clustering, post-generation audit, and bounded page repair for migration
- `migration_prompts.py`
  - migration prompt/schema layer
- `migration_normalize.py`, `migration_resolve.py`, `migration_types.py`, `migration_events.py`
  - routing, typed migration artifacts, and post-extraction normalization/resolution helpers
- `timeline_migration.py`
  - appends timeline sections to existing documents by splitting out date-prefixed bullets
- `corpus_normalization.py`
  - document classification into structural categories and decision extraction

### Dreaming, maintenance, wiki, ops

- `dreaming/extract.py`
  - session distillation
- `dreaming/proposals.py`
  - semantic write proposal generation
- `dreaming/recall.py`
  - repeated-recall promotion candidate processing
- `dreaming/events.py`
  - session-closed event used to trigger dream/distillation processing
- `maintenance.py`
  - maintenance suggestion generation and wiki health checks
- `compiled_wiki.py`, `wiki_indexes.py`
  - generated wiki pages and wiki index files, with claim-store-backed refresh when claim history exists plus stale generated-page pruning during refresh
- `ops.py`
  - batch jobs and watch loop orchestration
- `watch.py`
  - filesystem buffering and markdown change helpers

### Plumbing and schema


- `types.py`
  - Pydantic request/response models
- `config.py`
  - env-driven settings and runtime path resolution
- `schema.py`
  - canonical family/section vocabulary
- `metadata.py`
  - normalized frontmatter and write-target inference
- `status.py`
  - runtime status payload
- `openclaw_parity.py`
  - recall-event tracking and public artifact listing
- `llm/openrouter.py`
  - OpenRouter client used by query expansion, dreaming, maintenance, and eval judging
- `frontmatter.py`
  - YAML frontmatter parsing, dumping, and merging for markdown documents
- `fs.py`
  - atomic file-write helper and safe resolved-path checks under the corpus root
- `slug.py`
  - kebab-case slug generation via Unicode normalization
- `errors.py`
  - base exception hierarchy (`DoryError`, `DoryConfigError`, `DoryValidationError`)
- `claims.py`
  - data structures for verifiable claims with evidence references
- `entity_registry.py`
  - dataclasses and persistence for canonical entity IDs, aliases, and target paths
- `claim_store.py`
  - durable `claims` and `claim_events` tables used by semantic publishing
- `eval_judge.py`
  - LLM-based judging of retrieval results (pass/partial/fail)

## `src/dory_cli/` map

- `main.py`
  - all main CLI commands and nested groups
- `eval.py`
  - eval harness app wired under `dory eval`

## `src/dory_http/` map

- `app.py`
  - route definitions and server bootstrap
- `auth.py`
  - optional bearer-token enforcement and token issuing
- `metrics.py`
  - Prometheus-style metrics rendering

## `src/dory_mcp/` map

- `server.py`
  - stdio/TCP MCP bridge
- `tools.py`
  - MCP tool schemas and tool-to-handler mapping

## Integrations

- `packages/openclaw-dory/src/index.ts`
  - OpenClaw plugin runtime, Dory HTTP client, active-memory/status probing, tool registration, public artifact hooks
- `plugins/hermes-dory/provider.py`
  - Hermes provider and config loading
- `plugins/hermes-dory/config.example.yaml`
  - example provider configuration

## Scripts and ops assets

- `scripts/ops/install-dory.sh`
  - host/client/solo bootstrap
- `scripts/ops/client-session-shipper.py`
  - local session collector and shipper
- `scripts/ops/install-client-launchd.sh`
  - macOS client service installer
- `scripts/ops/install-client-systemd.sh`
  - Linux client service installer
- `scripts/ops/install-ops-launchd.sh`
  - macOS ops job installer
- `scripts/ops/install-backup-cron.sh`
  - cron job installer for scheduled backups
- `scripts/ops/backup.sh`
  - backup push script
- `Dockerfile`, `docker-compose.yml`
  - container packaging and compose deployment

## Tests

### Acceptance

- `tests/integration/acceptance/test_phase4_multiface.py`
  - shared memory across HTTP, MCP, Hermes, and wrapper assets
- `tests/integration/acceptance/test_phase2_shared_memory.py`
  - write from one agent visible in another agent's wake block
- `tests/integration/acceptance/test_memory_schema_migration_acceptance.py`
  - migration acceptance path

### Core

- `tests/integration/core/test_search_engine.py`
  - core search modes
- `tests/integration/core/test_semantic_write_flow.py`
  - semantic write behavior, semantic evidence artifacts, and tombstone republishing
- `tests/integration/core/test_semantic_evidence_artifacts.py`
  - semantic evidence artifact creation and claim provenance
- `tests/integration/core/test_event_driven_canonical_pages.py`
  - canonical timeline/evidence rendering from claim events
- `tests/integration/core/test_wake_builder.py`
  - frozen block construction and token estimation
- `tests/integration/core/test_write_flow.py`
  - append/create/replace/forget, frontmatter merge, timeline markers, reindex
- `tests/integration/core/test_sqlite_vector_store.py`
  - vector record persistence, reload, and legacy JSON import fallback
- `tests/integration/core/test_sqlite_store.py`
  - SQLite file/chunk/FTS row writes
- `tests/integration/core/test_reindex_pipeline.py`
  - full reindex populates both stores, embedding cache reuse
- `tests/integration/core/test_reindex_invalid_docs.py`
  - reindex skips files without or with malformed frontmatter
- `tests/integration/core/test_markdown_store.py`
  - corpus walk, frontmatter parsing, skip non-frontmatter files
- `tests/integration/core/test_link_queries.py`
  - neighbors/backlinks/lint, auto-entity edges, edge cleanup on delete
- `tests/integration/core/test_active_memory_flow.py`
  - corpus selector routing, active-memory engine invocation
- `tests/integration/core/test_compiled_wiki_search.py`
  - hybrid search prefers compiled wiki over raw project notes
- `tests/unit/test_claim_store.py`, `tests/unit/test_claim_store_events.py`
  - claim mutation and claim-event provenance behavior
- `tests/unit/test_compiled_wiki.py`
  - compiled wiki event-driven evidence rendering
- `tests/integration/core/test_distillation_write.py`
  - distillation writer, OpenRouter distiller, wiki refresh
- `tests/integration/core/test_proposal_generation.py`
  - proposal JSON generation and OpenRouter-assisted actions
- `tests/integration/core/test_migration_engine.py`
  - canonical bootstrapping, evidence linking, LLM classification, parallel workers
- `tests/integration/core/test_research_pipeline.py`
  - research artifact from compiled wiki and project sources
- `tests/integration/core/test_search_realish_queries.py`
  - ranking prefers live over archived, recent over superseded
- `tests/integration/core/test_session_fallback_search.py`
  - recall mode, hybrid fallback to sessions when durable is weak
- `tests/integration/core/test_watch_reindex.py`
  - MarkdownChangeHandler reindexes on change, ignores non-markdown

### HTTP

- `tests/integration/http/test_http_routes.py`
  - core HTTP verb coverage
- `tests/integration/http/test_session_ingest_http.py`
  - session ingest and recall behavior
- `tests/integration/http/test_bearer_auth.py`
  - bearer token enforcement and rejection
- `tests/integration/http/test_get_contract.py`
  - get returns frontmatter and SHA-256 hash
- `tests/integration/http/test_memory_write_http.py`
  - semantic write/replace/forget, quarantine, auth, validation
- `tests/integration/http/test_active_memory_http.py`
  - active-memory HTTP endpoint
- `tests/integration/http/test_research_http.py`
  - research artifact writing via HTTP
- `tests/integration/http/test_migrate_http.py`
  - migration via HTTP endpoint
- `tests/integration/http/test_openclaw_parity_http.py`
  - recall-event, public-artifacts, status tracking
- `tests/integration/http/test_hermes_shim_contract.py`
  - Hermes DoryMemoryProvider covering all verbs
- `tests/integration/http/test_status_metrics.py`
  - status counts and Prometheus metrics
- `tests/integration/http/test_stream_route.py`
  - SSE stream endpoint events

### MCP

- `tests/integration/mcp/test_stdio_server.py`
  - stdio server tool listing and dispatch
- `tests/integration/mcp/test_tcp_server.py`
  - TCP server tool listing and dispatch
- `tests/integration/mcp/test_tool_schema.py`
  - native naming, semantic fields, legacy path fields
- `tests/integration/mcp/test_http_bridge.py`
  - Claude Code bridge routing memory_write, active-memory, and structured HTTP error envelopes
- `tests/integration/mcp/test_get_parity.py`
  - native MCP `get` parity with HTTP metadata fields
- `tests/integration/mcp/test_cross_agent_visibility.py`
  - MCP-level cross-agent write visibility

### CLI

- `tests/integration/cli/test_ops_commands.py`
  - ops/watch/dream/maintenance behavior
- `tests/integration/cli/test_migrate_command.py`
  - migration CLI commands
- `tests/integration/cli/test_dream_commands.py`
  - dream subcommands
- `tests/integration/cli/test_dream_generation_commands.py`
  - dream proposal and distillation generation
- `tests/integration/cli/test_compiled_wiki_commands.py`
  - wiki refresh and health commands
- `tests/integration/cli/test_eval_runner.py`
  - eval harness runner
- `tests/integration/cli/test_eval_rerank.py`
  - eval rerank handling with explicit rerank enablement in `v0`
- `tests/integration/cli/test_install_dory_script.py`
  - install script validation
- `tests/integration/cli/test_cli_read_path.py`
  - wake/search/get/status/reindex from fixture corpus
- `tests/integration/cli/test_client_session_shipper_cli.py`
  - session shipper auto-discovery and spool
- `tests/integration/cli/test_entrypoints.py`
  - console script --help responses
- `tests/integration/cli/test_init_command.py`
  - init creates layout without overwriting
- `tests/integration/cli/test_link_cli.py`
  - neighbors/backlinks/lint CLI commands
- `tests/integration/cli/test_mcp_entrypoint.py`
  - dory-mcp --help
- `tests/integration/cli/test_research_commands.py`
  - research and wiki-refresh-indexes CLI
- `tests/integration/cli/test_semantic_write_commands.py`
  - memory-write subject routing and quarantine

### Ops

- `tests/integration/ops/test_docker_assets.py`
  - docker-compose ports, Dockerfile uv usage, release workflow target
- `tests/integration/ops/test_runbook_paths.py`
  - runbook mentions reindex recovery, backup/restore script contents

## Known drift

- `SqliteVectorStore` in `src/dory_core/index/sqlite_vector_store.py` keeps vectors in `dory.db` and avoids whole-file JSON rewrites. Vector search is still brute-force O(n) cosine similarity; replace with ANN only when corpus size demands it.
