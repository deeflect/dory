---
title: Dory Refactor Synthesis — Best-of-All-Worlds Memory Kernel
status: draft
type: architecture-note
created: 2026-05-22
scope: public-safe
---

# Dory Refactor Synthesis — Best-of-All-Worlds Memory Kernel

This note captures the working synthesis from the Dory refactor discussion: review current implementation flaws, compare against Honcho / OpenViking / Hindsight / ByteRover and related memory systems, and identify what to strip, add, and adjust.

## Executive Summary

Dory's core idea is still right:

- Markdown remains the canonical source of truth.
- SQLite/vector indexes remain disposable sidecars.
- Dory remains the private/local-first memory substrate.
- Hermes, OpenClaw, MCP, HTTP, and CLI are clients/adapters.
- Exact-path writes and semantic writes stay separate.

The problem is not the idea. The problem is accretion in the hot path.

Dory should become a smaller **memory kernel** with sharper planes:

```text
Dory Kernel
├── canonical markdown store
├── raw event/session log
├── fact/observation store
├── compiled cards / mental models
├── retrieval planner
├── tiered retriever
├── async compiler worker
└── thin adapters
```

The key correction from the discussion:

> Do not build a keyword heuristic router. Build a model-assisted, evidence-seeking retrieval planner.

A dumb router leads to endless path/keyword boosts. A planner emits typed retrieval attempts, which Dory then executes deterministically with evidence and budgets.

---

# Current Dory Findings

## What Works

Keep:

- Markdown as canonical durable memory.
- SQLite/vector sidecars as rebuildable indexes.
- Local-first/private-first deployment.
- Shared memory across agents instead of one agent-specific memory silo.
- Profile-scoped wake/search.
- Project-aware retrieval.
- Exact-path vs semantic-write separation.
- Dory as the canonical substrate, not a cloud provider replacement.

## Main Implementation Flaws

### 1. `search.py` is doing too much

Observed file size:

```text
src/dory_core/search.py: 1556 lines, 56515 bytes
```

It currently mixes:

- retrieval execution
- mode routing
- scoring
- deduplication
- frontmatter parsing
- hardcoded path priors
- confidence logic
- privacy-ish logic
- query expansion / hybrid behavior

Example concern: hardcoded boosts/penalties for paths like `core/active.md`, `core/env.md`, `inbox/`, `logs/`, generated files, etc.

This is a path to heuristic sludge. Search should not be the policy engine.

### 2. Hybrid search is too expensive for the default path

Runtime testing found approximate latencies:

- `wake`: ~100ms
- `get`: ~56ms
- session search: ~100ms
- BM25: ~2.6s
- vector: ~4.4s
- active memory: ~8.6–14.8s
- hybrid search: ~20s

Hybrid appears to run BM25 + vector + query expansion + repeated searches sequentially.

Conclusion: hybrid/deep retrieval should be explicit, not the default for interactive Hermes chat.

### 3. Active memory can still time out

A `dory_active_memory` call with a 5s budget timed out during the review.

Active memory should return the best partial context under budget rather than fail hard.

### 4. Unknown profile fallback is unsafe

Runtime testing found `wake(profile="nonexistent_profile")` silently returned personal/core context instead of failing closed.

Desired behavior:

```text
unknown profile → validation error
```

or:

```text
unknown profile → minimal safe profile
```

Never:

```text
unknown profile → personal/core user dump
```

### 5. Migration code is bloating runtime core

Found many `migration_*.py` modules in `src/dory_core/`, including:

- `migration_engine.py`
- `migration_llm.py`
- `migration_entity_synthesis.py`
- `migration_entity_discovery.py`
- `migration_core_seed.py`
- `migration_source_router.py`
- `migration_prompts.py`
- `migration_plan.py`
- `migration_normalize.py`
- `migration_idea_promotion.py`
- `migration_executor.py`
- `migration_batching.py`
- `migration_review_router.py`
- `migration_types.py`
- `migration_events.py`
- `migration_resolve.py`

Also, hot semantic write code imports migration helpers:

```python
from dory_core.migration_normalize import canonical_target_for_subject, normalize_migration_slug
```

Shared slug/canonical helpers should move to neutral core modules. Migration should move out of hot core.

