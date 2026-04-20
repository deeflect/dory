# Current-state docs

Implementation-grounded source of truth for the repo as it exists today.

Use these docs to answer:

- what's in the codebase right now
- where each subsystem lives
- which surfaces are real vs planned
- how data moves through the system
- what tests validate each area
- where docs have drifted from implementation

## Trust order

When sources disagree:

1. Code in `src/`, `packages/`, `plugins/`, `scripts/`
2. Integration and acceptance tests in `tests/`
3. These `docs/current-state/` docs
4. Root `README.md`

Historical design specs and implementation plans are kept out of the public tree — they can contain private corpus and deployment detail. Trust current code and these docs.

## Contents

- [codebase-map.md](codebase-map.md) — repo layout, packages, source and test files, starting points
- [runtime-and-data-flow.md](runtime-and-data-flow.md) — how wake/search/get/write/session flows work in the current code, with known gaps
- [surfaces-and-integrations.md](surfaces-and-integrations.md) — CLI, HTTP, MCP, Claude bridge, Hermes, OpenClaw surfaces with parameter tables and cross-surface drift
- [operations-and-validation.md](operations-and-validation.md) — migration, dreaming, maintenance, wiki refresh, evals, deploy/install, tests by area
- [external-memory-reference.md](external-memory-reference.md) — comparison notes from Karpathy's LLM Wiki, gbrain, mem0, MemPalace with steal/avoid guidance

## Snapshot

Last synced from implementation on `2026-04-20`.

## Known drift

Things the code does differently from the original spec, or caveats worth knowing:

- **Vector store** — SQLite-backed via `SqliteVectorStore` in `src/dory_core/index/sqlite_vector_store.py`. Vector search is still brute-force O(n) cosine similarity. ANN indexing is future work if corpus size demands it.
- **CLI surface** — `migrate-tui` isn't exposed in current help; historical private plan docs are outside the public tree.
- **Surface area** — the original five-verb spec is narrower than today. Current code also exposes `active-memory`, `research`, `migrate`, `session-ingest`, `recall-event`, `public-artifacts`, metrics, and stream endpoints.
- **Semantic writes** — registry-backed and claim-backed:
  - `EntityRegistry` (`src/dory_core/entity_registry.py`) is the durable resolution layer
  - `ClaimStore` (`src/dory_core/claim_store.py`) persists active claims and claim events
  - resolved writes emit immutable evidence under `sources/semantic/YYYY/MM/DD/`
  - semantic `forget` republishes tombstones from claim history plus claim events
