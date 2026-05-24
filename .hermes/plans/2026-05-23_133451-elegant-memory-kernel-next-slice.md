# Elegant Memory Kernel Next Slice Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add the next memory-kernel slice without turning Dory into a bloated Honcho clone: preserve the existing `EntityRegistry` / `ClaimStore` foundation, add only the smallest evidence-grounded observation layer needed for better recall, and keep hot-path retrieval fast and deterministic.

**Architecture:** Dory remains markdown-first. SQLite sidecars remain disposable/runtime indexes. The next slice adds an `ObservationStore` that groups existing claims/facts into evidence-backed observations, then exposes a narrow retrieval helper that active memory can optionally consult before raw search. No autonomous dreamer, no graph database, no giant new framework.

**Tech Stack:** Python, SQLite, dataclasses, existing Dory core modules, pytest, ruff.

---

## Non-Negotiable Design Constraints

1. **No bloat:** one small store module, one narrow retrieval helper, focused tests. Do not create a broad framework layer.
2. **No hot-path LLM dependency:** observation retrieval must work deterministically from stored rows. Extraction/compilation can be later.
3. **No duplicate entity system:** reuse `src/dory_core/entity_registry.py`; do not invent `Peer`, `Actor`, `Node`, etc.
4. **No duplicate fact system yet:** treat current `ClaimStore` as the first fact substrate. If a `Fact` type is needed, make it a thin alias/adapter over claim records, not a parallel DB.
5. **Evidence required:** every observation must have at least one evidence quote or claim/evidence pointer. No unsupported “insight” rows.
6. **Cheap failure mode:** if no observation DB exists, retrieval returns empty quickly.
7. **Public-safe:** tests/docs must use synthetic people/projects only.
8. **Elegant migration path:** this slice should be reversible and additive. Existing write/search/wake behavior must pass unchanged unless explicitly opted in.

---

## What This Slice Does

Build a minimal Hindsight/Honcho-inspired layer:

```text
EntityRegistry          already exists
ClaimStore              already exists, acts as raw facts/claims
ObservationStore        new: groups claims into evidence-backed observations
ObservationRetriever    new: cheap entity/query lookup over observations
ActiveMemory optional   later in this slice, tries observations before raw docs
```

## What This Slice Does NOT Do

- Does not implement Honcho-style peers/sessions/collections.
- Does not add async compiler worker yet.
- Does not add L0/L1/L2 summary generation for all docs.
- Does not add graph traversal beyond entity IDs.
- Does not replace markdown canonical pages.
- Does not make semantic write slower.

---

## Proposed Minimal Data Shape

### Observation

```python
@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    title: str
    content: str
    entity_ids: tuple[str, ...]
    status: str              # active | stale | retired
    trend: str               # new | stable | strengthening | weakening | stale
    confidence: str          # low | medium | high
    created_at: str
    updated_at: str
```

### Evidence

```python
@dataclass(frozen=True, slots=True)
class ObservationEvidence:
    observation_id: str
    claim_id: str | None
    evidence_path: str
    quote: str
    relevance: str           # low | medium | high
    observed_at: str | None
```

SQLite tables:

```sql
observations(
  observation_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT NOT NULL,
  trend TEXT NOT NULL,
  confidence TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)

observation_entities(
  observation_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  PRIMARY KEY (observation_id, entity_id)
)

observation_evidence(
  observation_id TEXT NOT NULL,
  claim_id TEXT,
  evidence_path TEXT NOT NULL,
  quote TEXT NOT NULL,
  relevance TEXT NOT NULL,
  observed_at TEXT,
  PRIMARY KEY (observation_id, evidence_path, quote)
)
```

Indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_observation_entities_entity
ON observation_entities(entity_id);

