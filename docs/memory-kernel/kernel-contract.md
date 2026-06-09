---
title: Dory Kernel Contract
status: active
type: reference
created: 2026-05-24
scope: public-safe
---

# Dory Kernel Contract

This document inventories Dory's current internal contracts — types, APIs,
source-of-truth boundaries, database layout, plane completeness, and
surface compatibility rules — before any memory-kernel behavior changes.

It is the **source-of-record reference** for Slice 1 of the memory-kernel plan.
Update this doc when internal contracts change, not when surface features are
added.

---

## 1. Stable Request/Response Types

Defined in `src/dory_core/types.py`. Every surface (CLI, HTTP, MCP, Hermes,
OpenClaw) serializes to/from these Pydantic models.

### Wake

| Model | Key Fields | Notes |
|---|---|---|
| `WakeReq` | `budget_tokens` (≤1500), `agent`, `profile`, `project`, `cwd`, `include_recent_sessions`, `include_pinned_decisions`, `debug` | |
| `WakeResp` | `profile`, `tokens_estimated`, `block` (markdown), `sources`, `frozen_at` | Non-debug output strips `tokens_estimated`, `sources`, `frozen_at` |

### Search

| Model | Key Fields | Notes |
|---|---|---|
| `SearchMode` | `Literal["bm25", "text", "keyword", "lexical", "vector", "semantic", "hybrid", "recall", "exact"]` | `text`/`keyword`/`lexical` normalize to `bm25`; `semantic` normalizes to `vector` |
| `SearchCorpus` | `Literal["durable", "sessions", "all"]` | |
| `SearchScope` | `path_glob`, `type`, `status`, `tags`, `agent`, `device`, `session_id`, `session_key`, `since`, `until` | |
| `SearchReq` | `query`, `scope`, `k` (1-50), `mode`, `corpus`, `min_relevance_score` (0.0-1.0), `include_content`, `rerank` ("auto"/"true"/"false"), `debug` | |
| `SearchResult` | `path`, `lines`, `score`, `score_normalized`, `rank_score`, `evidence_class` (canonical/generated/inbox/session/raw/archive/other), `snippet`, `frontmatter`, `stale_warning`, `confidence` | `score`, `score_normalized`, `rank_score`, `frontmatter` stripped in non-debug output |
| `SearchResp` | `query`, `count`, `results`, `took_ms`, `warnings` | |

### Active Memory

| Model | Key Fields | Notes |
|---|---|---|
| `ActiveMemoryReq` | `prompt`, `agent`, `cwd`, `project`, `scope`, `profile`, `timeout_ms` (100-30000), `budget_tokens` (100-1200), `include_wake`, `rerank`, `debug`, `partial_ok` | |
| `ActiveMemoryResp` | `kind` ("none"/"memory"), `block`, `summary`, `took_ms`, `profile`, `confidence`, `sources`, `partial`, `warnings` | `took_ms`, `profile`, `confidence` stripped in non-debug |

### Semantic Write

| Model | Key Fields | Notes |
|---|---|---|
| `WriteKind` | `Literal["append", "create", "replace", "forget"]` | |
| `WriteReq` | `kind`, `target`, `content`, `soft`, `dry_run`, `frontmatter`, `agent`, `session_id`, `expected_hash`, `reason` | |
| `WriteResp` | `path`, `action`, `bytes_written`, `hash`, `indexed`, `edges_added` | |
| `MemoryWriteAction` | `Literal["write", "replace", "forget"]` | `add`/`create` normalize to `"write"`; `remove`/`delete` normalize to `"forget"` |
| `MemoryWriteKind` | `Literal["fact", "preference", "state", "decision", "note"]` | |
| `MemoryWriteReq` | `action`, `kind`, `subject`, `content`, `scope`, `confidence`, `reason`, `source`, `soft`, `dry_run`, `force_inbox`, `allow_canonical`, `agent`, `session_id`, `origin_surface` | |
| `MemoryWriteResp` | `resolved`, `action`, `kind`, `subject_ref`, `target_path`, `result` (preview/written/replaced/forgotten/quarantined/rejected), `confidence`, `indexed`, `quarantined`, `message`, `evidence_path`, `matched_by`, `preview` | |

### Proposals

