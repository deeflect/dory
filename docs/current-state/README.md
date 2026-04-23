# Current-state docs

Current implementation notes for the public repository. These docs describe what exists now, not what older design plans proposed.

Use these when you need to answer:

- What's in the codebase today
- Where each subsystem lives
- Which surfaces are real vs planned
- How data moves through the system
- Which tests cover which area
- Where the code has drifted from the original spec

## Trust order

When sources disagree:

1. Code in `src/`, `packages/`, `plugins/`, `scripts/`
2. Integration and acceptance tests in `tests/`
3. These `docs/current-state/` docs
4. Root `README.md`

Historical design specs and implementation plans stay out of the public tree because they can carry private corpus or deployment detail. Trust the code and these docs.

## Contents

- [codebase-map.md](codebase-map.md) — repo layout, packages, source and test files, starting points
- [runtime-and-data-flow.md](runtime-and-data-flow.md) — how wake/search/get/write/session flows work in the current code, with known gaps
- [surfaces-and-integrations.md](surfaces-and-integrations.md) — CLI, HTTP, MCP, Claude bridge, Hermes, OpenClaw surfaces with parameter tables and cross-surface drift
- [hermes-research-publish.md](hermes-research-publish.md) — Hermes `dory_publish_research` flow, target paths, dry-run sequence, and verification
- [operations-and-validation.md](operations-and-validation.md) — migration, dreaming, maintenance, wiki refresh, evals, deploy/install, tests by area
- [external-memory-reference.md](external-memory-reference.md) — comparison notes from Karpathy's LLM Wiki, gbrain, mem0, MemPalace with steal/avoid guidance

## Snapshot

Last synced from implementation on `2026-04-20`.

## Known drift

What the code does differently from the original spec, or caveats worth knowing.

### Surface area

- **Surface grew past the five-verb spec.** Alongside `wake / search / get / memory-write / link`, current code ships `active-memory`, `research`, `migrate`, `session-ingest`, `recall-event`, `public-artifacts`, metrics, and stream endpoints.
- **CLI link commands** - `neighbors`, `backlinks`, and `lint` are top-level, not subcommands under `dory link`.
- **`migrate-tui`** is not exposed in CLI help. The older plan docs for it live in the private tree.

### Storage and indexing

- **Vector store** - SQLite-backed (`SqliteVectorStore` in `src/dory_core/index/sqlite_vector_store.py`). Search is still brute-force O(n) cosine similarity. ANN indexing is on the list if corpus size ever demands it.
- **Chunking** - accepts an `overlap_ratio` argument but does not implement overlap.
- **Atomic writes** - critical paths use temp-write-and-replace. Some non-core scripts still use plain `write_text()`.

### Auth

- **HTTP** - enforces bearer auth. Raw MCP over TCP has no auth handshake. The Claude Code bridge forwards the HTTP bearer to the server.
- **HTTP errors** - return FastAPI's default `{"detail": "..."}` instead of the contract's `{"error": {"code": "..."}}` shape.

### Search

- **Durable vs session search** - durable BM25/vector search indexes canonical/project/wiki/digest-style markdown, not raw `logs/sessions/**`. Raw sessions live in `session_plane.db` and are available through `mode="recall"`, `corpus="sessions"`, or merged tail evidence when `corpus="all"`.
- **Ranking** - default durable hybrid search stays durable-only and prefers canonical, core, and project-state evidence over generated mirrors. Session evidence is available for explicit recall/recent-history queries only when the request uses the session corpus or `corpus="all"`.
- **Live sessions** - generic `corpus="all"` searches do not merge `active` or `interrupted` session hits unless the query asks for recent/session evidence. This keeps in-progress agent transcripts out of normal project answers while preserving explicit recall behavior.
- **Dedup** - non-exact search collapses near-duplicate generated, wiki, and source mirrors behind the canonical doc before returning `k` results. Exact search skips this on purpose so cleanup-marker checks keep working.
- **Warnings** - `/v1/search` surfaces `warnings` when optional query expansion fails and the engine falls back.
- **Recall mode** searches all sessions, not just the current session. This deviates from the spec.
- **Optional LLM retrieval** - `retrieval_planner.py` can plan durable and optional session query variants and reorder final candidates via strict-schema selection. Planner failure falls back to deterministic search. Opt in with `DORY_QUERY_PLANNER_ENABLED`, `DORY_QUERY_EXPANSION_ENABLED`, `DORY_QUERY_RERANKER_ENABLED`.

### Semantic writes
- Resolved through `EntityRegistry` (`src/dory_core/entity_registry.py`), the durable name-to-entity layer.
- Backed by `ClaimStore` (`src/dory_core/claim_store.py`), which keeps active claims and a claim-event log.
- Every resolved write drops an immutable evidence file under `sources/semantic/YYYY/MM/DD/`.
- Semantic `forget` rebuilds the tombstone from claim history + events instead of a static overwrite.

### Active-memory
- Explicit calls always run the full staged retrieval path. No intent-heuristic short-circuit.
- Optional LLM plan/compose stages pick queries and compress a small sanitized evidence packet into a `## Active memory` block. Without an LLM, the same shape runs deterministically.
- Backend picked via `DORY_ACTIVE_MEMORY_LLM_PROVIDER`: OpenRouter, an OpenAI-compatible local/LAN model, `auto`, or `off`.
- Output is budget-clamped. Durable evidence skips generated `wiki/` cache pages. Coding and writing profiles topic-filter wiki helper context so unrelated recent pages don't bleed in.

### Compiled wiki
- Generates a Karpathy-style shell under `wiki/`: `hot.md`, `index.md`, `log.md`. Active-memory reads the shell first when present. The shell is generated from the claim/wiki core — never source of truth.
- `wiki-refresh-once` prefers claim-store-backed rendering when claim history exists, and prunes orphaned pages under managed families.
- `wiki-health` reports `claim_mismatch`, `claim_event_mismatch`, and `claim_evidence_mismatch` when compiled pages drift from the ledger, plus flags pages whose current-state sections disagree with retirement-only event history.

### Migration

- Corpus-level LLM entity clustering plus a bounded final audit -> repair flagged pages -> re-audit loop.
- Audit and repair artifacts land in `inbox/migration-runs/` and roll up into the run report.

### Integrations

- **Claude Code bridge** - exposes `dory_active_memory` but stays an HTTP-backed compatibility bridge, not native MCP.
- **Hermes provider** - normalizes legacy search mode names before HTTP (`text`/`keyword`/`lexical` -> `bm25`, `semantic` -> `vector`). Native names are also accepted. Memory mirror is date-partitioned under `inbox/hermes-memory-mirror/YYYY-MM-DD.md`. HTTP tool errors return structured `error_type` / `status_code`.
- **OpenClaw** - `probeEmbeddingAvailability()` / `probeVectorAvailability()` fail closed when Dory cannot confirm vectors are available. Status remains snapshot-based but now reports `custom.statusAgeMs` and `custom.statusStale`. Audit the source under `packages/openclaw-dory/`, not just an installed plugin.
- **Link output** - `dory_link` / `neighbors` support `max_edges` and `exclude_prefixes`. Responses include `total_count` and `truncated` so clients know when a dense graph was capped.

### Privacy metadata

- Frontmatter supports `visibility: private | internal | public` and `sensitivity: personal | financial | legal | contact | credentials | health | none`.
- `wiki-health` flags personal, raw, and imported docs missing those fields.

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