CREATE INDEX IF NOT EXISTS idx_observations_status_updated
ON observations(status, updated_at DESC);
```

Optional FTS can wait. Start with LIKE/entity lookup to avoid overbuilding.

---

## Task 0: Codex Review Gate Before New Work

**Objective:** Avoid stacking architecture work on an unreviewed cleanup branch.

**Files:** none.

**Steps:**
1. Have Codex review current branch.
2. Fix review findings.
3. Verify working tree is clean.
4. Run:

```bash
uv run ruff check .
ulimit -n 4096 && uv run pytest -q
```

**Exit criteria:** current branch is reviewed or consciously accepted as base.

---

## Task 1: Add ObservationStore Skeleton

**Objective:** Create a tiny SQLite-backed store with no integration yet.

**Files:**
- Create: `src/dory_core/observation_store.py`
- Test: `tests/unit/test_observation_store.py`

**Step 1: Write failing tests**

Test cases:
- initializes DB and tables
- refuses observation without evidence
- upserts observation with entities/evidence
- lists observations by entity
- returns empty tuple when no rows

**Step 2: Minimal implementation**

Implement:
- `ObservationRecord`
- `ObservationEvidence`
- `ObservationStore.__init__`
- `upsert_observation(...)`
- `list_for_entity(entity_id, limit=5)`
- `get(observation_id)`

**Guardrail:** `upsert_observation` must raise `DoryValidationError` or `ValueError` if evidence is empty or quote is blank.

**Verification:**

```bash
uv run pytest -q tests/unit/test_observation_store.py
uv run ruff check src/dory_core/observation_store.py tests/unit/test_observation_store.py
```

**Commit:**

```bash
git add src/dory_core/observation_store.py tests/unit/test_observation_store.py
git commit -m "feat: add evidence-backed observation store"
```

---

## Task 2: Add Deterministic Trend/Freshness Helper

**Objective:** Compute trend/freshness without asking an LLM.

**Files:**
- Modify: `src/dory_core/observation_store.py` or create `src/dory_core/observations.py`
- Test: `tests/unit/test_observation_store.py`

**Approach:** Start simple:
- `new`: one evidence item, recent
- `stable`: multiple evidence items over time with no replacements/invalidations
- `stale`: newest evidence older than threshold or observation status stale
- `weakening` / `strengthening`: only if enough event data exists; otherwise do not fake it

**Important:** It is acceptable to only implement `new`, `stable`, and `stale` in v1. Put `weakening/strengthening` in the type vocabulary but avoid guessing.

**Verification:**

```bash
uv run pytest -q tests/unit/test_observation_store.py
```

**Commit:**

```bash
git add src/dory_core/observation_store.py tests/unit/test_observation_store.py
git commit -m "feat: compute observation freshness deterministically"
```

---

## Task 3: Build Observations from Existing ClaimStore Rows

**Objective:** Reuse the current claim/fact substrate instead of introducing a duplicate fact DB.

**Files:**
- Modify: `src/dory_core/observation_store.py` or create `src/dory_core/observation_builder.py`
- Test: `tests/integration/core/test_observations_from_claims.py`

**Minimal builder:**

```python
def refresh_observations_from_claims(
    *,
    claim_store: ClaimStore,
    observation_store: ObservationStore,
    entity_id: str,
    limit: int = 20,
) -> tuple[ObservationRecord, ...]:
    ...
```

Behavior:
- reads current/recent claims for one entity
- groups by `kind`
- creates one observation per entity+kind, e.g. `Current state for project:sample`
- evidence quote = claim statement for now
- evidence path = claim.evidence_path
- never invents summary beyond grouping label + claim statement

**Guardrail:** This is not the final compiler. It is a deterministic bridge so observation retrieval can be tested.

**Verification:**

```bash
uv run pytest -q tests/integration/core/test_observations_from_claims.py tests/unit/test_claim_store.py
```

**Commit:**

```bash
git add src/dory_core/observation_builder.py src/dory_core/observation_store.py tests/integration/core/test_observations_from_claims.py
git commit -m "feat: derive observations from claim store"
```

---

## Task 4: Add ObservationRetriever

**Objective:** Provide a narrow retrieval API that active memory/planner can call without knowing SQLite internals.

**Files:**
- Create: `src/dory_core/observation_retrieval.py`
- Test: `tests/unit/test_observation_retrieval.py`

API:

```python
@dataclass(frozen=True, slots=True)
class ObservationSearchResult:
    observation_id: str
    title: str
    content: str
    entity_ids: tuple[str, ...]
    evidence: tuple[ObservationEvidence, ...]
    score: float

class ObservationRetriever:
    def __init__(self, store: ObservationStore) -> None: ...
    def search(self, *, query: str, entity_ids: tuple[str, ...] = (), limit: int = 5) -> tuple[ObservationSearchResult, ...]: ...