| Model | Notes |
|---|---|
| `MemoryProposalCreateReq` | Mirrors `MemoryWriteReq` plus `source_paths`, `proposal_id` |
| `MemoryProposalListReq` | `status`: pending / applied / rejected |
| `MemoryProposalGetReq` | `proposal_id` + `status` |
| `MemoryProposalApplyReq` | `proposal_id` + `agent`, `session_id`, `origin_surface` |
| `MemoryProposalRejectReq` | `proposal_id` + `reason`, `agent`, `session_id`, `origin_surface` |

### Sessions

| Model | Key Fields |
|---|---|
| `SessionIngestReq` | `path`, `content`, `agent`, `device`, `session_id`, `status` (active/interrupted/done), `captured_from`, `updated` |
| `SessionIngestResp` | `stored`, `path`, `reindexed` |

### Recall / Links / Artifacts / Research

| Model | Notes |
|---|---|
| `RecallEventReq` | `agent`, `session_key`, `query`, `result_paths`, `selected_path`, `corpus`, `source` |
| `RecallEventResp` | `stored`, `selected_path`, `created_at` |
| `LinkReq` | `op`: neighbors / backlinks / lint; `path`, `direction`, `depth` (≤5), `max_edges` (≤500) |
| `ArtifactReq` | `kind` (report/briefing/wiki-note/proposal), `title`, `question`, `body`, `sources`, `target`, `status` (draft/final) |
| `ArtifactResp` | `path`, `kind`, `bytes_written` |
| `ResearchReq` | `question`, `kind`, `corpus`, `limit` (1-20), `save` |
| `ResearchResp` | `artifact` (ArtifactReq), `sources` |
| `PurgeReq` / `PurgeResp` | Target path removal with dry-run safety; `allow_canonical` gating |
| `MigrateReq` / `MigrateResp` | Legacy migration from `legacy_root` |
| `OpenClawParityDiagnostics` | Flush/recall/artifact enablement status |

### Serialization Helpers

```python
serialize_search_response(resp, debug=False)    # strips internal score fields
serialize_wake_response(resp, debug=False)       # strips tokens/sources/frozen
serialize_active_memory_response(resp, debug=False)  # strips took_ms/profile/confidence
```

---

## 2. Source-of-Truth Boundaries

Dory's canonical human-readable memory is **markdown files on disk**. SQLite sidecars are indexes, caches, ledgers, or evidence planes that support runtime behavior; they must not become a second authoring surface for canonical prose.

| Data | Authoritative Source / Ledger | Sidecar / Index | Rebuild / Recovery Model |
|---|---|---|---|
| Canonical memory | Markdown files in corpus root | Chunks + vectors in SQLite (`.index/dory.db`) | Reindex from markdown |
| Entity registry | Registry rows derived from canonical pages, migration, and semantic write routing | SQLite (`<corpus_root>/.dory/entity-registry.db`) | Rebuildable when source pages/routing metadata exist; otherwise treated as a ledger to preserve |
| Claims + claim events | Claim/event ledger written through semantic write pipeline, with evidence paths back to markdown artifacts | SQLite (`<corpus_root>/.dory/claim-store.db`) | Ledger is authoritative for claim lifecycle; generated wiki/canonical renderings can be refreshed from it |
| Session evidence | Raw session markdown files (for ingested sessions) plus ingestion metadata | SQLite with FTS5 (`.index/session_plane.db`) | Reingest session files when available |
| Generated wiki | Derived markdown under `wiki/` | Search index entries | Refresh from claim store + canonical pages |
| Search indexes | Markdown files + chunk content | SQLite FTS5 + vector cache | Reindex from markdown |
| Link graph | Extracted from markdown `[[wiki-links]]` | SQLite `edges` table | Reindex from markdown |
| Recall ledger | Retrieval events | SQLite `recall_log` + `openclaw_recall_events` / `openclaw_recall_promotions` | Append-only operational ledger |
| Auth tokens | File on disk (`auth-tokens.json`) | — | Not memory data |
| Corpus structure | Filesystem layout | — | Direct source |

**Rule:** Indexes and generated views must be rebuildable from their sources.
Ledgers such as claim events and recall events are append-only evidence records;
do not replace them with unsupported synthesized truth.

---

## 3. Current Callable Internal APIs

These are the public-internal classes that surfaces and tests call directly.

### 3.1 `WakeBuilder` (`src/dory_core/wake.py`)

```python
class WakeBuilder:
    def __init__(self, root: Path, *, token_counter: TokenCounter | None = None) -> None
    def build(self, req: WakeReq) -> WakeResp
```

