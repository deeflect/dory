---
title: Dory Memory Kernel Final Plan
status: active
type: implementation-plan
created: 2026-05-24
scope: public-safe
---

# Dory Memory Kernel Final Plan

This is the execution plan for making Dory a cleaner personal memory layer:
modular, source-backed, local-first, easier to maintain, and easier to extend
without turning it into a clone of Honcho, Hindsight, OpenViking, ByteRover,
Mem0, RetainDB, Supermemory, or Holographic memory.

The conclusion from repo inspection and external comparison is simple:

> Dory already has the right primitives. The work is to turn them into a small
> internal kernel with explicit contracts, not to add another memory system.

## Trust Order

When sources disagree, use this order:

1. Current code and tests.
2. `docs/current-state/`.
3. This plan.
4. Older drafts and archived refactor notes.
5. External provider research notes.

## Kernel Invariants

Keep these stable through all slices:

- Markdown is the canonical human-readable source of truth.
- SQLite sidecars are rebuildable indexes, ledgers, caches, and evidence planes.
- Canonical memory is source-backed and reviewable.
- Runtime context is compiled output, never the canonical store.
- Raw sessions stay separate from durable canonical memory.
- Semantic writes remain guarded by dry-run, inbox, proposal, and canonical-write controls.
- Profiles and scopes decide what can enter hot context.
- Unknown profiles fail closed.
- LLM planning, composition, extraction, and compilation are optional accelerators with deterministic fallbacks.
- Public docs, tests, fixtures, and evals use synthetic examples only.

## Non-Goals

Do not do these as part of the memory-kernel pass:

- No graph database.
- No cloud-only memory dependency.
- No always-on LLM call for every chat turn.
- No second canonical store beside markdown plus claim/event/evidence records.
- No hidden private-to-public promotion.
- No virtual filesystem replacement for Dory's existing markdown paths.
- No broad HTTP/MCP/CLI breaking changes until the internal kernel contract is stable.
- No cleanup-only refactor mixed into behavior slices unless it is required to keep the slice safe.

## Current Delta Matrix

| Plane | Exists Today | Missing / Weak | Plan |
|---|---|---|---|
| Hot context | `WakeBuilder`, `ActiveMemoryEngine`, profiles, project resolution, partial active-memory responses | Active-memory owns too many concerns; wake and active-memory do not share one typed packet | Add contract docs, then extract helpers, then introduce `hot_context.py` |
| Entity memory | `EntityRegistry`, `SubjectResolver`, semantic routing, canonical pages | No small entity context packet for retrieval; relationships are not clearly separated from links/aliases | Add deterministic entity/project context packet before broad search |
| Evidence retrieval | Exact get, BM25/vector/hybrid, session plane, links, claims, events, semantic evidence, retrieval planner | Planner emits query strings, not typed retrieval attempts; search engine still owns too many stages | Evolve planner into typed attempts after entity/observation contracts exist |
| Observations | Claims and claim events act as durable active truth | No explicit observation index with source refs, freshness, and status | Add observation index over existing claim/evidence records, not a second truth store |
| Compiler jobs | Dream, digest, wiki refresh, maintenance, migration audit, proposals | These are not described as one compiler plane; extraction/promotion boundaries are uneven | Reconcile existing ops jobs before adding new worker abstractions |
| Surfaces | CLI, HTTP, MCP, Hermes, OpenClaw share core behavior | Runtime consolidation is partial; wake is still instantiated directly in wrappers | Finish `DoryRuntime` adoption after contract inventory |
| Cleanup | Search package split and `DoryRuntime` already exist | Large modules remain: active-memory, semantic-write, search engine, adapters | Track cleanup separately after tests characterize behavior |

## External Patterns To Borrow

- Honcho: async derivation and observer-to-observed thinking.
- Hindsight: fact versus observation layers, freshness, stale verification, evidence citations.
- OpenViking: progressive context tiers and typed retrieval planning.
- ByteRover: local markdown discipline, pre-compaction flush, no-hit honesty, reviewable curation.
- Mem0: scoped CRUD ergonomics and filter-based retrieval.
- RetainDB: typed memory, temporal validity, non-destructive supersession.
- Supermemory: static/dynamic profile split, update relationships, context fencing before ingest.
- Holographic: local SQLite, explicit trust/feedback, cheap deterministic retrieval.