### 6. Surface runtime setup is duplicated

Observed wrappers:

- `src/dory_http/app.py`: `HttpRuntime`
- `src/dory_mcp/server.py`: `RuntimeCore`
- CLI has its own builder helpers
- `src/dory_core/runtime.py`: `build_surface_runtime`

Dory should have one `DoryRuntime` in core, consumed by CLI/HTTP/MCP/Hermes/OpenClaw adapters.

### 7. Hermes provider is too large

Observed:

```text
plugins/hermes-dory/provider.py: 2221 lines, 85935 bytes
```

It combines:

- config loading
- env parsing
- Hermes config parsing
- tool schemas
- tool handlers
- validation
- HTTP client behavior
- fallback behavior

Recommended split:

```text
plugins/hermes-dory/
  config.py
  client.py
  schemas.py
  tools.py
  provider.py
```

### 8. CLI is too large

Observed:

```text
src/dory_cli/main.py: 1585 lines, 60945 bytes
```

Recommended split:

```text
dory_cli/
  commands/search.py
  commands/wake.py
  commands/write.py
  commands/admin.py
  commands/migration.py
```

### 9. Core imports CLI

Observed:

```python
# src/dory_core/ops.py
from dory_cli.eval import run_eval
```

Dependency direction should be CLI → core, never core → CLI.

---

# External Systems: Code-Level Lessons

## Honcho

Repo inspected: `plastic-labs/honcho`.

### Relevant Architecture

Honcho is not just keyword search. It maintains peer/entity state with a background model pipeline.

Core model:

- `Peer`: user, agent, or participant.
- `Session`: conversation containing peers.
- `Collection`: observations one peer has about another.
- `Document`: atomic observation with embedding, source IDs, derivation level, session name, etc.

Important concept:

```text
observer peer → observed peer
```

Example mappings for Dory:

```text
Hermes observing Dee
Hermes observing project Dory
Volodia observing repo Palace
Dee observing Hermes
```

### Deriver

Honcho has a background Deriver process:

```text
messages/events
  ↓
queue
  ↓
LLM extracts explicit atomic observations
  ↓
embeddings generated
  ↓
documents stored in collection
```

This decouples expensive extraction from the hot path.

### Dreamer

Honcho also has a heavier Dreamer layer with deduction/induction specialists, tools, surprisal sampling, and peer-card updates.

Useful, but probably too heavy for Dory initially.

### What Dory Should Borrow

- Peer/entity model as asymmetric observation collections.
- Async Deriver/Compiler worker.
- Atomic explicit observations.
- Peer/project cards as compiled output.
- Blend retrieval from semantic, recent, and most-derived observations.

### What Dory Should Avoid

- Full autonomous dreamer complexity at first.
- Making hot path dependent on eventual background jobs.
- Postgres/pgvector requirement as default.
- Storing peer cards in odd metadata places; Dory can store cards more cleanly.

## OpenViking

Repo inspected: `volcengine/OpenViking`.

### Relevant Architecture

OpenViking is a context database with a virtual filesystem model and L0/L1/L2 context tiers.

Context levels:

```text
L0 / ABSTRACT: .abstract.md — around 100 tokens
L1 / OVERVIEW: .overview.md — structured summary around 2k tokens
L2 / DETAIL: full content
```

### Non-Heuristic Retrieval Planner

OpenViking uses an LLM `IntentAnalyzer`, not a simple keyword router.

It emits a structured `QueryPlan` containing typed queries:

```text
TypedQuery:
  query: generated retrieval query
  context_type: skill | resource | memory
  intent: natural-language purpose
  priority: 1-5
```

This is the key pattern Dory should borrow.

### Hierarchical Retrieval

OpenViking retrieval:

1. Global vector search.
2. Pick high-score starting directories.
3. Recursively search child directories.
4. Propagate parent/child scores.
5. Optionally rerank.
6. Stop when top-k converges.

### What Dory Should Borrow

- LLM-generated retrieval plans.
- Typed queries by context plane.
- L0/L1/L2 hierarchy.
- Recursive context expansion.
- Score propagation from matched parent context to child docs.
- Early stopping / convergence checks.
- Hotness blending based on access frequency and recency.

### What Dory Should Avoid