- Loads the compiled project card (L0), raw project state (L1), profile
  sections (L2), pinned decisions, general compiled cards as trailing budget
  filler (L3), and recent session summaries.
- Uses `ProfileRegistry` for section order and budgets.
- Uses `TokenCounter` for budget fitting.
- Delegates compiled card collection to `compiled_wiki.collect_project_card`
  and `compiled_wiki.collect_general_cards`; cards are compacted with
  `compiled_wiki.wake_card_excerpt` before assembly.
- Computes staleness at read time via `compiled_wiki.wake_staleness_note`:
  core sections untouched past 21 days and project cards/state past 30 days
  carry an inline `[stale]` marker derived from frontmatter `updated` (stored
  freshness labels are never trusted).
- The admin profile appends a synthetic `maintenance` section built by
  `maintenance.wake_maintenance_summary` (stale core pages, stale active
  compiled cards, untriaged inbox queues) so maintenance findings reach an
  agent surface instead of rotting under `inbox/`.

**Internal types:**
- `HotBlockSection`: `(path: Path, content: str)` — assembled inside `build()`.

### 3.2 `ActiveMemoryEngine` (`src/dory_core/active_memory.py`)

```python
@dataclass(slots=True)
class ActiveMemoryEngine:
    wake_builder: _WakeBuilder       # protocol: build(req) -> WakeResp
    search_engine: _SearchEngine      # protocol: search(req) -> SearchResp
    root: Path | None = None
    planner: ActiveMemoryPlanner | None = None
    composer: ActiveMemoryComposer | None = None
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    def build(self, req: ActiveMemoryReq) -> ActiveMemoryResp
```

- Orchestrates: source policy → profile/retrieval config → optional LLM planning
  → search candidates → optional LLM composition → final block rendering.
- Uses `ProfileRegistry` to resolve retrieval profile (allow/deny/boosts/sessions).
- Falls back to deterministic `_build_deterministic_block` when LLM planner/composer
  are unavailable or time out.
- Reports `partial: True` when any stage is skipped or incomplete.
- Mutually owned with `WakeBuilder` and `SearchEngine` via protocols.

**Internal types:**
- `BudgetConfig`: `planner_min_remaining_ms`, `composer_min_remaining_ms`,
  `composer_timeout_headroom_ms`, `rerank_timeout_headroom_ms`, `partial_ok`.
- `SourcePolicy`: `profile: _ActiveMemoryProfile`, `retrieval: RetrievalProfileConfig`,
  `include_session_context: bool`.

### 3.3 `SearchEngine` (`src/dory_core/search/engine.py`)

```python
class SearchEngine:
    def __init__(self, index_root: Path, embedder: ContentEmbedder,
                 *, rerank_phase: str = "v1",
                 query_expander: QueryExpander | None = None,
                 retrieval_planner: SearchQueryPlanner | None = None,
                 result_selector: SearchResultSelector | None = None,
                 reranker: LLMReranker | None = None,
                 rerank_candidate_limit: int = 40)
    def search(self, req: SearchReq) -> SearchResp
```

- Core retrieval pipeline: query preprocessing → FTS / vector / hybrid → session
  merge → reranking → finalization.
- Owns the SQLite index connection and vector store.
- Delegates session search to `SessionEvidencePlane`.
- Relies on optional `SearchQueryPlanner`, `QueryExpander`, `LLMReranker`.
- Evidence classification: canonical / generated / inbox / session / raw / archive / other.
- Supports reorder via `result_selector` (currently `retrieval_planner`).

**Internal submodules (in `src/dory_core/search/`):**
- `engine.py` — main `SearchEngine` class
- `fts.py` — FTS query building, query profiling, snippet extraction
- `scoring.py` — BM25/vector/hybrid fusion, normalization, confidence
- `session.py` — session-scoped query helpers
- `dedup.py` — duplicate collapse, low-trust / retired filtering
- `policies.py` — session-evidence policy, live result detection
- `types.py` — `_ChunkRow`, `QueryProfile`
- `utils.py` — stale warnings, cosine similarity, document date extraction

### 3.4 `SemanticWriteEngine` (`src/dory_core/semantic_write.py`)

```python
class SemanticWriteEngine:
    def __init__(self, root: Path, *, index_root: Path | None = None,
                 embedder: ContentEmbedder | None = None,
                 resolver_client: OpenRouterClient | None = None)
    def write(self, req: MemoryWriteReq) -> MemoryWriteResp
```

