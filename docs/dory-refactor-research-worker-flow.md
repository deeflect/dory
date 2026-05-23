---
title: Research-Worker Flow — Grounding Dory Changes in External Repo Code
status: draft
type: architecture-note
created: 2026-05-22
scope: public-safe
aliases: [dory-research-worker, external-grounding-flow]
---

# Research-Worker Flow: Grounding Dory Changes in External Repo Code

## Why This Flow Exists

The Dory refactor synthesis identifies patterns to **borrow** and **avoid** from Honcho, OpenViking, Hindsight, ByteRover, Supermemory, and Mem0. But those conclusions are based on docs + repo inspection at one point in time. **Before implementing any specific change** — a RetrievalPlanner class, a Deriver-like worker, an Observation model, an L0/L1 tier — we need a repeatable subagent workflow that:

- Opens the actual external repo code (not just docs)
- Answers precise code-level questions about the pattern we're about to build
- Produces grounded artifacts (not vibes) that directly shape Dory implementation specs
- Flags when the external pattern has changed since last inspection

The research-worker flow below is designed for a **parent orchestrator agent** to dispatch subagents in parallel when preparing a Dory implementation phase.

---

## Flow Overview

```
Parent Agent (orchestrator)
│
├── Phase 0: Decide what to ground
├── Phase 1: Launch Research Workers (parallel)
├── Phase 2: Collect & Fuse Artifacts
└── Phase 3: Translate → Implementation Spec
```

---

## Phase 0: Decide What to Ground

Before launching workers, the orchestrator answers:

- **Which Dory change are we about to make?** (e.g., "Add a RetrievalPlanner that emits TypedQuery steps")
- **Which external system(s) have the most relevant prior art?** (e.g., OpenViking IntentAnalyzer for this one)
- **What's the specific question only code can answer?** (e.g., "What is the exact TypedQuery dataclass shape? How does IntentAnalyzer prompt pass to the LLM? Does it handle plan validation/failure?")

**Default mapping: Dory change → external repos to check:**

| Dory Change | Primary Repos | Secondary Repos | Skip If |
|---|---|---|---|
| Retrieval Planner | OpenViking | Hindsight | Plenty of internal docs |
| Async Compiler Worker | Honcho (Deriver) | Hindsight (consolidation) | Architecture is obvious |
| Observation/Fact Model | Hindsight | Honcho (Document) | Good enough from synthesis |
| Entity/Peer Model | Honcho | — | Dory's entity needs are simpler |
| L0/L1/L2 Tiers | OpenViking | ByteRover | — |
| Project Context Tree | ByteRover | — | — |
| Archive/Ghost Cues | ByteRover | — | — |
| Budget-First Active Memory | — (novel) | OpenViking | No external prior art; skip |
| Profile Policy Objects | — (novel) | — | No external prior art; skip |
| One DoryRuntime | — | — | Internal refactor only |
| RRF Fusion (recall) | Hindsight | — | Well-known algorithm |
| Temporal Retrieval | Hindsight | — | Well-known pattern |

---

## Phase 1: Research Workers

Each worker is a focused subagent. All are dispatched in parallel when the Dory change touches multiple external patterns.

### Worker 1: Honcho Code Inspector

**When to dispatch:** Dory is about to implement the Deriver/Compiler worker, the Peer/Entity model, or async observation extraction.

**Repo target:** `plastic-labs/honcho` — Core service (FastAPI + SQLAlchemy), `src/honcho/` directory.

**Questions this worker answers (checklist):**

1. **Deriver implementation:**
   - What is the exact Deriver entry point? (`src/honcho/deriver/__init__.py` or `python -m src.honcho.deriver`)
   - How does it poll for work? (database queue table, polling interval, locking strategy)
   - What does a single derive cycle extract? (messages → representations → summaries → peer cards)
   - Does it fall back when LLM is unavailable? How?
   - What's the exact schema for the queue table?

2. **Peer/Session/Document models:**
   - SQLAlchemy model definitions for `Peer`, `Session`, `Document`, `Collection`
   - What fields does `Document` have? (embedding, source_ids, derivation_level, session_name, etc.)
   - How are `observer_id`/`observed_id` relationships stored?