- Hardcoded URI roots.
- Overly complex virtual filesystem if Dory's Markdown tree is enough.
- A giant monolithic retriever.
- Putting all planning in one enormous prompt.

## Hindsight

Repo inspected: `vectorize-io/hindsight`.

### Relevant Architecture

Hindsight has three primary user-facing operations:

```text
retain → recall → reflect
```

The important internal hierarchy:

```text
Mental Models → Observations → Raw Facts
```

This is the strongest anti-bullshit pattern for Dory.

### Retain

Hindsight extracts structured facts using an LLM schema roughly shaped as:

```text
what
when
where
who
why
fact_type
entities
causal_relations
```

It asks whether a fact would be useful to recall months later, and normalizes relative dates.

### Recall

Hindsight uses multi-strategy retrieval:

- semantic/vector
- BM25/full-text
- graph/entity traversal
- temporal retrieval

Results are fused with reciprocal rank fusion and optionally reranked with a cross-encoder.

### Reflect

The reflect agent is forced to search in quality order:

1. `search_mental_models`
2. `search_observations`
3. `recall` raw facts
4. `expand` for deeper source content

It carries a strong grounding rule: only use retrieved tool results.

### Observations

Observation model includes evidence:

```text
Observation:
  title
  content
  evidence:
    - memory_id
    - exact quote
    - relevance
    - timestamp
  trend:
    stable | strengthening | weakening | new | stale
```

Trend is computed algorithmically from evidence timestamps, not guessed by the LLM.

### What Dory Should Borrow

- Mental model → observation → raw fact hierarchy.
- Evidence-grounded observations with exact quotes.
- Multi-strategy retrieval plus RRF fusion.
- Temporal retrieval as a real strategy, not keyword matching.
- Delta operations for updating structured mental models/cards.
- Algorithmic freshness/trend computation.
- Bank/profile config with mission/directives/disposition-style knobs.

### What Dory Should Avoid

- Postgres-only architecture.
- Mandatory LLM extraction on every retain if too expensive.
- Over-complex tag isolation.
- Treating aggregate proof-count as enough confidence; Dory should track source reliability and conflict state more explicitly.

## ByteRover

Repo inspected: `campfirein/byterover-cli`.

### Relevant Architecture

ByteRover has strong coding-agent/project-memory ideas.

Three-layer memory/context system:

1. volatile session memory
2. persistent project context tree
3. version control for memory

### Project Context Tree

Stored under:

```text
.brv/context-tree/
```

Markdown with YAML frontmatter. Directories form knowledge domains.

Summaries:

- each directory gets `_index.md`
- child hashes detect staleness
- summary generation escalates:
  1. normal LLM summarization
  2. aggressive LLM summarization
  3. deterministic truncation fallback

### Snapshot / Staleness

ByteRover tracks `.snapshot.json` with content hashes to detect added/deleted/modified files.

### Archive / Ghost Cues

Low-importance entries can be archived losslessly while leaving small searchable stubs:

```text
_archived/*.full.md
_archived/*.stub.md
```

This is useful for Dory memory cleanup: preserve history without keeping everything in active retrieval.

### Git for Memory

ByteRover uses a nested Git repo inside the context tree, via `isomorphic-git`.

This is simpler than inventing a custom versioning system.

### Agent Integration

ByteRover injects decision-table workflow instructions into coding agents:

- code task → query memory first
- made decision/wrote code → curate memory before done
- long conversation → query again for each distinct task

### What Dory Should Borrow

- Project context tree for coding-agent memory.
- Content-hash snapshot for staleness.
- Summary staleness propagation.
- Archive full content + searchable ghost cue.
- Nested Git repo/versioning for curated project memory.
- Decision-table agent instructions for query/curate behavior.
- Memory taxonomy: decisions, entities, patterns, preferences, skills.

### What Dory Should Avoid

- Flat blob memory namespace.
- LLM dedup per draft at large scale.
- Excess connector sprawl.
- Fragile directory-summary regeneration everywhere.

---

# Revised Dory Design

## Replace Router With Retrieval Planner

Avoid:

```text
if query contains "current" → boost core/active.md
if query contains "privacy" → boost core/user.md
```

Use:

```text
LLM creates typed retrieval plan
Dory executes plan deterministically
retrieved evidence grounds answer
```