- Full pipeline: subject resolution → target determination → canonical page
  routing → evidence artifact creation → claim recording → canonical/tombstone
  markdown publishing → inbox/quarantine gating → reindex.
- `write()` handles live writes and dry-run previews (`req.dry_run=True`).
- Uses `WriteEngine` for raw file operations.
- Uses `ClaimStore` for claim/event recording.
- Uses `EntityRegistry` for subject matching.
- Uses `SubjectResolver` (deterministic) or `RegistryBackedSubjectResolver`
  (registry + fallback + optional LLM).

**Internal types:**
- `SemanticWritePlan`: `(action, kind, subject, subject_ref, target_subject_ref,
  family, title, target_path, resolved_mode, content, scope, confidence, soft,
  match_confidence, reason, source, agent, session_id, origin_surface, matched_by,
  target_exists)`
- `_SemanticEvidenceArtifact`: evidence path + frontmatter + content
- `ResolvedMode`: `Literal["append", "replace", "forget"]`

### 3.5 `EntityRegistry` (`src/dory_core/entity_registry.py`)

```python
class EntityRegistry:
    def __init__(self, db_path: Path) -> None
    def upsert(self, *, entity_id, family, title, target_path, aliases=()) -> None
    def resolve(self, subject: str, *, family: str | None = None) -> RegistryMatch | None
    def get(self, entity_id: str) -> EntityRecord | None
    def merge(self, winner_id: str, loser_id: str) -> None
    def list_family(self, family: str) -> tuple[EntityRecord, ...]
```

- SQLite-backed with `entities` + `entity_aliases` tables.
- Aliases indexed for fast `normalized_value` lookups.
- Match sources: `entity_id` (0), `title` (1), `alias` (2) — ordered by priority.
- `RegistryMatch` returns: `entity_id`, `family`, `title`, `target_path`,
  `matched_by`, `confidence` (always "high" for deterministic resolution).

### 3.6 `ClaimStore` (`src/dory_core/claim_store.py`)

```python
class ClaimStore:
    def __init__(self, db_path: Path) -> None
    def add_claim(self, *, entity_id, kind, statement, evidence_path,
                  confidence="high", occurred_at=None) -> str
    def invalidate_claim(self, claim_id, *, reason, evidence_path=None) -> None
    def replace_current_claim(self, *, entity_id, kind, statement, evidence_path,
                              confidence="high", reason=None, occurred_at=None) -> str
    def retire_entity_claims(self, *, entity_id, reason, kind=None, evidence_path=None) -> None
    def current_claims(self, entity_id, *, kind=None) -> tuple[ClaimRecord, ...]
    def claim_history(self, entity_id) -> tuple[ClaimRecord, ...]
    def claim_events(self, entity_id) -> tuple[ClaimEvent, ...]
    def recent_active_claims(self, *, limit=20) -> tuple[ClaimRecord, ...]
    def recent_event_details(self, *, limit=20) -> tuple[ClaimEventDetail, ...]
```

- SQLite-backed with `claims` + `claim_events` tables.
- Claims have status lifecycle: `added` → active, then `replaced`, `retired`,
  or `invalidated`.
- Each state transition is recorded as a `ClaimEvent`.
- `ClaimRecord`: `(claim_id, entity_id, kind, statement, status, valid_from,
  valid_to, confidence, evidence_path, created_at, updated_at)`
- `ClaimEvent`: `(event_id, claim_id, entity_id, event_type, reason,
  evidence_path, created_at)`

### 3.7 `SessionEvidencePlane` (`src/dory_core/session_plane.py`)

```python
@dataclass(frozen=True, slots=True)
class SessionEvidencePlane:
    db_path: Path

    def upsert_session_chunk(self, *, path, content, updated, agent, device,
                             session_id, status) -> None
    def delete_paths(self, paths) -> int
    def load_paths(self) -> set[str]
    def count_docs(self) -> int
    def search(self, query: SessionSearchQuery) -> SessionSearchResponse
```

- SQLite-backed with `session_docs` (TEXT content) + `session_docs_fts` (FTS5).
- FTS triggers keep the virtual table in sync.
- `SessionSearchQuery`: `(query, limit, agents, devices, session_ids,
  statuses, since, until)`
