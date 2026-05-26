---
title: Dory Modularization Plan
status: active
type: implementation-plan
created: 2026-05-26
scope: public-safe
---

# Dory Modularization Plan

This is the working handoff after the May 2026 audit. The goal is not to add
more memory features first. The goal is to make Dory easier to understand,
change, turn features on/off, and verify before touching behavior deeply.

## Current Diagnosis

Dory has the right core idea:

- Markdown is the canonical human-readable memory store.
- SQLite is a sidecar for indexes, ledgers, caches, and session evidence.
- Raw session evidence stays separate from durable canonical memory.
- Agents use Dory through a narrow set of surfaces: CLI, HTTP, MCP, Hermes,
  OpenClaw, and bridge scripts.
- Semantic writes are guarded by dry-run, inbox/proposal, quarantine, and
  canonical-write controls.

The problem is not the model. The problem is that the runtime path has
accumulated too many partially overlapping layers.

The biggest code concentration points are:

- `src/dory_core/migration_engine.py` - very large legacy migration pipeline.
- `src/dory_http/app.py` - route definitions, app setup, HTML routes, API
  routes, runtime helpers, auth helpers, stream helpers.
- `src/dory_cli/main.py` - many top-level commands and migration commands.
- `src/dory_core/semantic_write.py` - planning, subject resolution, evidence,
  claims, canonical rendering, tombstones, indexing, idempotency, quarantine.
- `src/dory_core/search/engine.py` - mode routing, FTS, vector, hybrid merge,
  session merge, rerank, result selection, recall logging.
- `src/dory_core/active_memory.py` - profile policy, wake, helper context,
  search, session recall, optional LLM planning/composition, rendering.
- `src/dory_core/session_collectors.py`, `maintenance.py`, `digest_writer.py`,
  `retrieval_planner.py` - large but less central to the immediate hot path.

There are no obvious direct Python import cycles. The issue is fan-out,
responsibility mixing, and half-wired architecture.

## Important Constraint

Do not touch migration first.

Migration code is large and noisy, but it is not the first thing to refactor.
Treat it as a cold sidecar for now:

- Do not rewrite `migration_engine.py`.
- Do not move the migration package yet.
- Do not change migration behavior.
- Only touch migration-adjacent imports if they block hot-path modularization.
- If hot code imports migration helpers, move only the neutral helper out later
  in a tiny compatibility-preserving change.

The initial work should focus on the daily runtime path: wake, active memory,
search, exact get, semantic write, status, links, and adapters.

## Target Shape

Dory should become a small internal memory kernel with thin adapters.

```text
Dory Runtime / Kernel
├── configuration and feature flags
├── hot context plane
│   ├── wake
│   ├── active memory
│   ├── profile/scope policy
│   └── project/entity packet
├── retrieval plane
│   ├── exact get
│   ├── claims/events lookup
│   ├── BM25
│   ├── vector/hybrid optional
│   ├── session recall optional
│   └── rerank/planner optional
├── write plane
│   ├── exact path write
│   ├── semantic write plan
│   ├── evidence artifact
│   ├── claim/event mutation
│   └── canonical publisher
├── compiler/jobs plane
│   ├── dream
│   ├── digest
│   ├── maintenance
│   └── wiki refresh
└── adapters
    ├── CLI
    ├── HTTP
    ├── MCP
    ├── Hermes
    └── OpenClaw
```

Adapters should translate requests and responses. They should not own runtime
construction decisions.

## Modularity Rules

Use these rules for the next phase:

1. One runtime factory.
   `DoryRuntime` should be the shared construction point for HTTP, MCP, CLI,
   and tests. Surfaces should not independently assemble search engines,
   active-memory engines, semantic-write engines, rerankers, and planners.

2. Feature flags at boundaries.
   Optional behavior should be injectable/configurable at runtime boundaries:
   query expansion, retrieval planner, reranker, active-memory planner,
   composer, session recall, helper wiki context, vector search, and canonical
   writes.

3. Deterministic default path.
   The default memory path should work without LLM calls. LLM planning,
   composition, rerank, digesting, and extraction are accelerators, not
   requirements.