Borrow these as interfaces and discipline, not as infrastructure.

## Slice 1 - Kernel Contract And Delta Inventory

Goal: make the current system explicit before changing behavior.

Create `docs/memory-kernel/kernel-contract.md` with:

- Stable request/response types from `src/dory_core/types.py`.
- Source-of-truth boundaries: markdown, claim store, entity registry, session plane, generated wiki, indexes.
- Current callable internal APIs:
  - `WakeBuilder`
  - `ActiveMemoryEngine`
  - `SearchEngine`
  - `SemanticWriteEngine`
  - `EntityRegistry`
  - `ClaimStore`
  - `SessionEvidencePlane`
  - `DoryRuntime`
- Existing versus missing matrix for the four planes.
- Current DB/table ownership at a high level.
- Compatibility rules for CLI, HTTP, MCP, Hermes, and OpenClaw.

Do not add new behavior in this slice.

Validation:

```bash
uv run python scripts/release/check-public-safety.py --path docs
```

## Slice 2 - Characterization Tests Before Extraction

Goal: lock down behavior before moving code.

Use existing tests first and add focused golden tests only where behavior is under-specified:

- Active-memory block shape, source filtering, partial warnings, session gating.
- Wake profile behavior, project injection, unknown-profile failure.
- Semantic write replay, evidence artifact creation, claim mutation, canonical/tombstone rendering.
- Search result ordering, durable/session merge, planner fallback, rerank warnings.

Target validation:

```bash
uv run pytest -q tests/unit/test_active_memory.py tests/integration/core/test_active_memory_flow.py
uv run pytest -q tests/integration/core/test_wake_builder.py tests/unit/test_profiles.py
uv run pytest -q tests/unit/test_semantic_write.py tests/integration/core/test_semantic_write_flow.py
uv run pytest -q tests/integration/core/test_search_engine.py tests/integration/core/test_session_fallback_search.py
```

## Slice 3 - Active-Memory Module Extraction

Goal: make the hot path maintainable without changing behavior.

Keep `ActiveMemoryEngine.build()` as the public orchestration API. Extract pure helpers:

- `active_memory_policy.py`
  - source policy
  - profile/session gating
  - prompt classification
- `active_memory_retrieval.py`
  - search candidate collection
  - candidate scoring/filtering
  - project-state result injection
- `markdown_excerpt.py`
  - canonical file excerpts
  - frontmatter stripping
  - safe evidence text
- `active_memory_render.py`
  - summaries
  - bullets
  - final block rendering
  - budget fitting

No response shape changes. No new planner behavior.

Validation:

```bash
uv run pytest -q tests/unit/test_active_memory.py tests/integration/core/test_active_memory_flow.py tests/integration/http/test_active_memory_http.py
uv run ruff check src/dory_core tests/unit/test_active_memory.py
```

## Slice 4 - Runtime Contract Completion

Goal: make runtime construction boring and shared.

Add a runtime-owned wake builder to `DoryRuntime` and route HTTP/MCP wake through it.
Keep backwards-compatible aliases until all surfaces are verified.

Files likely touched:

- `src/dory_core/runtime.py`
- `src/dory_http/app.py`
- `src/dory_mcp/server.py`
- tests under `tests/integration/http/` and `tests/integration/mcp/`

Validation:

```bash
uv run pytest -q tests/integration/http/test_http_routes.py tests/integration/http/test_active_memory_http.py
uv run pytest -q tests/integration/mcp/test_stdio_server.py tests/integration/mcp/test_tcp_server.py tests/integration/mcp/test_tool_schema.py
uv run python -c "from dory_core.runtime import build_dory_runtime; print('runtime ok')"
```

## Slice 5 - Entity Context Packet

Goal: add deterministic entity/project lookup before broad retrieval.