- `SessionSearchResult`: `(path, snippet, updated, agent, device, session_id,
  status, score)`
- Scoring: coverage × 0.8 + phrase_bonus + lexical + recency_bonus.

### 3.8 `DoryRuntime` (`src/dory_core/runtime.py`)

```python
@dataclass(frozen=True, slots=True)
class DoryRuntime:
    corpus_root: Path
    index_root: Path
    embedder: ContentEmbedder
    query_expander: OpenRouterQueryExpander | None
    retrieval_planner: OpenRouterRetrievalPlanner | None
    reranker: Any
    rerank_candidate_limit: int
    search_engine: SearchEngine
    active_memory_engine: ActiveMemoryEngine
    semantic_write_engine: SemanticWriteEngine

def build_dory_runtime(*, corpus_root, index_root, settings=None,
                       embedder=None, query_expander=None,
                       retrieval_planner=None, reranker=None,
                       rerank_candidate_limit=None) -> DoryRuntime
```

- Consolidates all engine construction into one factory.
- Backward-compatible aliases: `SurfaceRuntime = DoryRuntime`,
  `build_surface_runtime = build_dory_runtime`.
- Used directly by MCP server; adopted by HTTP server; available
  to CLI via `_internals.py` helper functions.

### 3.9 Supporting Engines & Helpers

| Module | Key Export | Role |
|---|---|---|
| `dory_core/write.py` | `WriteEngine` | Raw markdown file write/create/replace/forget with injection checks |
| `dory_core/compiled_wiki.py` | `render_compiled_page()` | Generates compiled wiki cards from claims |
| `dory_core/wiki_indexes.py` | `WikiIndexBuilder` | Refreshes wiki index pages (people/projects/concepts/decisions/indexes) |
| `dory_core/profiles.py` | `ProfileRegistry` | Wake + retrieval profile config loading |
| `dory_core/project_context.py` | `resolve_project_handle()`, `resolve_project_path()` | Deterministic project resolution |
| `dory_core/subject_resolver.py` | `SubjectResolver`, `RegistryBackedSubjectResolver` | Canonical subject → entity resolution |
| `dory_core/canonical_pages.py` | `render_canonical_from_claims()`, `patch_canonical_markdown()` | Canonical page rendering helpers |
| `dory_core/index/reindex.py` | `reindex_paths()` | Full or partial search index rebuild |
| `dory_core/index/sqlite_store.py` | — | SQLite connection helpers |
| `dory_core/index/sqlite_vector_store.py` | — | Vector store management |
| `dory_core/llm/json_client.py` | `JSONClient` | Generic JSON-mode LLM client |
| `dory_core/llm/openrouter.py` | `OpenRouterClient` | OpenRouter API client |
| `dory_core/llm/openai_compatible.py` | — | OpenAI-compatible API client |
| `dory_core/embedding.py` | `ContentEmbedder`, `QueryEmbedder` | Embedding generation |
| `dory_core/llm_rerank.py` | `LLMReranker`, `build_reranker()` | Reranking |
| `dory_core/link.py` | `load_known_entities()`, `sync_document_edges()` | Wiki-link graph management |
| `dory_core/session_ingest.py` | — | Session file ingestion |
| `dory_core/session_shipper.py` | — | Session shipping |
| `dory_core/session_sync.py` | — | Session sync (external sources) |
| `dory_core/retrieval_planner.py` | `SearchQueryPlanner`, `ActiveMemoryPlanner`/`Composer` | LLM search/active-memory planning |
| `dory_core/query_expansion.py` | `QueryExpander` | LLM query expansion |
| `dory_core/rerank_orchestrator.py` | `RerankOrchestrator` | Rerank toggle + orchestration |
| `dory_core/digest_mining.py` / `digest_writer.py` / `digests.py` | — | Daily/weekly digest pipeline |
| `dory_core/dreaming/proposals.py` | `ProposalStore`, `create_semantic_write_proposal()`, `apply_proposal()`, `reject_proposal()` | Reviewable proposal JSON files under `inbox/proposed`, `inbox/applied`, and `inbox/rejected` |
| `dory_core/maintenance.py` | — | Maintenance report generation |
| `dory_core/migration_*.py` | — | Legacy migration pipeline (source router, entity discovery, synthesis, etc.) |
| `dory_core/compiler.py` | `CompilerPipeline`, `CompilerArtifact`, `context_fence_for_ingest()`, `list_compiler_pipelines()` | Compiler plane contract types, pipeline registry, and context fencing for compiler artifact re-ingestion prevention |

