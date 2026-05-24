---
title: Memory Kernel Roadmap
status: active
type: roadmap
created: 2026-05-24
scope: public-safe
---

# Memory Kernel Roadmap

This folder is the review surface for the next Dory memory architecture pass.

The goal is not to clone another memory product. The goal is to keep Dory small and dependable while borrowing the useful shape from three places:

- **Hermes-style hot context** — fast bounded context for the current turn, session, and project.
- **Honcho-style entities/relationships** — explicit people, agents, projects, observations, and provenance.
- **Dory's existing evidence layer** — markdown source of truth, claim events, exact reads, search, links, and public/private boundaries.

## Review order

1. [architecture.md](architecture.md) — target shape and non-goals.
2. [implementation-slices.md](implementation-slices.md) — step-by-step work Codex can execute/review.
3. [comparison-notes.md](comparison-notes.md) — what to borrow from Honcho, Hermes, Hindsight, OpenViking, ByteRover, and similar projects.
4. [validation-gates.md](validation-gates.md) — commands and stop/go checks for each slice.

## What changed in docs

Older broad refactor notes were moved to `docs/archive/2026-05-refactor-planning/`. They remain available for comparison, but this folder is the current review surface.

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