Create a small entity context layer over existing primitives. Do not create entities
during retrieval.

Suggested API:

```python
@dataclass(frozen=True, slots=True)
class EntityContext:
    entity_id: str
    canonical_name: str
    family: str
    canonical_path: str | None
    matched_by: str
    source_refs: tuple[str, ...]
```

Use it from active-memory first, then wake if useful:

- Resolve `project`, `cwd`, and obvious subject mentions through existing registry/project helpers.
- Include exact source refs and canonical path.
- Return empty when unresolved or ambiguous.

Validation:

```bash
uv run pytest -q tests/unit/test_entity_registry.py tests/unit/test_active_memory.py
uv run pytest -q tests/integration/core/test_active_memory_flow.py tests/integration/core/test_wake_builder.py
```

## Slice 6 - Observation Index Over Evidence

Goal: add observations without creating a second truth system.

Observations are derived/indexed views over claims and evidence. They must be
rebuildable. Claims remain the durable active-truth layer. Markdown remains
canonical.

Minimal model:

```python
@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    title: str
    content: str
    entity_ids: tuple[str, ...]
    status: Literal["active", "stale", "retired", "rejected"]
    freshness: Literal["new", "stable", "stale"]
    confidence: Literal["low", "medium", "high"]
    created_at: str
    updated_at: str

@dataclass(frozen=True, slots=True)
class ObservationEvidence:
    observation_id: str
    claim_id: str | None
    evidence_path: str
    quote: str
    relevance: Literal["low", "medium", "high"]
    observed_at: str | None
```

Rules:

- Every observation needs at least one source reference.
- No unsupported synthesized "insight" rows.
- V1 derives from `ClaimStore` only.
- Session-derived observations come later through compiler/proposal flow.
- Freshness is deterministic; do not ask an LLM to guess trend.
- If observation DB is absent, retrieval returns empty quickly.

Likely files:

- `src/dory_core/observation_store.py`
- `src/dory_core/observation_builder.py`
- `src/dory_core/observation_retrieval.py`
- new tests under `tests/unit/` and `tests/integration/core/`

Validation:

```bash
uv run pytest -q tests/unit/test_claim_store.py tests/unit/test_claim_store_events.py
uv run pytest -q tests/integration/core/test_event_driven_canonical_pages.py
uv run pytest -q tests/unit/test_observation_store.py tests/unit/test_observation_retrieval.py
```

The observation tests are new and should be added in the same slice.

## Slice 7 - Typed Retrieval Attempts

Goal: evolve the current planner from query-list planning to kernel-attempt planning.

Current planner behavior is already useful, but it emits durable/session query lists.
The target planner emits typed attempts that execute deterministically:

```text
entity_lookup(name, family?)
claim_lookup(entity_id, kind?)
observation_lookup(entity_id?, query)
session_recall(query, scope)
durable_search(query, mode, scope)
link_neighbors(path, direction)
```

Rules:

- Strict schema validation.
- Planner output never writes memory.
- Execution remains deterministic and budgeted.
- On planner failure, fall back to existing deterministic durable/session query behavior.
- Do not run all strategies on every request; profile, scope, corpus, and deadline decide.

Likely files:

- `src/dory_core/retrieval_planner.py`
- `src/dory_core/search/engine.py`
- `src/dory_core/active_memory.py` or extracted retrieval module
- tests for fallback, invalid schema, and session gating

Validation:

```bash
uv run pytest -q tests/unit/test_query_planner_toggle.py tests/unit/test_rerank_orchestrator.py
uv run pytest -q tests/integration/core/test_search_engine.py tests/integration/core/test_session_fallback_search.py
uv run pytest -q tests/unit/test_active_memory.py tests/integration/core/test_active_memory_flow.py
```

## Slice 8 - Shared Hot Context Packet

Goal: unify wake and active-memory around one typed internal packet.

Suggested internal shape:

