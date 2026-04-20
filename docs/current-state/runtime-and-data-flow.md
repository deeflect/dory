# Runtime and data flow

What the current code actually does at runtime.

## Runtime faces

All major read/write behavior is shared across:

- CLI — `src/dory_cli/main.py`
- HTTP — `src/dory_http/app.py`
- Native MCP bridge — `src/dory_mcp/server.py`
- Claude bridge — `scripts/claude-code/dory-mcp-http-bridge.py`
- Hermes provider — `plugins/hermes-dory/provider.py`
- OpenClaw plugin — `packages/openclaw-dory/src/index.ts`

The real center of gravity is `dory_core`.

## Storage model

Three distinct storage layers.

### 1. Durable markdown corpus

Source of truth.

- scanned by `MarkdownStore` in `src/dory_core/markdown_store.py`
- parsed via frontmatter helpers in `src/dory_core/frontmatter.py`
- chunked by `chunk_markdown()` in `src/dory_core/chunking.py`

### 2. Durable index sidecar

Lives under `index_root`.

- SQLite database at `index_root/dory.db`
  - entity registry tables
  - claims
  - claim events
  - files
  - chunks
  - FTS table
  - edge graph
  - embedding cache
  - chunk vectors
  - recall log
  - parity tables
- Vector records now live in SQLite table `chunk_vectors`
  - managed by `SqliteVectorStore`
  - brute-force O(n) cosine similarity
  - legacy `index_root/lance/chunks_vec.json` imports as fallback when SQLite vectors are empty

### 3. Session evidence plane

Separate from durable memory.

- SQLite database at `index_root/session_plane.db`
- used for session recall and ingest
- managed by `SessionEvidencePlane` in `src/dory_core/session_plane.py`

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

- `text`, `keyword`, `lexical` → `bm25`
- `semantic` → `vector`

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
  - returns a client-facing `rank_score` and `evidence_class`; raw `score` remains mode-specific diagnostic data
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

For hybrid search:

- Without an LLM retrieval planner, weak durable results can still trigger the deterministic fallback into session-plane results.
- With an LLM retrieval planner, session use can become part of the explicit search plan instead of relying only on the weak-result heuristic.
- When the planner is available, the final merged durable/session candidate set can also be reordered through strict-schema result selection before the response is returned.

Durable memory isn't the only retrieval source, but session recall now has an opt-in LLM-assisted path for deciding when to merge. The default path stays deterministic to keep interactive search fast.

## Active-memory flow

`ActiveMemoryEngine` in `src/dory_core/active_memory.py` is an optional staged retrieval helper.

Stages:

1. Explicit active-memory call enters the staged retrieval flow.
2. Request `profile` resolves to `general`, `coding`, `writing`, `privacy`, or `personal`; `auto` uses prompt classification as a compatibility fallback.
3. The source policy for that profile decides wake profile, whether session evidence is allowed, whether generated wiki helper context is allowed, and which path families are blocked.
4. Generated wiki shell is helper context only when the source policy allows it; helper files are not returned as citeable sources unless rendered as evidence.
5. Bounded wake build uses the profile's wake policy and is only rendered when no stronger durable/session evidence was found.
6. Optional retrieval planner turns prompt plus compact helper context into durable/session query sets.
5. Durable hybrid search with rerank enabled by default for this pass.
6. Session-plane recall search only when the source policy allows session context and the prompt asks for recency/session evidence.
7. Optional composer turns a tiny, sanitized evidence packet into a compact active-memory synthesis.
8. Final block renders synthesis plus bounded durable/session evidence sections under the request token budget and returns the resolved profile.

Behavior notes:

- Explicit `active-memory` endpoint/CLI/tool calls always run the staged retrieval flow; callers should set `profile` when they know the task class.
- Not a full autonomous sub-agent; it's a bounded retrieval helper.
- The first routing layer is the profile source policy plus canonical search priors, not broad unweighted search.
- `coding` excludes personal/voice paths by policy.
- `privacy` is boundary-only and disables session evidence, generated wiki helper context, raw inbox, people pages, and personal knowledge pages.
- `writing` loads voice context without full identity/profile context.
- Wiki pages are hidden helper context when allowed. Durable active-memory evidence excludes `wiki/` so generated cache pages do not outrank canonical files.
- Active-memory emits a compact `## Active memory` synthesis across current focus, helper hints, and selected durable/session hits before bounded evidence sections.
- When an active-memory LLM provider is configured, planning and composition are strict-schema LLM passes from `src/dory_core/retrieval_planner.py`. `DORY_ACTIVE_MEMORY_LLM_PROVIDER=openrouter` uses the maintenance OpenRouter model; `local` uses an OpenAI-compatible local/LAN endpoint from `DORY_LOCAL_LLM_*`; `auto` prefers local and falls back to OpenRouter. `DORY_ACTIVE_MEMORY_LLM_STAGES=compose` keeps deterministic retrieval but uses the LLM to compress selected evidence; `plan` uses the LLM only for query expansion; `both` does both when the request deadline has enough time.
- Active-memory is read-only. The LLM receives sanitized snippets and strict schemas only; it has no write path and cannot mutate the corpus or index.
- Sources are citeable rendered wake or retrieved evidence paths; hidden helper files are not returned as sources.