---

## 4. Existing vs. Missing Matrix (Four Planes)

| Plane | Exists Today | Missing / Weak | Next Step |
|---|---|---|---|
| **1. Hot Context** | `WakeBuilder.build()`, `WakeBuilder.build_packet()`, `ActiveMemoryEngine.build()`, shared `HotContextPacket`, profiles (default/casual/assistant/coding/writing/privacy/admin), project resolution, entity-context pre-flight, compiled cards (L0), recent sessions, pinned decisions. Active memory responds with block/summary/sources/warnings. | Active memory still owns many concerns (policy/retrieval/composition/rendering). Wake section filtering does not yet expose full reason-included diagnostics for every withheld section. | Search-engine cleanup backlog + wake diagnostics |
| **2. Entity Memory** | `EntityRegistry` (SQLite), `SubjectResolver` (deterministic + LLM), semantic write routing, compiled wiki cards, `ClaimStore` for active durable truth, deterministic `EntityContext`, `ObservationStore` / `ObservationRetrieval` sidecar over active claims. | Relationships are not clearly separated from links/aliases. Entity lookup is per-call only; no batch entity context. Observation index is deterministic claim-derived V1, not richer inferred pattern mining. | Batch entity context and richer reviewed observations |
| **3. Evidence Retrieval** | Exact `get` (file read), BM25/vector/hybrid search, session FTS5 search, claim/event/evidence lookup, observation lookup, link neighbors, retrieval planner (optional LLM), typed retrieval attempts, reranker (optional LLM), session fallback. | Search engine owns too many stages (query → FTS → vector → session → rerank → finalize) in one class. Typed retrieval is available through `RetrievalFacade.execute_typed_plan()` but normal search still preserves existing `SearchEngine` behavior. | Search-engine cleanup backlog |
| **4. Compiler** | Dream (extraction/recall/proposals/events), daily/weekly digests, wiki refresh (`WikiIndexBuilder`), compiled wiki card refresh, maintenance reports, migration audit/repair, session ingest + recall promotion. All inventoried in `compiler.py` with typed pipeline descriptors (`CompilerPipeline`), context fencing (`context_fence_for_ingest`), and a unified contract document (`docs/memory-kernel/compiler-plane.md`). | Pipeline metadata is static; no shared execution loop yet. Proposal promotion still requires CLI commands (no approval queue daemon). | Post-kernel consolidation: shared compiler worker if justified; approval queue daemon. |

### Additional Observations

- `DoryRuntime` exists and is used by MCP, CLI active-memory/search helper paths, and as the base for HTTP's `HttpRuntime`; HTTP and MCP still instantiate `WakeBuilder` directly for `/wake` / `dory_wake`.
- `ActiveMemoryEngine` accepts a `WakeBuilder` protocol, bypassing `DoryRuntime`.
- `SemanticWriteEngine` is fully constructed inside `build_dory_runtime()`.
- The `search/` package has been split from a monolithic `search.py` but `engine.py` is still large (~800 lines).

---

## 5. Current DB / Table Ownership

SQLite sidecars are split between `index_root` (search/session/retrieval indexes)
and `<corpus_root>/.dory/` (semantic-write ledgers). Reviewable proposals are
JSON files under the corpus inbox, not SQLite rows.

| Store | Owned By | Tables / Files | Purpose |
|---|---|---|---|
| `index_root/dory.db` | `index/migrations.py` (schema) | `files`, `chunks`, `chunks_fts`, `edges`, `embedding_cache_meta`, `chunk_vectors`, `recall_log`, `openclaw_recall_events`, `openclaw_recall_promotions`, `meta` | Search index (files, chunks, FTS, vectors, edges), recall ledger, OpenClaw parity tables |
| `<corpus_root>/.dory/claim-store.db` | `ClaimStore` | `claims`, `claim_events` (with indexes on entity_id + status, claim_id, event_id) | Durable claim lifecycle evidence (claims + event history) |
| `<corpus_root>/.dory/observation-store.db` | `ObservationStore` | `observations`, `observation_evidence` (with indexes on status/freshness/evidence) | Rebuildable derived observations over active claims and evidence |
| `<corpus_root>/.dory/entity-registry.db` | `EntityRegistry` | `entities`, `entity_aliases` (with index on normalized_value) | Entity lookup and alias resolution |
| `index_root/session_plane.db` | `SessionEvidencePlane` | `session_docs`, `session_docs_fts` (with sync triggers) | Session evidence store with FTS5 |
| `<corpus_root>/inbox/{proposed,applied,rejected}/` | `ProposalStore` | Proposal JSON documents | Reviewable memory write proposal queue |