```python
@dataclass(frozen=True, slots=True)
class HotContextPacket:
    profile: str
    guardrails: tuple[str, ...]
    project: EntityContext | None
    entity_context: tuple[EntityContext, ...]
    active_claims: tuple[SourceBackedItem, ...]
    observations: tuple[SourceBackedItem, ...]
    durable_evidence: tuple[SourceBackedItem, ...]
    session_evidence: tuple[SourceBackedItem, ...]
    sources: tuple[str, ...]
    warnings: tuple[str, ...]
    partial: bool
```

Rollout:

1. Use it internally in active-memory rendering.
2. Add wake support only after active-memory behavior is stable.
3. Keep external `WakeResp` and `ActiveMemoryResp` compatible unless a deliberate API change is reviewed.

Validation:

```bash
uv run pytest -q tests/unit/test_active_memory.py tests/integration/core/test_active_memory_flow.py
uv run pytest -q tests/integration/core/test_wake_builder.py tests/integration/http/test_active_memory_http.py
uv run pytest -q tests/integration/mcp/test_tool_schema.py
```

## Slice 9 - Compiler Plane Reconciliation

Goal: define the compiler plane using existing ops jobs before adding a new worker.

Inventory and align:

- dream/proposal generation
- daily digests
- wiki refresh
- wiki health
- maintenance reports
- migration audit/repair
- session ingest and recall promotion

Then define one small compiler contract:

```text
input evidence -> derived candidate -> reviewable proposal/artifact -> optional canonical promotion
```

Rules:

- Compiler jobs can be async or scheduled.
- Compiler jobs produce reviewable artifacts.
- No silent canonical rewrite from raw sessions.
- No hot-path LLM dependency.
- Context fencing must prevent recalled memory blocks from being re-ingested as new facts.

Validation:

```bash
uv run pytest -q tests/unit/test_ops.py tests/integration/cli/test_ops_commands.py
uv run pytest -q tests/integration/core/test_proposal_generation.py tests/integration/core/test_distillation_write.py
```

If new compiler modules are added, add dedicated tests in the same slice.

## Cleanup Backlog

These are real maintainability tasks, but they should not block the memory-kernel
behavior slices unless they become necessary.

- Continue decomposing `src/dory_core/search/engine.py` into durable retrieval, session merge, finalization, and recall logging collaborators.
- Split `src/dory_core/semantic_write.py` by pipeline stage:
  - route/plan/preview
  - semantic evidence artifacts
  - claim recording
  - canonical/tombstone publishing
  - idempotence
- Move semantic-write dependency construction into `DoryRuntime`.
- Keep `SearchEngine.search()` and `SemanticWriteEngine.write()` stable as public internal APIs.
- Split oversized adapters only when tests make it safe:
  - `src/dory_cli/main.py`
  - `src/dory_http/app.py`
  - `plugins/hermes-dory/provider.py`
- Keep migration-only code out of hot runtime imports where possible.
- Isolate brute-force vector search behind a small retrieval interface before replacing it.

Cleanup validation should include import compatibility:

```bash
uv run python -c "from dory_core.search import SearchEngine; from dory_core.runtime import build_dory_runtime; print('imports ok')"
uv run pytest -q
```

## Public Safety And Validation Rules

For docs-only slices:

```bash
uv run python scripts/release/check-public-safety.py --path docs --path README.md
```

For code slices:

```bash
uv run ruff check <touched-python-files> --select E,F
uv run pytest -q
uv run python scripts/release/check-public-safety.py
```

Use repeated `--path` flags when targeted scanning is needed. Avoid broad targeted
scans over directories that contain generated or dependency artifacts.
Run repo-wide `uv run ruff check .` after the current lint baseline is clean; do
not make an unrelated lint baseline failure part of a memory-kernel slice.

For OpenClaw package changes:

```bash
cd packages/openclaw-dory
npm run build
```

Also run the relevant Python package parity tests.

## Decision Gate

This plan is ready to implement when:

- `kernel-contract.md` exists and names what is current, partial, missing, and deprecated.
- Slice 2 characterization tests pass.
- Observation semantics are accepted as an index over evidence, not a second source of truth.
- Cleanup work is tracked separately from behavior work.
- Every slice has a runnable validation command using real test paths.