3. **Dreamer (if relevant):**
   - Threshold logic: when does Honcho trigger dreaming vs. just deriving?
   - What tools does the dreamer have access to?
   - Deduction vs. induction specialist pattern

**Artifact produced:** `research/honcho-code-notes.md`

```markdown
# Honcho Deriver — Code Findings

## Deriver Entry Point
- File: `src/honcho/deriver/__init__.py`, function `run_deriver()`
- Polls `deriver_queue` table every 1s (configurable via `DERIVER_POLL_INTERVAL`)
- Lock: `SELECT ... FOR UPDATE SKIP LOCKED` on queue rows

## Document Model (SQLAlchemy)
- File: `src/honcho/models/document.py`
- Fields: id, collection_id, content, content_hash, embedding(VECTOR(1536)),
           source_ids(ARRAY), metadata(JSONB), session_id, observer_id,
           observed_id, derivation_level, created_at, updated_at

## Derivation Cycle
1. Fetch unprocessed messages (batch_size=50)
2. For each session, collect recent messages
3. Call LLM with extraction prompt → structured observations
4. Embed observations → store as Documents in Collection
5. Update session's last_derived_at
6. Mark queue items as processed

## Key Takeaway for Dory
- The queue table pattern (messages → queue → deriver) is exactly what
  Dory needs for the Async Compiler Worker.
- Dory should NOT use Postgres SKIP LOCKED — use SQLite WAL + polling.
- Derivation prompt is ~400 tokens; Dory's extraction will be similar.
```

4. **Confidence tags per finding:**
   - `[CODE]` — verified in actual source code with line references
   - `[DOC]` — inferred from docs, not confirmed in code
   - `[STALE]` — repo may have changed since last inspection

---

### Worker 2: OpenViking Code Inspector

**When to dispatch:** Dory is about to implement the RetrievalPlanner, L0/L1/L2 context tiers, or hierarchical retrieval.

**Repo target:** `volcengine/OpenViking` — Python package, `src/openviking/` or similar.

**Questions this worker answers:**

1. **IntentAnalyzer implementation:**
   - Exact file and function signature
   - System prompt used for query analysis (what does it tell the LLM?)
   - Is it synchronous or async? How long does it take?
   - Does it validate TypedQuery output or trust the LLM?
   - What happens if LLM returns 0 queries? (fallback to direct vector search?)

2. **TypedQuery data structure:**
   - Exact Python dataclass or TypedDict definition
   - All fields: query, context_type (skill/resource/memory), intent, priority, etc.
   - How does priority interact with the retrieval queue?

3. **Hierarchical Retriever:**
   - `HierarchicalRetriever` class: constructor params, main method signature
   - Score propagation formula (alpha blend between parent/child scores)
   - Recursive search algorithm: does it use a priority queue? What's the termination condition?
   - Convergence detection mechanism

4. **L0/L1/L2 storage format:**
   - Where are `.abstract` (L0) and `.overview` (L1) files stored relative to content?
   - How are they generated? (on write, async, or on first request?)
   - Are they markdown, JSON, or something else?
   - How does retrieval decide which level to return?

**Artifact produced:** `research/openviking-code-notes.md`

```markdown
# OpenViking Retrieval Planner — Code Findings

## IntentAnalyzer
- File: `src/openviking/retrieval/intent_analyzer.py`, class `IntentAnalyzer`
- Method: `analyze(query: str, session_context: ...) -> list[TypedQuery]`
- Uses LLM chat completion with system prompt (~600 tokens)
- Returns 0-5 TypedQuery objects; 0 → falls back to simple vector search
- [CODE] No validation of LLM output — trusts the schema

## TypedQuery Dataclass (confirmed from code):
```python
@dataclass
class TypedQuery:
    query: str                  # Generated retrieval query
    context_type: str           # "skill" | "resource" | "memory"
    intent: str                 # Natural language purpose
    priority: int               # 1 (high) to 5 (low)
    root_dirs: list[str] | None # Override default root directories
