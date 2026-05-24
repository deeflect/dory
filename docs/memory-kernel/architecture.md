---
title: Memory Kernel Architecture
status: draft
type: architecture
created: 2026-05-24
scope: public-safe
---

# Memory Kernel Architecture

## Problem

Dory already has useful pieces:

- markdown as the canonical durable store
- SQLite-backed search/session/claim sidecars
- `EntityRegistry` for semantic write routing
- `ClaimStore` for claim history and evidence
- compiled wiki cards
- active-memory and wake APIs
- HTTP, MCP, CLI, Hermes, and OpenClaw surfaces

The pain is that memory behavior is still spread across too many layers. Hot context, semantic writes, entity resolution, search, compiled wiki, session recall, and profile policy can each work, but they do not yet feel like one small memory kernel.

## Target shape

Dory should expose a small internal kernel with four planes.

```text
Dory Memory Kernel
├── 1. Hot Context Plane
│   ├── wake block
│   ├── active-memory block
│   ├── project/current-task packet
│   └── profile/scope policy
├── 2. Entity Memory Plane
│   ├── entities: person, agent, project, concept, decision
│   ├── observations: source-backed statements about entities
│   ├── claims: active durable truth + event history
│   └── relationships: aliases, mentions, backlinks, ownership, project membership
├── 3. Evidence Retrieval Plane
│   ├── exact markdown get with hashes/lines
│   ├── claim/event/evidence lookup
│   ├── session recall
│   ├── BM25/vector/link search
│   └── retrieval planner and deterministic fallback
└── 4. Compiler Plane
    ├── background observation extraction
    ├── entity/project card refresh
    ├── stale-context detection
    ├── promotion from evidence -> claim -> canonical markdown
    └── cleanup/report jobs
```

## Hot context plane

Hot context is the small packet an agent should receive before doing work. It should be fast, profile-scoped, and boringly deterministic when no LLM is available.

Suggested packet order:

1. **Pinned profile facts** — safe constraints for the current profile.
2. **Project card** — if `project` or `cwd` resolves to a known project.
3. **Current task/session state** — recent local session evidence, capped by budget.
4. **Entity cards** — only entities relevant to the prompt/project.
5. **Evidence links** — exact paths/hashes for anything potentially important.

Rules:

- Return partial context instead of failing hard on timeout.
- Prefer compiled cards first, then exact evidence, then broader search.
- Never silently fall back from an unknown profile to a private/default profile.
- Keep the response explainable: include what was searched and what was skipped.

## Entity memory plane

Entity memory is the structured layer Dory needs before search becomes truly useful.

Minimum model:

```text
Entity
- id
- type: person | agent | project | concept | decision | tool | repo
- canonical_name
- aliases[]
- status: active | inactive | archived
- visibility/sensitivity
- canonical_path?
- created_at / updated_at

Observation
- id
- entity_id
- subject
- predicate/kind
- content
- source_refs[]
- confidence
- freshness
- status: active | superseded | rejected | archived
- created_at / observed_at

Relationship
- source_entity_id
- relation_type
- target_entity_id
- source_refs[]
- confidence
```

This should reuse `EntityRegistry` and `ClaimStore` before adding any new store. If a new table is needed, it should be an observation index over existing evidence, not a second canonical memory system.

## Evidence retrieval plane

Dory search should be evidence-first, not vibes-first.

Retrieval order for answerable memory questions:

1. Exact entity/project match.
2. Active claims and claim events.
3. Compiled cards/wiki shell.
4. Session recall when the query asks for recent/session evidence or `corpus=sessions|all`.
5. BM25/vector/link search.
6. Optional LLM planner/reranker under budget.

The retrieval planner should emit typed attempts such as:

```text
- entity_lookup(name="atlas", type="project")
- claim_lookup(entity="atlas", kind="decision")
- session_recall(query="last deployment issue", since="7d")
- durable_search(query="semantic write latency", mode="bm25", roots=["docs", "projects"])
```

Execution stays deterministic. If planning fails, Dory falls back to the current deterministic search path.

## Compiler plane

The compiler plane is where expensive or fuzzy work belongs.

Examples:

- extract observations from shipped sessions
- link observations to existing entities
- detect new aliases/project names
- refresh project/entity cards
- summarize trends and stale state
- propose canonical writes, but do not silently rewrite private memory

Compiler jobs can be async, scheduled, or manually triggered. They should produce reviewable artifacts and source references.

## Non-goals

Do not add these in the next slice:

- no graph database
- no cloud-only memory dependency
- no always-on LLM call for every chat turn
- no hidden private-to-public promotion
- no second canonical memory store beside markdown + claim/event evidence
- no broad rewrite of HTTP/MCP/CLI surfaces before the kernel contract is clearer

## Public/private boundary

The kernel must preserve Dory's privacy model:

- Canonical markdown remains local/private by default.
- Public docs/tests use synthetic examples only.
- Profiles and scopes decide what can be injected into hot context.
- Unknown profiles fail closed or return a minimal safe profile.
- Search results should carry enough source metadata for clients to decide whether to show/use them.
