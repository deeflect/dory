# Runtime and data flow

What the code actually does at runtime. If a section here contradicts the spec, trust the code.

## Runtime faces

Same read/write core, different wrappers:

- CLI - `src/dory_cli/main.py`
- HTTP - `src/dory_http/app.py`
- Native MCP bridge - `src/dory_mcp/server.py`
- Claude bridge - `scripts/claude-code/dory-mcp-http-bridge.py`
- Hermes provider - `plugins/hermes-dory/provider.py`
- OpenClaw plugin - `packages/openclaw-dory/src/index.ts`

Everything routes through `dory_core`. The wrappers are thin.

## Storage model

Three layers:

### 1. Markdown corpus (source of truth)

- Scanned by `MarkdownStore` in `src/dory_core/markdown_store.py`
- Frontmatter parsed by `src/dory_core/frontmatter.py`
- Chunked by `chunk_markdown()` in `src/dory_core/chunking.py`

### 2. Index sidecar (disposable)

Lives under `index_root`. Blow it away and rebuild any time.

- `index_root/dory.db` - SQLite. Holds the entity registry, claims, claim events, files, chunks, FTS, edge graph, embedding cache, chunk vectors, recall log, and parity tables.
- `chunk_vectors` - the vector table, managed by `SqliteVectorStore`. Brute-force O(n) cosine today. A legacy `index_root/lance/chunks_vec.json` is imported as a fallback when the SQLite vector table is empty.

### 3. Session evidence plane (separate)

- `index_root/session_plane.db` - SQLite, managed by `SessionEvidencePlane` in `src/dory_core/session_plane.py`.
- Used for session recall and ingest. Deliberately separate from durable memory so raw logs don't leak into canonical answers.

## Reindex flow

Full reindex:

1. `MarkdownStore.scan()` walks `*.md`.
2. Each doc is parsed and chunked.
3. `reindex_corpus()` prepares file rows and chunk rows.
4. New embeddings are generated for uncached content hashes.
5. `SqliteStore.replace_documents()` rewrites file/chunk/FTS/cache state.
6. `SqliteVectorStore.replace()` rewrites vector rows in `dory.db`.

Partial reindex:

1. Changed relative paths are resolved.
2. Old chunk IDs for those paths are deleted.
3. Changed docs are reparsed and re-embedded.
4. SQLite/vector rows are upserted.

Main code:

- `src/dory_core/index/reindex.py`
- `src/dory_core/index/sqlite_store.py`
- `src/dory_core/index/sqlite_vector_store.py`

## Wake flow

`WakeBuilder` in `src/dory_core/wake.py` builds a frozen prompt block from:

- `core/user.md`
- `core/soul.md`
- `core/env.md`
- `core/active.md`
- optional pinned decisions
- optional recent session summaries

Current behavior:

- respects a token budget
- recent sessions are selected by file mtime, not lexicographic path order
- recent sessions are summarized to one line each
- it's a static snapshot at call time

## Search flow

`SearchEngine` in `src/dory_core/search.py` supports:

- `bm25`
- `vector`
- `hybrid`
- `recall`
- `exact`

Input aliases:

- `text`, `keyword`, `lexical` -> `bm25`
- `semantic` -> `vector`

`corpus` values:

- `durable`
- `sessions`
- `all`

### Durable search

- BM25 reads from SQLite FTS; scores are negative (raw SQLite `bm25()` output)
- Vector search reads all vector rows and scores by cosine similarity (brute-force)
- Hybrid search:
  - gets BM25 candidates
  - gets vector candidates
  - fuses rankings via RRF
  - applies priors for canonical/current/source-backed docs
  - returns `evidence_class` and confidence in the default client-facing response; `rank_score`, `score_normalized`, raw `score`, and `frontmatter` are diagnostic fields exposed only with `debug=true`
  - demotes raw inbox, generated, and session-like material unless a scope/exact query asks for it
  - adds lexical and temporal boosts
  - optionally expands queries through OpenRouter (`query_expansion.py`) when `DORY_QUERY_EXPANSION_ENABLED=true`
  - when OpenRouter retrieval planning is configured with `DORY_QUERY_PLANNER_ENABLED=true`, `retrieval_planner.py` can replace heuristic expansion/session decisions with a strict search plan of durable queries plus optional session queries
  - can rerank rows when `DORY_QUERY_RERANKER_ENABLED=true` and `rerank` permits it
  - expanded queries contribute to both BM25 and vector candidate generation
  - durable/session fallback merge uses rank-plus-coverage scoring instead of raw concatenation