```

## HierarchicalRetriever
- File: `src/openviking/retrieval/retriever.py`
- Uses `heapq` priority queue ordered by (priority, -score)
- Score propagation: `final_score = alpha * child_score + (1-alpha) * parent_score`
- Convergence: stop when top-K haven't changed for MAX_CONVERGENCE_ROUNDS (3)

## L0/L1 Storage
- [DOC] Inferred: `.abstract` is plain text ~100 tokens, `.overview` is markdown ~2k tokens
- Generated asynchronously after content is written
- [CODE?] Need to confirm whether this is eager (generated on write) or lazy (generated on first access)

## Key Takeaway for Dory
- Dory's RetrievalPlanner should validate LLM output (unlike OpenViking)
- The priority queue + convergence pattern is exactly right for Dory
- Score propagation alpha=1.0 means Dory should default to child-only scoring
```

---

### Worker 3: Hindsight Code Inspector

**When to dispatch:** Dory is about to implement Observations, Facts, Mental Models, the retain/recall/reflect pattern, or multi-strategy retrieval (TEMPR).

**Repo target:** `vectorize-io/hindsight` — Python SDK and server.

**Questions this worker answers:**

1. **Observation model:**
   - Exact Observation class fields (title, content, evidence, trend, status)
   - How is evidence stored? (list of `{memory_id, quote, relevance, timestamp}`?)
   - Trend computation algorithm: how does it determine stable/strengthening/weakening/new/stale?

2. **Mental Model structure:**
   - How are mental models represented? (markdown? structured fields?)
   - How are they user-curated vs. auto-generated?
   - How does reflect() choose between mental model vs. observation vs. raw fact?

3. **Retain pipeline:**
   - What exact LLM extraction prompt is used?
   - How does it normalize relative dates ("yesterday" → "2026-05-21")?
   - Does it extract causal relationships? (what/when/where/who/why/fact_type/entities/causal_relations)
   - Is retain synchronous or async?

4. **TEMPR multi-strategy retrieval:**
   - How are semantic, BM25, graph, and temporal queries executed?
   - Are they parallel or sequential?
   - RRF fusion implementation (reciprocal rank formula, k constant)
   - How is graph traversal implemented? (neo4j? in-memory entity graph?)

5. **Reflect agent:**
   - Forced search ordering: is it enforced in code or just documentation?
   - How does it ground responses? (tool result only, or mixed with LLM knowledge?)
   - Mission/directives/disposition: where are these stored? How are they injected?

**Artifact produced:** `research/hindsight-code-notes.md`

```markdown
# Hindsight — Code Findings

## Observation Model
- File: `src/hindsight/models/observation.py`, class `Observation`
- Fields: id, title, content, evidence (list[EvidenceRef]), trend (enum),
           status (enum), created_at, updated_at
- EvidenceRef: {memory_id: str, quote: str, relevance: float, timestamp: datetime}
- Trend computation: [CODE] Algorithmic based on evidence timestamp recency + count,
  not LLM-guessed. Uses weighted decay function.

## Retain Pipeline (confirmed from code)
- Sync call that invokes LLM → extracts structured facts
- Extraction prompt includes: "Extract what, when, where, who, why. Normalize dates."
- No async queue — retain blocks until extraction completes.
- [DOC] Hindsight later consolidates facts into observations in background.

## TEMPR (Multi-Strategy Retrieval)
- [CODE] Four strategies run in parallel via asyncio.gather()
- RRF: rank = 1/(k + position), k default = 60
- Graph strategy: [DOC] Uses entity co-occurrence, not a graph DB
- Fusion: weighted sum of RRF scores from each strategy

## Key Takeaway for Dory
- Observation evidence must store exact quotes + timestamps — this is Hindsight's
  strongest anti-bullshit pattern.
- Dory should NOT make retain synchronous — use Honcho's queue pattern instead.
- RRF with k=60 is the right default for Dory's result fusion.
- Trend computation must be algorithmic, not LLM-guessed.
```

---

### Worker 4: ByteRover Code Inspector

**When to dispatch:** Dory is about to implement the Project Context Tree, content-hash snapshots, archive ghost cues, or memory versioning.

**Repo target:** `campfirein/byterover-cli` — Go CLI.