**Ownership rules:**
- Each SQLite file has one owning class or schema owner.
- No cross-DB joins; relationships are resolved at the Python layer.
- The schema version is tracked in `dory.db.meta(key='schema_version')`.
- `reindex_paths()` in `index/reindex.py` rebuilds `files`, `chunks`, `chunks_fts`,
  `edges`, and `chunk_vectors` from markdown source.
- FTS rebuild is triggered on init if doc count doesn't match FTS row count (session_plane) or via explicit reindex (dory.db).

---

## 6. Compatibility Rules

### 6.1 CLI (`src/dory_cli/main.py`)

- Builds shared engines via `_internals.py` helpers; `_build_dory_runtime()`
  backs active-memory/search helper paths while direct commands still construct
  narrow engines where useful (`WakeBuilder` for `wake`, `SemanticWriteEngine`
  helper for semantic writes, etc.).
- Commands: `wake`, `active-memory`, `search`, `memory-write`, `write`,
  `research`, `digest`, `purge`, `link`, `status`, `migrate`, `reindex`,
  `ops` (dream/digest/wiki/maintenance/health).
- **Rule:** CLI can construct engines directly for now. When `DoryRuntime` is
  complete, CLI should adopt it for consistency.

### 6.2 HTTP (`src/dory_http/app.py`, `HttpRuntime`)

- Uses `HttpRuntime` (separate dataclass from `DoryRuntime`, built in
  `build_app()`).
- `build_app()` first creates a `DoryRuntime` and copies its engines into
  `HttpRuntime`; `HttpRuntime` adds HTTP-only auth configuration.
- `HttpRuntime` exposes `SearchEngine`, `ActiveMemoryEngine`, and
  `SemanticWriteEngine` plus HTTP-only auth configuration; per-route helpers
  construct `ClaimStore`, `EntityRegistry`, `SessionEvidencePlane`, `WriteEngine`,
  and `WikiIndexBuilder` from the same roots when needed.
- Routes: `GET /wake`, `POST /search`, `POST /active-memory`, `POST /memory-write`,
  `POST /write`, `POST /proposals/*`, `GET /get`, `POST /session-ingest`,
  `POST /link`, `POST /purge`, `POST /research`, `POST /digest`,
  `POST /reindex`, `POST /migrate`, `GET /status`, `POST /recall-event`,
  `POST /artifact`, `POST /ops/*`, `GET /wiki/*`.
- **Rule:** HTTP API is the primary external contract. Never make breaking
  changes to HTTP response shapes without a migration plan and version bump.
  `HttpRuntime` may eventually merge with `DoryRuntime`.

### 6.3 MCP (`src/dory_mcp/server.py`)

- Uses `DoryRuntime` directly (via `build_dory_runtime()`) for search,
  active-memory, semantic writes, proposals, and research; wake currently
  constructs `WakeBuilder` directly from `corpus_root`.
- Tools: `dory_wake`, `dory_search`, `dory_active_memory`, `dory_memory_write`,
  `dory_memory_propose`, `dory_memory_proposals`, `dory_memory_proposal_get`,
  `dory_memory_proposal_apply`, `dory_memory_proposal_reject`, `dory_write`,
  `dory_research`.
- `McpServer` protocol defines method signatures; `DoryMcpServer` implements
  them by calling `DoryRuntime` engines.
- **Rule:** MCP must stay compatible with the DoryRuntime shape. Tool input
  schemas are generated from Pydantic models (`WakeReq`, `SearchReq`, etc.).

### 6.4 Hermes (`plugins/hermes-dory/`)

- `HermesDoryProvider` extends `BaseMemoryProvider`.
- Calls Dory through HTTP client (`DoryClient`) or direct engine construction.
- Tool handlers: `_handle_wake_tool`, `_handle_active_memory_tool`,
  `_handle_search_tool`, `_handle_memory_write_tool`,
  `_handle_memory_propose_tool`, `_handle_memory_proposals_tool`,
  `_handle_memory_proposal_get_tool`, `_handle_memory_proposal_apply_tool`,
  `_handle_memory_proposal_reject_tool`, `_handle_write_tool`,
  `_handle_research_tool`.