## Get flow

`get` is intentionally simple.

HTTP `GET /v1/get`:

- resolves a corpus-relative path
- prevents escape from the corpus root
- slices lines via `from` and `lines` query params
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

Steps:

1. Resolve the subject through `EntityRegistry`, with fallback subject matching.
2. Route the request to a canonical target path.
3. Create an immutable semantic evidence artifact under `sources/semantic/YYYY/MM/DD/*.md`.
4. Append/replace/forget through `WriteEngine`.
5. Mutate `ClaimStore` current claims and claim events using the semantic evidence artifact as provenance.
6. Rebuild canonical pages from active claims plus claim events.
7. On `forget`, republish the tombstone page from claim history plus claim events.

Behavior notes:

- Person/project/concept/decision/core pages are structured section documents.
- Semantic writes auto-maintain `Timeline` and `Evidence` from claim events, not just page-local append state.
- Resolved semantic writes always emit immutable evidence artifacts unless quarantined.
- Claim events persist `entity_id`, `event_type`, `reason`, `evidence_path`, `created_at`.
- Semantic `forget` leaves the original page superseded and writes the retired history/evidence view to the tombstone.
- Unresolved low-confidence subjects are rejected or quarantined.
- Registry-backed subject resolution is refreshed by semantic writes through `_sync_registry()`, so same-process writes can resolve newly established subjects.

## Migration flow

`MigrationEngine` in `src/dory_core/migration_engine.py` runs in four semantic stages when LLM support is enabled:

1. Per-document strict extraction
2. Evidence-first staging for unresolved docs
3. Corpus-level entity clustering across extracted candidates
4. Claim-store-backed canonical compilation plus optional generated-page audit/repair

Behavior:

- Every selected markdown file can emit a structured document artifact under `inbox/migration-documents/<run_id>/`.
- Migration stages `.json`, `.jsonl`, `.ndjson`, `.txt`, `.yaml`, `.yml`, `.toml`, and `.csv` legacy inputs alongside `.md` files, normalizing them to markdown before classification.
- Unresolved docs stay evidence-only or quarantine by default, but bounded no-LLM promotion now exists for a narrow subset of structured inputs:
  - transcript-shaped `.jsonl` / `.ndjson` session evidence can emit clear deterministic atoms and promote those bounded facts into canonical synthesis
  - typed JSON payloads with explicit supported families (`project`, `person`, `decision`, `concept`) can promote without an LLM when they provide a clear title plus summary
- Per-document artifacts record extraction/classification fallback reasons when LLM promotion degrades to evidence-only.
- Resolved docs participate in a corpus-level entity clustering pass before atoms are committed.
- Clustering can merge aliases like `person:primary-user` and `person:primary-user-alias` across source files before claims/pages are written.
- Migration preserves extracted source dates when writing claims, so canonical timelines reflect source `time_ref` instead of migration-run time.
- Migration applies a small claim-write policy instead of blindly appending every atom:
  - `project_update` → claim kind `state`
  - A newer `project_update` for the same project replaces the current active project state claim instead of leaving multiple active states side by side
  - `person_fact` / `concept_claim` → claim kind `fact`
  - `goal` / `open_question` / `followup` / `timeline_event` → claim kind `note`
- Core-doc migration uses file-specific section targets instead of a generic `Role` fallback:
  - `core/user.md` → `Summary`
  - `core/identity.md` → `Role`
  - `core/soul.md` → `Voice`
  - `core/env.md` → `Environment`
  - `core/active.md` → `Current Focus`
  - `core/defaults.md` → `Default Operating Assumptions`