Score inconsistency: BM25-only returns negative scores; hybrid/vector return positive. API consumers shouldn't compare scores across modes.

### Session recall

- `mode="recall"` or `corpus="sessions"` searches `session_plane.db`
- Results are marked as lower-trust session evidence
- Session-plane ranking combines FTS hits with token coverage and recency
- Session snippets are cut around matching query terms instead of always returning the first bytes
- Recall mode currently searches ALL session documents, not just the current session (deviates from spec)

### Session fallback

In hybrid search:

- Deterministic path — weak durable results trigger a fallback into session-plane results.
- With the LLM retrieval planner on — session use becomes part of the explicit plan instead of relying on the weakness heuristic. The final merged durable + session set can also be reordered via strict-schema selection.
- Generic `corpus="all"` merges skip `active` and `interrupted` session rows unless the query asks for session or recent-history evidence. This keeps live agent transcripts out of normal project-state answers while preserving explicit recall.

### Client session sync

- The background client shipper scans local harness stores on its poll interval and posts cleaned sessions to `/v1/session-ingest`.
- The Claude Code HTTP bridge also runs the shipper once immediately before `dory_wake` by default. This makes just-finished local Codex/Claude/OpenClaw/Hermes sessions available to a wake call without waiting for the next poll.
- The pre-wake sync uses the configured client env, spool, and checkpoint paths. Disable it with `DORY_SYNC_SESSIONS_ON_WAKE=false`.

Default stays deterministic so interactive search stays fast. The LLM path is opt-in.

## Active-memory flow

`ActiveMemoryEngine` in `src/dory_core/active_memory.py`. Optional staged retrieval helper, not a full autonomous sub-agent.

Stages:

1. Explicit call enters the staged flow.
2. Request `profile` resolves to `general`, `coding`, `writing`, `privacy`, or `personal`. `auto` falls back to prompt classification.
3. The profile's source policy decides the wake profile, whether session evidence is allowed, whether the generated wiki shell is used as helper context, and which path families are blocked.
4. Optional retrieval planner turns the prompt plus compact helper context into durable and optional session queries.
5. Durable hybrid search with rerank enabled by default.
6. Session-plane recall only when the profile allows it and the prompt asks for recency.
7. Optional composer compresses a tiny sanitized evidence packet into a synthesis block.
8. Final output: synthesis + bounded durable/session evidence under the request token budget. Response includes the resolved profile.

Profile rules:

- `coding` blocks personal/voice paths.
- `writing` loads voice context without full identity.
- `privacy` is boundary-only: no session evidence, no wiki helper, no inbox, no people pages, no personal knowledge.

LLM path (optional):

- Provider via `DORY_ACTIVE_MEMORY_LLM_PROVIDER`: `openrouter`, `local` (OpenAI-compatible endpoint from `DORY_LOCAL_LLM_*`), `auto` (local then OpenRouter), or `off`.
- Stages via `DORY_ACTIVE_MEMORY_LLM_STAGES`: `plan`, `compose`, or `both`. Deterministic retrieval always runs; the LLM only touches the stages you enable.
- Read-only. The LLM sees sanitized snippets and strict schemas. No write path. If the deadline is tight, LLM stages are skipped.

Other notes:

- Wiki pages are helper context only and are never returned as citeable sources. Durable evidence excludes `wiki/` so generated cache pages do not outrank canonical files.
- Output starts with a `## Active memory` synthesis across current focus, helper hints, and selected durable/session hits, followed by bounded evidence sections. When a canonical file is available locally, active-memory combines the focused search snippet with a compact canonical excerpt so `include_wake=false` calls are not reduced to a single generic line.

## Get flow

`get` is intentionally simple.

HTTP `GET /v1/get`:

- resolves a corpus-relative path
- prevents escape from the corpus root
- slices lines via `from` and `lines` query params
- returns 404 for paths outside the configured corpus, even when a memory document cites those paths as implementation evidence
- returns content, frontmatter, hash, and line counts

Native MCP `dory_get` returns the same path, line, frontmatter, and hash metadata.

## Path-first write flow

`WriteEngine` in `src/dory_core/write.py` is the low-level writer.

Supported kinds:

- `append`
- `create`
- `replace`
- `forget`

Core behavior:

- validates relative targets and resolves final parents under the corpus root before writing
- validates/normalizes frontmatter
- scans for prompt-injection patterns (subset of spec patterns; doesn't cover "ignore all instructions" or Unicode tag characters)
- rejects invisible unicode
- supports quarantine mode for unsafe content
- preserves tombstones for `forget`
- allows dotted relative targets like `*.tombstone.md` for internal tombstone rewrites
- updates the index immediately when embedder and `index_root` are present
- resyncs link edges for changed docs

Known gaps:

- the main write engine uses atomic temp-write-and-replace, but non-core scripts still use plain `write_text()`
- `forget` still spans two file replacements and isn't transactional across both paths
- raw write validation is still path-based; the semantic layer is preferred for agent-facing writes

## Semantic write flow

`SemanticWriteEngine` in `src/dory_core/semantic_write.py` is the preferred durable write layer.

Pipeline:

1. Resolve the subject through `EntityRegistry` (with fallback matching).
2. Route to a canonical target path.
3. Drop an immutable evidence artifact under `sources/semantic/YYYY/MM/DD/*.md`.
4. Append / replace / forget through `WriteEngine`.
5. Update `ClaimStore` active claims and claim events using the evidence artifact as provenance.
6. Rebuild the canonical page from active claims + events.
7. On `forget`, republish the tombstone page from claim history + events.

Notes:

- Person, project, concept, decision, and core pages are structured section docs.
- `Timeline` and `Evidence` on those pages come from claim events, not ad-hoc appends.
- Every resolved semantic write emits an immutable evidence artifact unless quarantined.
- Claim events carry `entity_id`, `event_type`, `reason`, `evidence_path`, `created_at`.
- Semantic `forget` supersedes the original page and writes the retired history + evidence view to the tombstone.
- Unresolved low-confidence subjects get rejected or quarantined.
- `_sync_registry()` refreshes registry state inside the same process, so writes can resolve subjects that were just established.

## Migration flow

`MigrationEngine` in `src/dory_core/migration_engine.py`. Four stages when LLM support is on:

1. Per-document strict extraction
2. Evidence-first staging for unresolved docs
3. Corpus-level entity clustering across candidates
4. Claim-store-backed canonical compilation, plus optional audit / repair pass on generated pages

### Inputs

- Markdown files + legacy structured inputs: `.json`, `.jsonl`, `.ndjson`, `.txt`, `.yaml`, `.yml`, `.toml`, `.csv`. Non-markdown inputs are normalized to markdown before classification and land as `*.json.md`, `*.jsonl.md`, etc. so the corpus stays markdown-first.
- Per-document artifacts land under `inbox/migration-documents/<run_id>/` and record fallback reasons when LLM promotion degrades to evidence-only.

### No-LLM promotion

Unresolved docs stay evidence-only or quarantined by default, but a narrow set of structured inputs can promote deterministically:

- Transcript-shaped `.jsonl` / `.ndjson` session evidence with clear assistant statements.
- Typed JSON with an explicit family (`project`, `person`, `decision`, `concept`) plus a title and summary.
- Schema-tagged exports with a registered adapter: `dory.project_export.v1`, `dory.person_export.v1`, `dory.decision_export.v1`, `dory.concept_export.v1`. Unknown schemas stay evidence-only.

### Claim-write policy

Migration doesn't just append every atom — it maps them to claim kinds:

- `project_update` → claim kind `state`. A newer `project_update` for the same project replaces the current active state claim instead of leaving multiple actives side by side.
- `person_fact` / `concept_claim` → claim kind `fact`.
- `goal` / `open_question` / `followup` / `timeline_event` → claim kind `note`.

Extracted source dates (from `time_ref`) are preserved on the written claims so canonical timelines reflect when things happened, not when migration ran.

### Entity clustering

Resolved docs go through a corpus-level clustering pass before atoms are committed. Clustering merges aliases (e.g. `person:primary-user` and `person:primary-user-alias`) across source files before claims and pages are written.

### Core docs

Core docs map to specific sections instead of a generic `Role` fallback:

- `core/user.md` → `Summary`
- `core/identity.md` → `Role`
- `core/soul.md` → `Voice`
- `core/env.md` → `Environment`
- `core/active.md` → `Current Focus`
- `core/defaults.md` → `Default Operating Assumptions`

### Audit and repair

- After compilation, migration emits `inbox/migration-runs/<run_id>.audit.json`.
- If pages are flagged, it emits `inbox/migration-runs/<run_id>.repair.json`, applies one bounded grounded repair pass, and re-audits before writing the final report and run artifact.
- Entity-resolution, audit, and repair failures that previously degraded silently now persist as fallback warnings in the run report.

## Session ingest flow

`SessionIngestService` in `src/dory_core/session_ingest.py`:

1. Only accepts `logs/sessions/**/*.md`.
2. Writes a session markdown file under the corpus.
3. Writes a session evidence row into `session_plane.db`.
4. Doesn't trigger a full durable reindex.

Why the separation matters:

- Session evidence is searchable quickly.
- It doesn't pollute durable canonical memory automatically.
- The markdown write uses the same atomic temp-write-and-replace helper as the main durable write path.

## Link graph flow

Link behavior splits into two mechanisms:

- Explicit wikilinks like `[[people/alex|Alex]]`
- Implicit known-entity matching against corpus entities

During indexed writes:

1. Edges are extracted from markdown.
2. Previous edges from the source doc are deleted.
3. New edges are inserted into SQLite.

Graph reads exposed via:

- `neighbors`
- `backlinks`
- `lint`

in `src/dory_core/link.py`.

`neighbors` and `backlinks` are bounded by `max_edges` and can filter noisy path families with `exclude_prefixes`. Responses include `count`, `total_count`, and `truncated`, so agent clients can keep dense project/core graphs small without losing the fact that more edges exist.

Note: the code block regex in `link.py` (`re.compile(r"```.*?```", re.DOTALL)`) can match incorrectly across multiple fenced code blocks and doesn't handle unclosed blocks.

## Research flow

`ResearchEngine` in `src/dory_core/research.py` is still bounded, but no longer a snippet dump.

- Runs hybrid search with rerank enabled.
- Requests compact snippets rather than whole chunk bodies.
- Builds a grounded artifact body with `Question`, `Answer`, `Evidence`, and optional `Session Evidence` sections.
- Dedupes source paths before returning the artifact.
- Optionally persists the artifact through `ArtifactWriter`.

Artifact targets:

- reports -> `references/reports/`
- briefings -> `references/briefings/`
- wiki notes -> `wiki/concepts/`
- proposals -> `inbox/proposed/`

## OpenClaw parity flow

`src/dory_core/openclaw_parity.py` adds two parity surfaces:

- recall event tracking
- public artifact listing

Current code supports:

- `POST /v1/recall-event`
- `GET /v1/public-artifacts`
- parity diagnostics inside status

## Claim-driven publishing

`ClaimStore` plus the canonical / wiki renderers split current state from history.

- Active / current sections: from active claims.
- `Timeline`: from ordered claim events.
- `Evidence`: from deduped `evidence_path` values on those events.
- Older callers can still synthesize events from claim history, but live semantic writes supply explicit claim events now.

Main code:

- `src/dory_core/claim_store.py`
- `src/dory_core/canonical_pages.py`
- `src/dory_core/compiled_wiki.py`
- Recall-promotion candidate tracking (used by dreaming)

## Token counting

Two implementations:

- `src/dory_core/token_counting.py` - tiktoken-based counting with per-agent encoding and heuristic fallback
- `src/dory_core/chunking.py` - uses `text.split()` (whitespace split), not tiktoken

Chunking doesn't use the tiktoken counter, so chunk boundaries are based on word count, not actual token count.

## Error handling

Domain exceptions in `src/dory_core/errors.py`:

- `DoryError` - base
- `DoryConfigError` - configuration issues
- `DoryValidationError` - input validation failures

HTTP mapping: all `DoryValidationError` instances return 400 regardless of the specific failure (path invalid, precondition failed, injection blocked, quota exceeded, frontmatter invalid). The API contract defines distinct error codes and HTTP status codes for each.

MCP mapping: tool-call exceptions are caught at the server boundary and returned as JSON-RPC errors. Validation-style failures map to invalid-params, everything else to internal server error.

## Current risks and drift

- Vector search is SQLite + brute-force cosine, not ANN.
- MCP has no auth. HTTP does. The Claude Code bridge forwards HTTP bearer auth; raw MCP over TCP doesn't.
- Recall mode searches all sessions, not just the current one.
- Critical write paths are atomic; some non-core scripts still use plain `write_text()`.
- `SubjectResolver` entries can go stale in long-running daemons.
- HTTP errors return `{"detail": "..."}` instead of the contract's `{"error": {"code": ...}}` shape.
- Chunking splits on whitespace for token counts, not tiktoken.
- Chunk `overlap_ratio` is accepted but not implemented.
