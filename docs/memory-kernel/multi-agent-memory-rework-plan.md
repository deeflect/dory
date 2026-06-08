---
title: Multi-Agent Memory Rework Plan
status: active
type: implementation-plan
created: 2026-06-08
scope: public-safe
---

# Multi-Agent Memory Rework Plan

This plan is for making Dory a low-bloat memory layer for one person working
with multiple AI agents, devices, and projects. Dory should make agents feel
oriented and stateful without turning wake into a dump of personal or project
history.

## Product Goal

Dory should behave like a memory governor:

```text
raw evidence -> distilled notes/proposals -> claims/observations/project state -> scoped hot context
```

Wake gives orientation. Active memory gives task context. Search/get gives
proof. Writes promote durable knowledge; they are not automatic logging.

## External Patterns To Borrow

- **Claude Code / Letta MemFS:** keep always-loaded startup memory tiny, use
  markdown files that humans can inspect, and lazy-load topic-specific detail.
- **Honcho:** model memory around subjects and sessions. For Dory, the subjects
  are the primary user, agents, projects, repos, devices, decisions, and
  concepts.
- **ByteRover:** curate project knowledge into a local context tree and make
  agents retrieve before implementing.
- **LangMem:** separate semantic, episodic, and procedural memory instead of
  treating all memory as one vector bucket.
- **Zep:** use temporal validity and invalidation instead of relying on latest
  search rank to resolve contradictions.
- **Mem0:** force scope filters such as user, agent, project, and session before
  search results can influence context.

## Anti-Patterns

- Flat top-k vector injection.
- Raw sessions entering wake.
- Large always-loaded profile files.
- Generated summaries without source paths, confidence, freshness, and
  sensitivity.
- Auto-promoting inferred personal facts into hot context.
- Letting personal or writing memory steer coding tool calls unless the task
  explicitly asks for that context.

## Memory Planes

| Plane | Purpose | Wake Eligible |
|---|---|---|
| Hot core | Small operating rules, profile guardrails, current project pointer | Yes, capped |
| Semantic | Current facts, preferences, decisions, project state | Only scoped excerpts |
| Episodic | What happened in sessions, failures, fixes, outcomes | No; active-memory only when requested |
| Procedural | Reusable workflows, prior mistakes, playbooks, skills | Scoped excerpts |
| Observations | Inferred source-backed patterns over claims/evidence | Only active and relevant |
| Raw evidence | Sessions, semantic evidence, digests, artifacts | Never directly |

## Profiles

| Profile | Include | Exclude |
|---|---|---|
| `assistant` | Personal preferences, active commitments, relevant relationships, current open loops | Raw coding sessions unless asked |
| `coding` | Project state, repo decisions, prior mistakes, procedures, minimal interaction preferences | Biography, people pages, writing voice, raw sessions |
| `writing` | Writing voice, audience constraints, active writing/project context | Unrelated coding state and broad identity |
| `privacy` | Boundaries, redaction rules, public/private policy | Sessions, people pages, inbox, raw evidence |
| `admin` | Service topology, ops state, infrastructure decisions | Secrets and private raw logs |

## Retrieval Contract

Context assembly should follow this order:

1. Resolve profile and allowed source categories.
2. Resolve project/entity from explicit project, cwd, or prompt.
3. Load exact project/profile cards only when the scope is clear.
4. Load active claims and observations for the resolved entity.
5. Add pinned decisions and procedures only when relevant.
6. Search durable memory with profile allow/deny filters.
7. Search sessions only on recency/session intent or explicit session scope.
8. Gate every candidate before prompt injection by relevance, source authority,
   freshness, scope, sensitivity, conflict status, and tool-risk.
9. Render a compact packet with source paths and warnings.

## Write Contract

Writes use three lanes:

- **Raw ingest:** sessions and transcripts stay as source evidence.
- **Proposal lane:** inferred facts, personal context, writing voice, and broad
  preferences require review.
- **Canonical lane:** explicit durable facts, project state changes, decisions,
  and repeated mistakes can become claims/project state with evidence.

Replace or supersede contradictory facts. Do not append competing active truth.

## Implementation Slices

### Slice 1 - Safer Coding Wake

Status: implemented.

When a coding agent passes `project` or `cwd` and Dory cannot resolve a project,
wake should not fall back to global active context. It should return a small
fallback plus a warning, so agents know they need active-memory/search before
assuming project state.

Validation:

```bash
uv run pytest -q tests/unit/test_wake_budget.py tests/integration/core/test_wake_builder.py
```

### Slice 2 - Wake Source Policy

Status: implemented for deterministic profile/path/frontmatter gates.

Move wake filtering from section order alone to policy:

- profile allow/deny patterns
- unresolved coding project/cwd warning and global-active fallback suppression
- explicit `assistant`, `coding`, `writing`, `privacy`, and `admin` profile
  boundaries
- status, temperature, source-kind, visibility, sensitivity, inbox/archive, and
  raw-session gates
- reason-included warnings for skipped wake sources

### Slice 3 - Observation Runtime Wiring

Status: implemented.

Wire the existing observation store into runtime:

- choose one observation DB path
- expose an ops refresh from claims
- inject only active, source-backed observations for resolved entity/project
- keep search behavior unchanged

### Slice 4 - Admission Gate

Status: implemented for observation injection; durable search admission remains
profile/path/status based.

Add a deterministic gate between retrieval and context injection. Score task
relevance, source authority, freshness, scope, sensitivity, conflict status, and
tool-risk. Withhold weak or merely adjacent memories.

### Slice 5 - Temporal Claims

Status: implemented in the claim ledger; deeper supersession metadata remains
backlog.

Extend claim events with temporal validity and supersession metadata so active
truth is explicit and stale claims do not compete by rank.

### Slice 6 - Memory Activity UX

Status: implemented as a compact CLI review surface.

Expose recent recalls, writes, proposals, warnings, source paths, and wake diffs
through CLI/HTTP UI. This is the review surface for keeping Dory clean.

Current implemented surface:

- active-memory warnings for unresolved/withheld context
- inline source attribution for source-backed active-memory items
- `dory ops observations-refresh` activity payload for derived observation
  refresh
- `dory ops memory-activity` payload with recent claim events, observation
  counts, and proposal counts

Remaining backlog:

- richer browser/HTTP activity UI with wake diffs and recall logs

## Success Criteria

- Coding wake contains project/env/defaults, not personal biography or writing
  voice.
- A new or unresolved project wake gives a clear warning instead of stale global
  project state.
- Writing wake recalls voice/style without pulling unrelated identity.
- Privacy wake is boundary-only by default.
- Active memory returns source-backed context with partial/stale/privacy
  warnings.
- Raw sessions only appear when recency/session context is requested.
- Agents can resume work from wake plus one active-memory call without Dee
  restating project basics.
- Hot context remains bounded; adding hot content forces review or demotion.