4. Small modules with clear direction.
   Core may not depend on CLI/HTTP/MCP adapters. Adapters depend on core.
   Shared schemas live in core. Provider-specific code lives in adapters.

5. Separate hot and cold code.
   Wake/search/write/status should not have to load migration-heavy concepts.
   Migration remains callable but outside the mental model for normal runtime.

6. No behavior refactor without characterization.
   Before moving a behavior-heavy module, add or identify focused tests that
   pin current behavior. Then extract without behavior changes.

7. Prefer delete-or-wire for scaffolding.
   Partially wired architecture is expensive. If `HotContextPacket`,
   observations, or typed retrieval plans are kept, wire them into a real
   runtime path. Otherwise defer them rather than building around unused code.

## Feature Toggles To Make Explicit

These should be represented as configuration or injectable runtime components,
not ad hoc checks scattered through code:

- `search.bm25_enabled`
- `search.vector_enabled`
- `search.hybrid_enabled`
- `search.session_recall_enabled`
- `search.query_expansion_enabled`
- `search.query_planner_enabled`
- `search.reranker_enabled`
- `active_memory.enabled`
- `active_memory.include_wake_default`
- `active_memory.helper_wiki_enabled`
- `active_memory.session_context_policy`
- `active_memory.llm_planner_enabled`
- `active_memory.llm_composer_enabled`
- `write.semantic_enabled`
- `write.canonical_write_requires_allow_flag`
- `write.proposals_enabled`
- `write.quarantine_enabled`
- `compiler.dream_enabled`
- `compiler.digest_enabled`
- `compiler.maintenance_enabled`
- `compiler.wiki_refresh_enabled`

Some of these already exist in environment variables. The cleanup is to make
them discoverable through one runtime settings object and one status payload.

## Runtime Facade First

The first implementation slice should be a runtime facade, not a broad refactor.

Suggested API shape:

```python
class DoryRuntime:
    def wake(self, req: WakeReq) -> WakeResp: ...
    def active_memory(self, req: ActiveMemoryReq) -> ActiveMemoryResp: ...
    def search(self, req: SearchReq) -> SearchResp: ...
    def digest(self, req: DigestReq) -> DigestResp: ...
    def get(self, path: str, *, from_line: int = 1, lines: int | None = None) -> dict: ...
    def write(self, req: WriteReq) -> WriteResp: ...
    def memory_write(self, req: MemoryWriteReq) -> MemoryWriteResp: ...
    def purge(self, req: PurgeReq) -> PurgeResp: ...
    def link(self, req: LinkReq) -> dict[str, object]: ...
    def research(self, req: ResearchReq) -> dict[str, object]: ...
    def status(self) -> dict[str, object]: ...
```

This does not need to be the final public API. It is a way to make surfaces
thin and make future changes easier.

Initial consumers:

- HTTP routes call `runtime.search(req)` rather than `_build_search_engine(runtime).search(req)`.
- MCP `RuntimeCore` delegates to `DoryRuntime` rather than duplicating tool
  logic.
- CLI can keep current command organization initially, but shared helper paths
  should use the runtime facade where practical.

## Hot Context Plan

Current state:

- `WakeBuilder` builds a wake block directly.
- `ActiveMemoryEngine` builds a context response through helper wiki context,
  wake, durable search, optional session recall, optional planner/composer, and
  rendering helpers.
- `HotContextPacket` exists, but active-memory still renders through older
  helpers and wake does not use it as the canonical packet.

Target:

- Introduce one internal packet builder for wake and active memory.
- Render wake and active-memory responses from that packet.
- Keep external `WakeResp` and `ActiveMemoryResp` compatible.

Order:

1. Characterize current wake and active-memory block shape.
2. Make active-memory render from `HotContextPacket`.
3. Make wake optionally build a compatible packet internally.
4. Remove duplicate source/budget/render logic only after tests pass.

## Retrieval Plan

Current state:

- Search already has submodules for FTS, scoring, policies, dedup, session,
  types, and utils.
- `SearchEngine` still owns too many orchestration decisions.
- `KernelRetrievalEngine` and typed retrieval attempts exist but are not part
  of the main runtime path.
- Hardcoded path priors still exist in `search/policies.py`.

Target:

- Fast deterministic retrieval first.
- Optional expensive retrieval second.
- Typed attempts only if they actually execute in runtime.

Preferred default order:

1. Exact entity/project/context lookup.
2. Claim/event/evidence lookup when available.
3. BM25 durable search.
4. Session recall only when requested by mode, corpus, scope, or profile.
5. Vector/hybrid only when enabled and useful.
6. LLM planner/reranker only when enabled and within budget.

Immediate action:

- Do not rewrite ranking yet.
- Add a retrieval facade under runtime so callers stop depending on
  `SearchEngine` internals.
- Decide whether `KernelRetrievalEngine` becomes the facade executor or remains
  deferred.

## Write Plan

Current state:

- `WriteEngine` handles exact guarded markdown writes.
- `SemanticWriteEngine.write()` handles many steps in one method.
- Claim/event ledgers and semantic evidence artifacts are useful and should
  stay.
- Transaction boundaries are still weak for multi-step semantic writes.

Target:

Split semantic write into clear stages:

```text
MemoryWriteReq
→ SemanticWritePlanner
→ SemanticWriteValidator
→ SemanticEvidenceWriter
→ ClaimMutation
→ CanonicalPublisher
→ IndexSync
→ MemoryWriteResp
```

Initial action:

- Add characterization tests before extraction.
- Extract pure planning/preview code first.
- Keep live behavior identical.
- Later, add a transaction-like commit wrapper or failure recovery around
  evidence + claim + canonical writes.

## Adapter Plan

HTTP:

- Keep FastAPI routes but split route registration by domain after runtime
  facade exists.
- `app.py` should not contain all helper logic forever.
- Browser routes can stay separate from JSON API routes.

MCP:

- Keep tool schema registry as source of truth.
- Runtime tool handlers should delegate to `DoryRuntime`.

CLI:

- Finish command splitting after runtime facade is in place.
- Avoid broad CLI cleanup until runtime behavior is stable.

Hermes/OpenClaw:

- Do not redesign them first.
- Keep them as HTTP clients/adapters.
- Add parity snapshots later so adapter drift is caught.

## Validation Strategy

Before behavior changes:

```bash
uv run pytest -q tests/unit/test_profiles.py
uv run pytest -q tests/unit/test_hot_context.py
uv run pytest -q tests/unit/test_typed_retrieval_attempts.py
uv run pytest -q tests/integration/core/test_wake_builder.py
uv run pytest -q tests/integration/core/test_search_engine.py
uv run pytest -q tests/integration/core/test_semantic_write_flow.py
```

For runtime facade work:

```bash
uv run pytest -q tests/integration/http/test_http_routes.py
uv run pytest -q tests/integration/http/test_active_memory_http.py
uv run pytest -q tests/integration/mcp/test_stdio_server.py
uv run pytest -q tests/integration/mcp/test_tool_schema.py
```

For public docs touched:

```bash
uv run python scripts/release/check-public-safety.py --path docs
```

Note from the audit: one attempted `uv run pytest -q tests/unit/test_profiles.py
tests/unit/test_hot_context.py tests/unit/test_typed_retrieval_attempts.py`
caused `uv` to rebuild `.venv` and then ran long without output, so it was
stopped. Re-run focused tests intentionally during implementation.

## Proposed Implementation Sequence

### Implementation Progress

As of 2026-05-26:

- Phase 1 has a shared `DoryRuntime` facade used by HTTP and MCP for the main
  JSON/tool paths.
- Phase 2 has a runtime feature flag inventory exposed through debug status.
- Phase 3 has active-memory rendering through `HotContextPacket`, with wake
  able to emit the same packet shape internally.
- Phase 4 has a runtime-owned retrieval facade while preserving the existing
  `SearchEngine.search()` behavior.
- Phase 5 has semantic write split into planner/preview, evidence artifact
  store, claim/registry recorder, and claim-derived canonical publisher.
- Phase 5 has replay handling for partial semantic-write failures: retries now
  reuse existing semantic evidence, can finish after claim-recording failures,
  and avoid duplicating active claims after canonical-publish failures.
- Phase 6 has the non-migration HTTP JSON routes split out of `app.py` into
  `dory_http.api_routes`, with shared request-id/API error helpers and a small
  `HttpRuntime` module.