- Hermes-specific wrapping: `build_memory_section()`, `store_memory()`,
  `sync_memories()`, `on_memory_write()`.
- Config: `wake_profile` (default: "coding"), `search_mode`, `memory_mode`.
- **Rule:** Hermes provider should remain a thin adapter. Avoid adding
  memory-kernel logic inside the plugin. Prefer HTTP client mode over direct
  engine imports.

### 6.5 OpenClaw (`packages/openclaw-dory/`)

- TypeScript plugin (compiled JS) that talks to Dory HTTP server.
- Plugin ID: `dory-memory`, kind: `memory`.
- Config: `baseUrl` (required), `token`, `tokenEnv`, `timeoutMs`.
- Uses Dory HTTP endpoints: wake, search, memory_write, recall events,
  artifact listing, flush.
- OpenClaw parity features tracked in `OpenClawParityDiagnostics`.
- **Rule:** OpenClaw communicates exclusively through HTTP. Any new endpoint
  needed by OpenClaw must also be available through HTTP. The TypeScript build
  (`npm run build`) produces `dist/index.js` which is included in the sdist.

### 6.6 Cross-Cutting Compatibility Rules

1. **No breaking type changes without review.** All Pydantic models in
   `types.py` are shared by every surface. Adding fields is safe; renaming
   or removing fields requires coordinated updates across all surfaces.

2. **Serialization is surface-specific.** Debug fields are stripped by
   `serialize_*` helpers for non-debug responses. CLI and MCP have their own
   formatting; HTTP returns JSON.

3. **Engine construction is converging on `DoryRuntime`.** MCP uses it for most tools.
   HTTP uses it as the base for `HttpRuntime`. CLI helper paths use it for
   active-memory/search while some commands still instantiate narrow engines
   directly. The target is one `build_dory_runtime()` factory for all surfaces.

4. **Wake construction is not yet unified.** `WakeBuilder` is instantiated
   directly in HTTP `/v1/wake`, MCP `dory_wake`, and CLI `wake`. Active-memory
   receives a `WakeBuilder` through `DoryRuntime`, but the runtime does not yet
   expose a reusable wake-builder field.

5. **Profiles are the gatekeeper.** `WakeProfile` and `ActiveMemoryProfile`
   determine what enters hot context. Unknown profiles must fail closed.
   Profiles are defined in `profiles.py` and configured via `<corpus_root>/profiles.yaml`.

6. **Index DB is a cache; ledger DBs are evidence.** Search chunks, FTS rows,
   vector caches, edges, generated wiki pages, and session FTS indexes must be
   rebuildable from their source artifacts. Claim/event and recall ledgers are
   evidence records with source refs, not unsupported synthesized truth.

7. **Synthetic-only in public paths.** Public docs, tests, fixtures, and evals
   must use synthetic examples. The `check-public-safety.py` script validates
   this for obvious secrets and private paths.

8. **Semantic writes are guarded.** Every `MemoryWriteReq` goes through
   dry-run/preview → inbox/quarantine → canonical-promotion gating.
   `force_inbox=True` and `allow_canonical=True` are explicit opt-ins.

---

## 7. Planes Summary

```text
Hot Context Plane        Entity Memory Plane      Evidence Retrieval Plane    Compiler Plane
├── WakeBuilder          ├── EntityRegistry       ├── SearchEngine             ├── Dream pipeline
├── ActiveMemoryEngine   ├── ClaimStore           ├── SessionEvidencePlane     ├── Daily/weekly digests
├── ProfileRegistry      ├── SubjectResolver      ├── Link graph               ├── WikiIndexBuilder
├── CompiledWiki          │  (deterministic+LLM)  ├── Exact file get           ├── Compiled card refresh
├── ProjectContext        ├── CanonicalPages      ├── FTS5 (BM25)              ├── Maintenance reports
├── PinnedDecisions       ├── SemanticWriteEngine ├── Vector search            ├── Migration audit/repair
└── Session summaries     └── Observation index   ├── Hybrid search            ├── Session recall promotion
                               (Slice 6: MISSING) ├── Query planner  (opt LLM) └── Write proposals
                                                   ├── Query expansion (opt LLM)
                                                   └── Reranker       (opt LLM)
```