**Questions this worker answers:**

1. **Context tree structure:**
   - Where is the context tree stored? (`.brv/context-tree/`?)
   - What files exist at each level? (`context.md`, knowledge entries as markdown)
   - How are domain → topic → subtopic hierarchies represented?

2. **Snapshot/staleness system:**
   - What is the `.snapshot.json` format? (path → content_hash mapping?)
   - How is staleness detected? (compare current hash vs. snapshot hash)
   - How is staleness propagated up the tree? (child stale → parent stale?)
   - What triggers summary regeneration?

3. **Archive ghost cues:**
   - How are archives stored? (`_archived/*.full.md` + `_archived/*.stub.md`?)
   - What data does a stub contain? (title, keywords, summary, original path, timestamp?)
   - How does the retrieval system weight stub vs. full content?
   - Can archived content be restored? How?

4. **Atomic writes:**
   - `DirectoryManager.writeFileAtomic()` — what's the implementation?
   - Crash safety: write to temp → rename?
   - File locking for concurrent agent access?

5. **ToolsSDK interface:**
   - Exact interface methods and signatures
   - How does the LLM use it for curation?
   - Error handling: what happens on failed write?

**Artifact produced:** `research/byterover-code-notes.md`

---

### Worker 5: Supermemory / Mem0 Inspector (Optional)

**When to dispatch:** Only if the Dory change specifically targets user-profile extraction, contradiction handling, or managed-service integration patterns. These are lower priority than the four workers above.

**Repo target:** `supermemoryai/supermemory` and `mem0ai/mem0`.

**Questions answered:**

1. **Supermemory:**
   - How does it handle knowledge updates and contradictions specifically?
   - Fact expiration mechanism — how does it "forget expired information"?
   - User profile building pattern

2. **Mem0:**
   - How does memory.add() work? (message → extract → store)
   - Update mechanism: does it merge, overwrite, or deduplicate?
   - How does it filter by user_id vs. agent_id?
   - Vector store integration

**Artifact produced:** `research/supermemory-mem0-code-notes.md` (if dispatched)

---

## Phase 2: Collect & Fuse Artifacts

The orchestrator collects all worker artifacts and performs cross-referencing.

### Step 2a: Freshness Check

For each external repo, check if the current repo HEAD at time of research differs materially from the version used in the original synthesis doc.

```text
honcho: HEAD abc1234 (synthesis was on abc1230) → 3 new commits, no model changes
openviking: HEAD def5678 (synthesis was on 9876543) → major refactor of retriever? → flag
hindsight: HEAD fedcba9 (synthesis was on aaaa000) → stable
byterover: HEAD 1234abcd → new since synthesis
```

### Step 2b: Cross-Reference Matrix

Build a matrix mapping each worker finding to the Dory change it affects.

| Finding | Source | Affects Dory Change | Confidence |
|---|---|---|---|
| Deriver uses queue table + SKIP LOCKED | Honcho [CODE] | Async Compiler Worker | High |
| IntentAnalyzer trusts LLM output unvalidated | OpenViking [CODE] | RetrievalPlanner | High — avoid this |
| Trend computation is algorithmic (not LLM) | Hindsight [CODE] | Observation model | High |
| Archive stubs contain summary + keywords | ByteRover [DOC] | Archive ghost cues | Medium — needs code confirm |
| RRF k=60 default | Hindsight [CODE] | Result fusion | High |
| L0/L1 generated async post-write | OpenViking [DOC] | Context tiers | Medium |

### Step 2c: Gap Analysis

Identify patterns the synthesis says Dory should borrow but where code evidence is weak:

- **Deriver retry/backoff** — not found in Honcho code yet
- **Graph entity traversal** — Hindsight docs mention it but code shows entity co-occurrence, not a real graph
- **Staleness propagation** — ByteRover docs describe it but code structure is unclear
- **Dreamer threshold** — not found in Honcho code (may be dead code or V2)

Flag these as `[WEAK EVIDENCE]` for the implementation spec.

---

## Phase 3: Translate Findings → Implementation Spec

Each worker artifact feeds into a specific section of the Dory implementation spec.

### Translation Template

