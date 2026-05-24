---
title: Memory System Comparison Notes
status: draft
type: research-note
created: 2026-05-24
scope: public-safe
---

# Memory System Comparison Notes

These notes are for brainstorming. Treat them as directional patterns, not implementation truth. Before copying a specific design, inspect the current upstream code and tag findings as code-verified.

## Hermes

Useful pattern:

- Hot context is separate from long-term memory.
- Wake/active memory is budgeted and profile-shaped.
- The agent gets just enough current truth to act, not the whole corpus.
- Durable memory writes are explicit and should be compact.

Borrow for Dory:

- A first-class hot-context packet builder.
- Profile-aware context assembly.
- Partial results under time budget.
- Small, direct memory records instead of giant journal dumps.

Avoid:

- Letting runtime prompts become the canonical store.
- Mixing user profile, project state, and raw session evidence without source metadata.

## Honcho

Useful pattern:

- Memory is organized around peers/entities and observations.
- The important relation is often `observer -> observed`.
- Background derivation can turn raw messages into reusable observations.
- Sessions and entities are explicit rather than inferred only from keywords.

Borrow for Dory:

- Entity/project/person/agent cards.
- Source-backed observations.
- A compiler/deriver worker that runs outside the interactive turn.
- Relationship-aware retrieval before broad search.

Avoid:

- Requiring a cloud service or product-specific runtime.
- Storing observations as unreviewable magic without markdown/evidence references.
- Building a heavyweight graph database before Dory has a minimal observation index.

## Hindsight-style memory

Useful pattern:

- Distinguishes raw memories, observations, and mental models.
- Retrieval can use semantic, lexical, temporal, and graph signals.
- Evidence references make summaries auditable.
- Staleness/trend can matter as much as similarity.

Borrow for Dory:

- Observation objects with evidence refs.
- Freshness/staleness metadata.
- Search ordering that prefers current active facts before raw old evidence.
- Explicit `superseded` / `rejected` states.

Avoid:

- Treating LLM summaries as truth without source refs.
- Running all retrieval strategies on every interactive call.

## OpenViking-style retrieval planning

Useful pattern:

- Analyze intent before retrieving.
- Emit typed retrieval attempts rather than one fuzzy query.
- Use priority/budget when multiple contexts could help.

Borrow for Dory:

- Typed planner output: entity lookup, claim lookup, session recall, durable search, link traversal.
- Strict schema validation.
- Deterministic fallback when planning fails.

Avoid:

- Trusting unvalidated LLM output.
- Planner calls on the hot path when deadline is too tight.

## ByteRover/project-context style systems

Useful pattern:

- Project memory should be its own context tree.
- Coding agents need repo/task/decision context, not only personal memory.
- Archive/ghost cues can keep stale decisions visible without polluting current context.

Borrow for Dory:

- Project cards as first-class hot-context items.
- Current vs archived project state.
- Repo/path-aware context lookup.
- Stale decision warnings.

Avoid:

- Making project context a separate silo from global entities and claims.
- Over-indexing every file if exact project state docs are enough.

## Dory-specific synthesis

Dory should keep its own strongest properties:

- markdown source of truth
- git-reviewable changes
- exact reads with hashes/lines
- claim history and semantic evidence
- local-first deployment
- many clients, one memory substrate

The hybrid model should look like this:

```text
Hermes hot context
  + Honcho entities/observations
  + Hindsight freshness/evidence
  + OpenViking typed retrieval planning
  + Dory markdown/search/claim store
= Dory memory kernel
```

## Questions to check against other projects

When comparing new projects, answer these exact questions:

1. What is their canonical memory object?
2. Is the canonical object human-reviewable?
3. Do they separate raw evidence from summarized facts?
4. Do they model people/projects/entities explicitly?
5. Is retrieval planned, heuristic, or just vector search?
6. Are writes synchronous, async, or background-derived?
7. How do they handle stale/superseded memory?
8. Can memory be exported/migrated without the service?
9. What should Dory steal?
10. What should Dory deliberately avoid?