- **Hermes** — normalizes legacy search mode names before HTTP: `text`/`keyword`/`lexical` → `bm25`, `semantic` → `vector`. Native API names are accepted directly too.
- **Atomic writes** — critical paths use temp-write-and-replace helpers, but non-core scripts still contain plain `write_text()`.
- **Chunking** — accepts `overlap_ratio` but doesn't implement overlap.
- **Auth asymmetry** — HTTP enforces bearer auth; raw MCP TCP has no auth handshake. The Claude Code bridge forwards bearer auth to HTTP.
- **HTTP errors** — use FastAPI's default `{"detail": "..."}` instead of the contract's `{"error": {"code": "..."}}` format.
- **Recall mode** — searches all sessions, not just the current session (deviates from spec).
- **CLI link commands** — `neighbors`/`backlinks`/`lint` are top-level, not `dory link` subcommands.
- **Claude Code bridge** — exposes `dory_active_memory` but stays an HTTP-backed compatibility bridge, not native MCP.
- **OpenClaw probes** — `probeEmbeddingAvailability()` / `probeVectorAvailability()` fail closed when Dory can't prove vector availability.
- **Search warnings** — `/v1/search` can surface `warnings` when optional query expansion fails and the engine falls back.
- **Search ranking** — default hybrid/all search now gives canonical/core/project-state evidence a source prior over sessions and generated mirrors. Session evidence remains available for explicit recall/recent-history queries and as supporting tail evidence for `corpus="all"`.
- **Search dedup** — non-exact search collapses near-duplicate generated/wiki/source mirrors behind the canonical document before the final `k`. Exact search intentionally skips this collapse for cleanup-marker checks.
- **Privacy metadata** — frontmatter supports `visibility: private|internal|public` and `sensitivity: personal|financial|legal|contact|credentials|health|none`. `wiki-health` reports personal/raw/imported docs missing those fields.
- **Optional LLM retrieval** — `src/dory_core/retrieval_planner.py` can plan durable query variants and optional session queries and can reorder the final candidate set through strict-schema result selection. Planner failure falls back to deterministic search. Opt in via `DORY_QUERY_PLANNER_ENABLED=true`, `DORY_QUERY_EXPANSION_ENABLED=true`, `DORY_QUERY_RERANKER_ENABLED=true`.
- **OpenClaw status** — still snapshot-based, but now reports `custom.statusAgeMs` and `custom.statusStale` so callers can detect staleness. Source audit should inspect `packages/openclaw-dory/`, not only a live installed plugin.
- **Active-memory** — explicit calls always run staged retrieval instead of short-circuiting on intent heuristics. Optional LLM planning/composition picks retrieval queries and compresses a tiny sanitized evidence packet into a compact `## Active memory` section. The LLM path can use OpenRouter or an OpenAI-compatible local/LAN model via `DORY_ACTIVE_MEMORY_LLM_PROVIDER`; deterministic fallback runs the same shape without an LLM. The final block is budget-clamped, durable evidence excludes generated `wiki/` cache pages, and coding/writing profiles topic-filter wiki helper context so unrelated recent pages do not bleed into focused prompts.
- **Link output** — `dory_link`/`neighbors` support `max_edges` and `exclude_prefixes`; responses include `total_count` and `truncated` for dense graphs.
- **Hermes provider** — built-in memory mirroring is date-partitioned under `inbox/hermes-memory-mirror/YYYY-MM-DD.md`, and HTTP tool errors return structured `error_type`/`status_code` payloads.
- **Compiled wiki** — bounded synthesis, but now groups evidence by claim-event type and `wiki-health` audits for missing timelines too. Dory generates a Karpathy-style shell under `wiki/`: `hot.md`, `index.md`, `log.md`. Active-memory reads the shell first when present. The shell is generated from the structured claim/wiki core — never source of truth.
- **Wiki freshness** — `wiki-refresh-once` prefers claim-store-backed page rendering when claim history exists, and prunes orphaned pages under managed families. `wiki-health` also flags pages where current-state sections disagree with retirement-only event history, and reports `claim_mismatch`, `claim_event_mismatch`, and `claim_evidence_mismatch` when compiled pages drift from ledger truth.
- **Migration** — corpus-level LLM entity clustering plus a bounded final LLM QA loop (audit → repair flagged pages → re-audit). Audit and repair artifacts land in `inbox/migration-runs/` and fold into the run report.

## Update rules

When you change the codebase:

1. Update the relevant file here in the same change.
2. Document implemented behavior and file locations — not design prose.
3. Call out mismatches explicitly instead of silently rewriting history.
4. Cite the concrete source files or tests that justify the doc change.
5. If behavior is in flux, say so here rather than pretending it's settled.

## Good starting points

- **Runtime entrypoints** — `pyproject.toml`, `src/dory_cli/main.py`, `src/dory_http/app.py`, `src/dory_mcp/server.py`
- **Core runtime** — `src/dory_core/search.py`, `src/dory_core/write.py`, `src/dory_core/semantic_write.py`, `src/dory_core/session_ingest.py`, `src/dory_core/ops.py`
- **Resolver and claim runtime** — `src/dory_core/entity_registry.py`, `src/dory_core/claim_store.py`, `src/dory_core/canonical_pages.py`, `src/dory_core/compiled_wiki.py`
- **Best cross-surface validation** — `tests/integration/acceptance/test_phase4_multiface.py`