```markdown
## Pattern: [External Pattern Name]

### Source
- Repo: plastic-labs/honcho
- File: `src/honcho/deriver/__init__.py`, lines 42-78
- Confidence: [CODE] — verified in active source

### Dory Equivalent
`src/dory_core/compiler/worker.py` — AsyncCompilerWorker

### Borrowed Implementation Detail
Honcho polls a `deriver_queue` table every 1s using `SELECT ... FOR UPDATE SKIP LOCKED`.
Dory will poll an SQLite `compiler_queue` table every 1s using SQLite WAL mode and
a simple `UPDATE ... WHERE processed=0 LIMIT 1` + row locking via `BEGIN IMMEDIATE`.

### Avoided Detail
Honcho uses Postgres-specific SKIP LOCKED. Dory will use SQLite WAL + polling.

### Implementation Spec Entry
- Class: `AsyncCompilerWorker`
- Constructor params: `queue_poll_interval: float = 1.0`
- Method: `poll_cycle()`: 1) SELECT pending queue items, 2) FOR each:
  a. Extract facts from raw text (LLM call), b. Embed facts, c. Store in obs store,
  d. Update card if threshold exceeded, e. Mark queue item processed
- Error handling: exponential backoff 1s → 5s → 30s, requeue after 3 failures
- SQLite safety: `BEGIN IMMEDIATE` per item, rollback on failure
```

### When to Skip

A research finding translates to **skip** under these conditions:

- `[CODE] confirmed pattern that Dory already has better` — e.g., Honcho's Postgres requirement
- `[DOC] only and contradicts Dory first principles` — e.g., cloud-native architecture
- `[WEAK EVIDENCE] and pattern is non-critical` — defer until more evidence exists

### When to Re-Research

A finding triggers re-research when:

- The same Dory change is revisited in a later phase
- External repo shows >50 commits since last inspection
- A new external system is discovered with better prior art

---

## Worker Dispatch Rules

### Default: Full Dispatch

For any non-trivial Dory change, dispatch all relevant workers in parallel.

```text
Making: RetrievalPlanner + L0/L1 tiers
→ Dispatch: OpenViking Worker (primary), Hindsight Worker (secondary for reflect ordering)
```

```text
Making: Async Compiler Worker + Observation model
→ Dispatch: Honcho Worker (primary for queue pattern), Hindsight Worker (primary for obs model)
```

### Parallelism Strategy

| Dory Phase (from synthesis) | Workers to Dispatch | Parallel Group |
|---|---|---|
| Phase 0: Safety patches | None (internal) | — |
| Phase 1: De-bloat core | None (internal refactor) | — |
| Phase 2: Compiled cards | OpenViking, Hindsight | Parallel A |
| Phase 3: Facts/Observations | Honcho, Hindsight, ByteRover | Parallel B |
| Phase 4: Tiered retrieval | OpenViking, ByteRover | Parallel C |
| Phase 5: Project context tree | ByteRover | Single worker |

All workers in a parallel group dispatch simultaneously. The orchestrator waits for all to complete before Phase 2.

### Time Budget Per Worker

| Worker | Expected Time | Notes |
|---|---|---|
| Honcho | 5-8 min | Large repo, need to understand Deriver fully |
| OpenViking | 4-7 min | Well-documented, code is clear |
| Hindsight | 4-6 min | Documentation is good, code is clean |
| ByteRover | 5-8 min | Go codebase, need to trace context tree |
| Supermemory/Mem0 | 3-5 min | Only if dispatched |

---

## Artifact Maturity Model

| Level | Label | Meaning |
|---|---|---|
| 1 | Fresh | Research completed in this session, HEAD recorded |
| 2 | Cached | Research from ≤7 days ago, same HEAD |
| 3 | Stale | Research from >7 days ago or HEAD changed |
| 4 | Needs re-research | >30 days or major refactor in external repo |

Workers should attempt to use **fresh** research. If a cached artifact exists and the external repo HEAD is the same, workers can skip re-downloading and instead do a targeted fast check on changed files only.

---

## Worker Failure Modes & Recovery