- After compilation, migration can emit a separate audit artifact under `inbox/migration-runs/<run_id>.audit.json`.
- When pages are flagged, migration can also emit a repair artifact under `inbox/migration-runs/<run_id>.repair.json`, apply one bounded grounded repair pass, and re-audit before writing the final report/run artifact.
- Run-level fallback warnings persist in the main run artifact/report for entity-resolution, audit, and repair failures that previously degraded silently.
- Normalized non-Markdown evidence is written as markdown targets like `*.json.md`, `*.jsonl.md`, `*.ndjson.md`, so the durable corpus stays markdown-first even when the legacy source was structured data.
- Transcript-shaped JSON Lines inputs render extracted record summaries plus text-only transcript lines before the raw payload block, giving LLM-backed migration a cleaner evidence surface than raw event logs alone.
- Deterministic classification routes transcript-shaped `.jsonl` / `.ndjson` inputs into `logs/sessions/imported/*.md` with session frontmatter.
- In no-LLM mode, transcript-shaped session evidence can emit bounded deterministic atoms into the per-document migration artifact and, when clear enough, participate in bounded canonical synthesis.
- Typed JSON exports with explicit `kind`/`type` families (`project`, `person`, `decision`, `concept`) can emit bounded deterministic atoms and promote into canonical synthesis without an LLM, provided the payload declares a supported family and provides a clear title plus summary.
- Explicit structured-export adapters exist for known schema-tagged payloads: `dory.project_export.v1`, `dory.person_export.v1`, `dory.decision_export.v1`, `dory.concept_export.v1`.
- Unknown structured schemas stay evidence-only instead of being promoted through broader family guessing.

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

## Active memory flow (summary)

`ActiveMemoryEngine` in `src/dory_core/active_memory.py` is an optional staged pre-reply helper.

- Resolves an explicit profile (`coding`, `writing`, `privacy`, `personal`, `general`) or classifies `auto`.
- Applies profile source policy before rendering evidence; `coding` blocks personal/voice profile pages, and `privacy` is boundary-only.
- Builds a reduced wake block only when requested and only through the profile's wake policy.
- Runs durable hybrid search plus optional session recall when the prompt asks for recent/session context.
- Uses optional strict-schema LLM planning/composition when configured, but falls back to deterministic retrieval.
- Emits a synthesized block with compact durable/session evidence sections and citeable source paths.

Read-only bounded context supplement. The active-memory LLM path never writes memory.

## Research flow

`ResearchEngine` in `src/dory_core/research.py` — still bounded, but no longer a snippet dump.

- Runs hybrid search with rerank enabled.
- Requests compact snippets rather than whole chunk bodies.
- Builds a grounded artifact body with `Question`, `Answer`, `Evidence`, and optional `Session Evidence` sections.
- Dedupes source paths before returning the artifact.
- Optionally persists the artifact through `ArtifactWriter`.

Artifact targets:

- reports → `references/reports/`
- briefings → `references/briefings/`
- wiki notes → `wiki/concepts/`
- proposals → `inbox/proposed/`

## OpenClaw parity flow

`src/dory_core/openclaw_parity.py` adds two parity surfaces:

- recall event tracking
- public artifact listing

Current code supports:

- `POST /v1/recall-event`
- `GET /v1/public-artifacts`
- parity diagnostics inside status

## Claim-driven publishing

`ClaimStore` and the canonical/wiki renderers split current state from history:

- Active/current sections come from active claims.
- `Timeline` comes from ordered claim events.
- `Evidence` comes from deduped claim-event `evidence_path`s.
- Older callers can still fall back to synthesized events from claim history, but live semantic writes now supply explicit claim events.

Main code:

- `src/dory_core/claim_store.py`
- `src/dory_core/canonical_pages.py`
- `src/dory_core/compiled_wiki.py`
- recall-promotion candidate tracking used by dreaming

## Token counting

Two implementations:

- `src/dory_core/token_counting.py` — tiktoken-based counting with per-agent encoding and heuristic fallback
- `src/dory_core/chunking.py` — uses `text.split()` (whitespace split), not tiktoken

Chunking doesn't use the tiktoken counter, so chunk boundaries are based on word count, not actual token count.

## Error handling

Domain exceptions in `src/dory_core/errors.py`:

- `DoryError` — base
- `DoryConfigError` — configuration issues
- `DoryValidationError` — input validation failures

HTTP mapping: all `DoryValidationError` instances return 400 regardless of the specific failure (path invalid, precondition failed, injection blocked, quota exceeded, frontmatter invalid). The API contract defines distinct error codes and HTTP status codes for each.

MCP mapping: tool-call exceptions are caught at the server boundary and returned as JSON-RPC errors. Validation-style failures map to invalid-params, everything else to internal server error.

## Current risks and drift

- Vector layer is SQLite-backed brute-force search, not ANN indexing.
- MCP has no authentication; HTTP does.
- Claude Code bridge forwards HTTP bearer auth, but native MCP has no auth handshake.
- Recall mode searches all sessions, not just the current session.
- Main write paths are atomic, but non-core scripts still contain non-atomic file writes.
- `SubjectResolver` entries become stale in long-running daemons.
- HTTP error responses don't match the API contract format.
- Chunking uses whitespace-split token counting, not tiktoken.
- Chunk overlap parameter is accepted but not implemented.