```

Scoring v1:
- entity match boost
- simple lowercase token overlap in title/content
- recent/stable boost
- no embeddings yet

**Guardrail:** This is intentionally boring and cheap. If no store or DB exists, return empty.

**Verification:**

```bash
uv run pytest -q tests/unit/test_observation_retrieval.py
uv run ruff check src/dory_core/observation_retrieval.py tests/unit/test_observation_retrieval.py
```

**Commit:**

```bash
git add src/dory_core/observation_retrieval.py tests/unit/test_observation_retrieval.py
git commit -m "feat: add lightweight observation retrieval"
```

---

## Task 5: Wire Runtime Lazily

**Objective:** Make `DoryRuntime` expose observation retrieval without making all surfaces pay setup cost.

**Files:**
- Modify: `src/dory_core/runtime.py`
- Maybe Modify: `src/dory_http/app.py` only if needed for dependency access
- Test: existing runtime/http tests or new `tests/unit/test_runtime_observations.py`

Approach:
- Add optional/lazy factory function or field for `ObservationStore`.
- DB path: `corpus_root / ".dory" / "observation-store.db"`.
- Do not instantiate in hot path unless a caller asks.

**Verification:**

```bash
uv run pytest -q tests/unit/test_runtime_observations.py tests/integration/http/test_http_routes.py
```

**Commit:**

```bash
git add src/dory_core/runtime.py tests/unit/test_runtime_observations.py
git commit -m "feat: expose observations through Dory runtime"
```

---

## Task 6: Add Read-Only CLI/HTTP Debug Surface (Optional)

**Objective:** Give Codex/users a way to inspect observations without creating new write paths yet.

**Files:**
- CLI option preferred over HTTP if keeping scope smaller:
  - Modify: relevant `src/dory_cli/commands/*.py`
  - Test: `tests/integration/cli/test_observation_commands.py`

Possible CLI:

```bash
dory observations list --entity project:sample --limit 5
```

Output should show:
- title
- entity ids
- evidence path(s)
- quote snippets

**Guardrail:** Read-only only. No CLI write/mutate commands in this slice.

**Verification:**

```bash
uv run pytest -q tests/integration/cli/test_observation_commands.py
```

**Commit:**

```bash
git add src/dory_cli tests/integration/cli/test_observation_commands.py
git commit -m "feat: add observation inspection command"
```

---

## Task 7: Planner/Active-Memory Integration Behind a Toggle

**Objective:** Let active memory try observations before raw search, but only behind config until reviewed.

**Files:**
- Modify: `src/dory_core/active_memory.py`
- Modify: `src/dory_core/config.py`
- Test: `tests/unit/test_active_memory.py` or focused integration test

Config:

```python
active_memory_observation_stage: bool = False
```

Behavior when enabled:
1. If planner/entity hint identifies an entity, query observations for that entity.
2. If observations found, include compact evidence-backed snippets.
3. Continue existing card/BM25 flow unless budget is exhausted.
4. If observation DB missing, no-op.

**Important:** Default can remain `False` for one commit to avoid behavior surprises. A later commit can enable it for coding profile after performance checks.

**Verification:**

```bash
uv run pytest -q tests/unit/test_active_memory.py tests/integration/core/test_active_memory_flow.py
```

**Commit:**

```bash
git add src/dory_core/active_memory.py src/dory_core/config.py tests/unit/test_active_memory.py
git commit -m "feat: add optional observation stage to active memory"
```

---

## Task 8: Performance & Simplicity Gate

**Objective:** Prove this did not make Dory slower or more complex in the common path.

**Commands:**

```bash
uv run ruff check .
ulimit -n 4096 && uv run pytest -q
uv run python scripts/release/check-public-safety.py --path src --path tests
```

Add a small benchmark or script if one already exists; do not invent a giant benchmark harness.

Manual checks:
- observation DB missing → active memory behavior unchanged
- observation DB with rows → retrieval returns in milliseconds
- no LLM required
- no private data in tests/docs

**Commit if needed:**

```bash
git commit -m "test: verify observation memory kernel slice"
```

---

## Review Questions for Codex

Ask Codex specifically:

1. Did we accidentally create a parallel memory system instead of reusing `ClaimStore`/`EntityRegistry`?
2. Is `ObservationStore` small enough and clearly bounded?
3. Does any hot-path code instantiate SQLite stores unnecessarily?
4. Are observations always evidence-backed?
5. Is retrieval deterministic and cheap?
6. Are there signs this should be deleted/simplified before continuing?

---

## Future Work After This Slice

Only if this proves useful:

1. Async compiler queue:
   - raw event/session/write → queued job → facts/claims → observations → cards
2. Real `Fact` adapter if `ClaimRecord` becomes too narrow.
3. Relationship edges between entities.
4. L0/L1 cards generated from observations.
5. Project context tree with content hashes.
6. Archive ghost cues.

Do not implement these until the observation slice passes review and performance gates.