| Failure | Worker Action | Orchestrator Action |
|---|---|---|
| Repo is down (403/404/rate-limit) | Log error, try mirror (e.g., GitLab, local clone) | Use cached artifact if available; flag as `[STALE]` |
| Key file moved/renamed | Log the new location, answer from next-best evidence | Accept finding with `[CODE?]` tag |
| LLM extraction fails on source code | Retry with simpler question; fall back to docs-only | Flag finding as `[DOC]` |
| Worker times out | Return partial results with `[PARTIAL]` tag | Merge partial + cached; re-dispatch at higher budget |
| Repo has been archived/deleted | Mark as `[UNAVAILABLE]` | Remove from cross-ref matrix; note in risk register |

---

## Example: Full Run for Phase 3 (Facts/Observations)

### Orchestrator Command

```text
Dory Phase 3: Facts and Observations
- Add Fact model
- Add Observation model with evidence quotes
- Add trend/freshness computation
- Add claim/observation retrieval before raw search

Repos to ground:
1. Honcho — Deriver queue pattern, Document model
2. Hindsight — Observation model, evidence, trend, retain pipeline
3. ByteRover — archive stubs (observation stubs for archived facts)

Dispatch: all 3 in parallel, budget 6 min each
```

### Parallel Dispatch

```
→ Honcho Worker
  - Answer: Deriver queue table schema, polling interval, locking
  - Answer: Document model fields (embedding, source_ids, derivation_level)
  - Produce: research/honcho-code-notes.md

→ Hindsight Worker
  - Answer: Observation dataclass, EvidenceRef struct
  - Answer: Trend computation algorithm (source code)
  - Answer: Retain extraction prompt
  - Produce: research/hindsight-code-notes.md

→ ByteRover Worker
  - Answer: Archive stub format (fields, location)
  - Answer: Snapshot staleness detection
  - Produce: research/byterover-code-notes.md
```

### Fuse & Translate

```markdown
## Observation Model Spec (derived from Hindsight + Honcho)

### Dory Observation
```yaml
Observation:
  id: str (ULID)
  title: str
  content: str
  entities: list[EntityRef]
  evidence: list[EvidenceRef]    # ← from Hindsight
    - fact_id: str
      path: str                  # ← Dory-specific: markdown path
      quote: str
      timestamp: datetime
      relevance: float
  trend: Trend                   # ← algorithmic, from Hindsight code
    value: stable|strengthening|weakening|new|stale
    last_confirmed: datetime
  status: active|archived|superseded
  created_at: datetime
  updated_at: datetime
```

### Trend Algorithm (copied from Hindsight, adapted for Dory)
```python
def compute_trend(evidence: list[EvidenceRef]) -> Trend:
    """
    Hindsight code (hindsight/models/trend.py:42-71):
    Uses weighted decay function based on evidence recency.
    - If most evidence is < 24h old and count > 3 → strengthening
    - If most evidence is > 30d old → stale
    - If evidence count stable over time → stable
    - If count declining over time → weakening
    - If only 1 evidence point → new
    Dory adaptation: add source_path diversity factor
    """
```

### Deriver Queue (from Honcho, adapted for SQLite)
```python
class CompilerQueueItem:
    id: int
    source_path: str
    event_type: str                # "write" | "session" | "import"
    status: str = "pending"        # pending | processing | done | failed
    retry_count: int = 0
    created_at: datetime
    processed_at: datetime | None
    error: str | None
```
```

---

## Implementation Note: Where This Flow Lives

This research-worker flow is designed for:

1. **Agent-driven workflows** — when a coding agent is about to implement a Dory change, it runs this flow as a preliminary step
2. **Phase gates** — before each Phase 2-5 implementation begins
3. **Change review** — when the synthesis conclusions need code-level verification

The artifacts (research/`*-code-notes.md`) can be cached and reused across sessions. The freshness check handles invalidation.

Supermemory and Mem0 are included as **optional secondary workers** — dispatch them only when:
- Dory needs a user-profile extraction/fusion pattern that Honcho doesn't cover
- Dory needs contradiction/conflict resolution for facts
- Dory is evaluating managed-service deployment patterns
- Otherwise, they're low priority for Dory's local-first design