Example retrieval plan:

```json
{
  "intent": "project_refactor_design",
  "answer_style": "synthesis",
  "retrieval_plan": [
    {
      "plane": "compiled.project_card",
      "target": "dory",
      "depth": "L0",
      "reason": "Need current project state"
    },
    {
      "plane": "observations",
      "target": "project:dory",
      "reason": "Need durable architectural claims"
    },
    {
      "plane": "sessions",
      "query": "Dory refactor Honcho OpenViking Hindsight",
      "reason": "Need recent discussion"
    },
    {
      "plane": "raw",
      "paths": ["projects/dory/state.md"],
      "reason": "Need source of truth"
    }
  ]
}
```

The planner suggests. Retrieval verifies.

## Hot Path

```text
wake / active-memory
  ↓
retrieval planner
  ↓
compiled cards / mental models
  ↓
observations
  ↓
targeted raw docs only if needed
  ↓
partial return if budget expires
```

## Cold Path

```text
new session/write/event
  ↓
append raw event
  ↓
async Dory compiler
  ↓
extract facts
  ↓
resolve entities
  ↓
consolidate observations
  ↓
update cards/mental models
  ↓
refresh summaries/index
```

## Model Lanes

Dory should connect models by operation, not have one global model for everything.

```yaml
models:
  extraction:
    provider: local
    model: small-fast-json-capable

  summarization:
    provider: local
    model: medium

  reflection:
    provider: local_or_remote
    model: stronger

  embeddings:
    provider: local
    model: qwen3-embed

  rerank:
    provider: local
    model: qwen3-rerank
```

Principle:

- cheap/local for extraction and summarization
- stronger/optional for reflection
- never expensive reflection on every user turn

---

# Proposed Data Model

## Entity

```yaml
id: person:dee
type: person
aliases: []
stable_profile_path: core/user.md
dynamic_card_path: .index/cards/entities/person-dee.json
```

Entity types:

- person
- agent
- project
- tool
- organization
- concept

## Fact

```yaml
id: fact:...
entity_ids:
  - person:dee
kind: preference | decision | state | event | tool | project | workflow
what: ...
when: ...
where: ...
who: ...
why: ...
source:
  path: ...
  quote: ...
confidence: medium
status: active
created_at: ...
```

## Observation

```yaml
id: obs:...
title: ...
content: ...
entities:
  - person:dee
  - project:dory
evidence:
  - fact_id: fact:...
    path: ...
    quote: ...
    timestamp: ...
    relevance: ...
trend: stable | strengthening | weakening | new | stale
status: active
```

## Mental Model / Card

```yaml
id: card:project:dory
level: L0 | L1
updated_at: ...
summary: ...
active_decisions: []
known_gotchas: []
open_questions: []
source_paths: []
```

## RetrievalPlan

```yaml
intent: ...
budget_ms: 5000
steps:
  - plane: project_card | entity_card | observations | sessions | raw | deep
    target: ...
    query: ...
    depth: L0 | L1 | L2
    priority: 1-5
    reason: ...
```

---

# What to Strip

## Strip from hot core

1. Migration machinery from runtime core.
2. LLM query expansion from default interactive search.
3. Hardcoded path/keyword priors from `search.py`.
4. Surface-specific runtime wrappers.
5. Duplicate serialization helpers.
6. Raw sessions from vector-heavy default paths.

## Move / Split

```text
src/dory_core/search.py
  → search/retrieval.py
  → search/scoring.py
  → search/fusion.py
  → search/planner.py
  → search/dedup.py
  → search/policies.py
```

```text
plugins/hermes-dory/provider.py
  → config.py
  → client.py
  → schemas.py
  → tools.py
  → provider.py
```

```text
src/dory_cli/main.py
  → commands/search.py
  → commands/wake.py
  → commands/write.py
  → commands/admin.py
  → commands/migration.py
```

---

# What to Add

## 1. Async Compiler Worker

Maintains facts, observations, cards, summaries.

Inspired by Honcho Deriver and Hindsight consolidation.

## 2. Retrieval Planner

LLM-assisted structured planner inspired by OpenViking.

Planner emits typed retrieval steps; Dory executes with deterministic retrieval and evidence.