- Phase 6 has the CLI hot-memory commands (`wake`, `active-memory`,
  `memory-write`, `purge`, `research`) split into `dory_cli.commands.memory`.
- Phase 6 has the remaining hot-path CLI commands (`search`, `get`, `status`,
  `reindex`, `neighbors`, `backlinks`, `lint`) split into
  `dory_cli.commands.core`.
- Phase 6 has CLI `memory-write` and `research` routed through `DoryRuntime`
  instead of constructing their own hot-path engines.
- Phase 6 has HTTP web/app/wiki routes split into `dory_http.web_routes`, so
  `app.py` now owns app construction, middleware, health, and the migration
  route.
- Runtime semantic-write construction is lazy so read-only runtime paths such
  as search do not initialize write-side ledgers.

Remaining before deeper behavior changes:

- Add any remaining failure-recovery coverage for lower-level indexing errors
  if index-side behavior needs to become transactional.
- Continue adapter slimming only where it does not disturb migration; the main
  remaining cold CLI surface is migration-oriented and should stay deferred.
- Keep migration set aside until the hot runtime path is stable.

Latest verification:

- `uv run ruff check` passed on the semantic-write/reliability slice.
- `uv run pytest -q tests/integration/core/test_semantic_write_reliability.py tests/unit/test_semantic_write.py`
  passed with 18 tests.
- `uv run pytest -q tests/integration/cli/test_semantic_write_commands.py tests/integration/cli/test_research_commands.py tests/integration/core/test_active_memory_flow.py tests/integration/cli/test_purge_command.py`
  passed with 10 tests after the CLI runtime delegation pass.
- A broader touched-surface regression passed with 228 tests across semantic
  write, HTTP, MCP, wake/hot context, retrieval, and CLI slices.
- `python3 scripts/release/check-public-safety.py --path docs/memory-kernel/modularization-plan.md`
  passed.
- `git diff --check` passed.

### Phase 1 - Runtime Facade

- Expand `DoryRuntime` into the central callable facade.
- Move repeated get/slice/link/status/digest helper behavior behind runtime
  methods.
- Route HTTP and MCP through it.
- Keep response shapes unchanged.

### Phase 2 - Feature Toggle Inventory

- Create one runtime feature/config object from existing settings.
- Expose enabled/disabled state in status/debug output.
- Make optional components injectable and easy to turn off in tests.

### Phase 3 - Hot Context Unification

- Make active-memory render from `HotContextPacket`.
- Make wake use the same packet internally or produce one beside the current
  response.
- Remove duplicate budget/source/render code after tests pin behavior.

### Phase 4 - Search Boundary Cleanup

- Keep `SearchEngine.search()` public but move orchestration decisions into a
  retrieval facade.
- Make session recall, vector, hybrid, planner, expansion, and rerank explicit
  branches with visible warnings/status.
- Avoid ranking behavior churn until tests characterize current ordering.

### Phase 5 - Semantic Write Decomposition

- Extract planning and preview. Done.
- Extract evidence artifact writer. Done.
- Extract claim mutation. Done.
- Extract canonical publisher. Done.
- Add failure/replay tests for partial write cases. Done for evidence reuse,
  claim-recording failure replay, and canonical-publish failure replay; indexing
  recovery remains a separate lower-priority boundary.

### Phase 6 - Adapter Slimming

- Split HTTP route modules.
- Finish CLI command split.
- Keep Hermes/OpenClaw stable and add parity snapshots.

### Phase 7 - Revisit Migration

Only after the hot runtime is modular:

- Move migration code into a cold package/module namespace.
- Keep compatibility imports if needed.
- Move neutral helpers out of migration-named modules.
- Do not mix migration movement with behavior changes.

## Success Criteria

Dory is ready for deeper behavior work when:

- A new runtime component can be turned on/off from one place.
- HTTP/MCP/CLI do not duplicate core engine construction.
- Wake and active-memory share a clear internal context representation.
- Search has an obvious cheap path and optional expensive paths.
- Semantic writes have clear stages and testable failure boundaries.
- Migration is isolated from the mental model of the hot path.
- The public API remains compatible while internals become easier to change.
