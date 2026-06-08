---
title: Memory Kernel Roadmap
status: active
type: roadmap
created: 2026-05-24
scope: public-safe
---

# Memory Kernel Roadmap

This folder contains the memory-kernel planning surface. The authoritative
execution plan is [final-plan.md](final-plan.md). The Hermes/coding-agent
behavior slice is [dory-hermes-memory-behavior-plan.md](dory-hermes-memory-behavior-plan.md).
Older draft files remain for comparison and background only.

The goal is not to clone another memory product. The goal is to keep Dory small and dependable while borrowing the useful shape from three places:

- **Hermes-style hot context** — fast bounded context for the current turn, session, and project.
- **Honcho-style entities/relationships** — explicit people, agents, projects, observations, and provenance.
- **Dory's existing evidence layer** — markdown source of truth, claim events, exact reads, search, links, and public/private boundaries.

## Review order

1. [final-plan.md](final-plan.md) — active execution plan, delta matrix, slices, and gates.
2. [dory-hermes-memory-behavior-plan.md](dory-hermes-memory-behavior-plan.md) — active Dory + Hermes UX recommendation, provider decision, concrete structure rules, and eval suite.
3. [cleanup-ledger-template.md](cleanup-ledger-template.md) — public-safe template for non-destructive cleanup review queues.
4. [architecture.md](architecture.md) — target shape and non-goals.
5. [implementation-slices.md](implementation-slices.md) — earlier draft slice plan retained for comparison.
6. [comparison-notes.md](comparison-notes.md) — what to borrow from Honcho, Hermes, Hindsight, OpenViking, ByteRover, and similar projects.
7. [validation-gates.md](validation-gates.md) — earlier gate draft retained for comparison; prefer the runnable gates in `final-plan.md`.

## What changed in docs

Older broad refactor notes were moved to `docs/archive/2026-05-refactor-planning/`. They remain available for comparison, but `final-plan.md` is the current source for implementation order and validation gates.

## Current thesis

Dory should become a **memory kernel** with four explicit planes:

```text
hot context      -> small, low-latency context returned every turn/session
entity memory    -> people/projects/agents/concepts plus observations and claims
evidence search  -> exact markdown, claim events, sessions, BM25/vector/link retrieval
compiler jobs    -> background promotion, summarization, cleanup, and stale-context repair
```

The important boundary:

- Runtime context can be compiled and cached.
- Canonical memory remains source-backed and reviewable.
- Product/client surfaces never get private corpus material unless the chosen profile and scope allow it.