## 3. L0/L1/L2 Context Tiers

Inspired by OpenViking and ByteRover.

```text
L0: tiny card / abstract
L1: overview / mental model
L2: full raw source
```

## 4. Evidence-Grounded Observations

Inspired by Hindsight.

No observation without source quote/evidence.

## 5. Project Context Tree

Inspired by ByteRover.

For coding-agent memory:

```text
projects/{project}/
  state.md
  architecture.md
  decisions.md
  context/
    summaries/
    observations/
    archive-stubs/
```

## 6. Archive Ghost Cues

Preserve history but reduce active retrieval bloat:

```text
archive full old material
leave compact searchable stub
```

## 7. One DoryRuntime

Single runtime object consumed by HTTP/MCP/CLI/Hermes/OpenClaw.

---

# What to Adjust

## 1. Active Memory Must Be Budget-First

Example budget behavior:

```text
0–100ms: profile/project cards
100–800ms: scoped BM25/exact search
800–2500ms: optional vector
2500–4000ms: optional rerank
4000–5000ms: compose / fallback
```

If budget expires, return partial context with warnings.

## 2. Default Search Modes

Recommended defaults:

- casual Telegram: cards only / no deep search
- coding wake: project card + BM25
- exact lookup: exact/BM25
- research: hybrid
- maintenance/deep: hybrid + expansion + rerank

## 3. Profiles Become Policy Objects

Profiles should define allowed roots, default depth, default retrieval style, privacy behavior, and card preferences.

Example:

```yaml
profiles:
  coding:
    allowed_roots:
      - projects/
      - decisions/
      - knowledge/research/
    default_depth: L1
    default_search_mode: bm25
    private_roots: deny_unless_explicit

  casual:
    default_depth: L0
    default_search_mode: cards
    allowed_roots:
      - core/defaults.md
      - safe_profile_card
```

## 4. Unknown Profile Fails Closed

Immediate safety patch.

## 5. CLI Remote/Local Clarity

Runtime testing found local CLI and remote HTTP status represent different worlds.

Add explicit UX:

```bash
dory status --local
dory status --remote https://...
dory remote status
```

---

# Recommended Phases

## Phase 0: Safety and Performance Patches

1. Unknown profile fails closed.
2. Active memory returns partial context on timeout.
3. Hybrid is not default for interactive Hermes contexts.
4. Active memory gets strict internal stage budgets.
5. `/v1/get` returns line/hash metadata by default or via lightweight verification mode.

## Phase 1: De-Bloat Core

1. Extract migration modules from hot core.
2. Move slug/canonical-target helpers out of migration code.
3. Break `dory_core → dory_cli` import.
4. Split `search.py`.
5. Split Hermes provider.
6. Create one `DoryRuntime`.

## Phase 2: Vertical Slice of Compiled Cards

Start small:

```text
Dory project card + retrieval planner + active-memory uses card before search
```

Do not refactor the whole system first.

## Phase 3: Facts and Observations

1. Add Fact model.
2. Add Observation model with evidence quotes.
3. Add trend/freshness computation.
4. Add claim/observation retrieval before raw search.

## Phase 4: Tiered Retrieval

1. L0/L1/L2 summaries for project/state/core docs.
2. Planner chooses depth.
3. Full vector/hybrid only on explicit deep retrieval.

## Phase 5: Project Context Tree / Coding Memory

1. Content-hash snapshots.
2. Summary staleness propagation.
3. Archive full content + ghost stubs.
4. Optional nested Git for curated project memory.

---

# Final Position

Dory should not become Honcho, Hindsight, OpenViking, ByteRover, or Supermemory.

Dory should remain:

> A private local memory kernel with a boring canonical store, fast compiled cards, evidence-grounded observations, and a typed retrieval planner.

The winning path is not adding more heuristics.

The winning path is:

```text
raw source of truth
  → extracted facts
  → evidence-grounded observations
  → compiled mental models/cards
  → model-assisted retrieval plan
  → deterministic evidence retrieval
```

The next concrete engineering step should be a small vertical slice, not a giant rewrite:

```text
project:dory card
+ RetrievalPlan schema
+ planner-generated steps
+ active-memory card-first fallback
```

If that makes Dory faster and less noisy, expand outward.
