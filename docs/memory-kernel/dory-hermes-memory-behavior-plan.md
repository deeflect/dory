---
title: Dory + Hermes Memory Behavior Plan
status: active
type: implementation-plan
created: 2026-06-08
scope: public-safe
---

# Dory + Hermes Memory Behavior Plan

This is the implementation-ready plan for reducing context bloat, stale-context
leakage, and over-personalized agent behavior across Dory, Hermes, Codex,
Claude, and other workers. It is public-safe: examples are synthetic and no
private corpus content is quoted.

## 1. Executive Decision

Recommended architecture:

- Keep **Dory** as the canonical private memory substrate.
- Keep **markdown + claim/event/evidence ledgers** as the source of truth.
- Use **active-memory** as the injected task brief.
- Use **search/get/digest/research/proposals** as tool-mediated evidence paths.
- Keep **Hermes built-in `SOUL.md` / `USER.md` / `MEMORY.md`** short, stable,
  and local to Hermes behavior. They should not become a second canonical store.
- Do **not** self-host Honcho as the default Dory/Hermes memory layer now.

Honcho should complement Dory only in a bounded experiment if multi-peer
social/personality memory becomes the actual product question. It should not
replace Dory's canonical pages, claim store, session evidence plane, or
reviewable write pipeline.

Do not:

- import all Dory memory into Honcho, Mem0, Supermemory, or any other provider;
- let generated summaries become canonical truth;
- delete historical evidence just because it is stale;
- inject raw search snippets after active-memory by default;
- give coding agents broad personal profile context unless the user asks.

## 2. Current-State Diagnosis

Confirmed bloat/staleness sources:

- `src/dory_core/wake.py` loaded compiled wiki cards before profile sections and
  only enforced the budget after at least one section was already rendered. An
  oversized first compiled card could crowd out project/core context.
- `src/dory_core/profiles.py` makes coding wake load `core/active.md`,
  `core/env.md`, and `core/defaults.md`. If those canonical hot files are stale,
  stale context enters before retrieval can correct it.
- `src/dory_core/types.py` defaults `WakeReq.include_recent_sessions` to `5`.
  `src/dory_core/wake.py` appends recent session summary lines with no age/status
  filter.
- `src/dory_core/types.py` defaults `ActiveMemoryReq.include_wake` to true, so
  direct callers can ask for wake plus retrieved evidence in one response.
- General search in `src/dory_core/search/engine.py` filters retired/superseded
  and quarantined docs, but `status: stale` mainly becomes a warning unless the
  active-memory admission layer excludes it.
- `plugins/hermes-dory/provider.py` previously built automatic context from
  active-memory or wake and then appended up to five raw search snippets.
- Append-style semantic writes can grow canonical pages because
  `src/dory_core/canonical_pages.py` preserves active claims and claim-event
  evidence/history.
- Digest and digest-mining flows under `src/dory_core/digest_writer.py` and
  `src/dory_core/digest_mining.py` can promote stale summaries if review is weak.
- Maintenance reports under `inbox/maintenance/` identify issues; they do not
  automatically correct canonical hot sources.

Suspected/conditional sources:

- `wiki/hot.md` can amplify stale claim-store state into helper context if claim
  events or session summaries are stale.
- Rerank can reduce stale results, but only when configured and successful.

Implemented in this slice:

- Active-memory admission now excludes inbox/archive, stale/retired/raw,
  imported/legacy/session/generated/cold, digest/log, semantic evidence-artifact,
  private, and sensitive durable sources before prompt injection.
- Hermes automatic context injection now uses the active-memory block as the
  brief and falls back to wake only when active-memory is empty.
- Hermes raw search snippet injection is opt-in via `inject_retrieved_evidence`.
- Claude Code bridge defaults `dory_wake` to `budget_tokens: 1200`.
- Wake truncates an oversized first section to the requested budget.
- Wake recent-session pointers include only completed or legacy no-status
  sessions, so active/interrupted session logs stay behind session recall.
- The public suite includes synthetic memory-behavior eval traps for Hermes
  briefs, current truth vs history, generated digests, coding-agent briefs, and
  Honcho deferral.

## 3. Target Memory Architecture

### Dory Responsibilities

- Canonical current truth in markdown:
  - `core/*.md`
  - `projects/<slug>/state.md`
  - `people/**` where appropriate
  - `decisions/canonical/**`
- Historical evidence:
  - raw/session logs;
  - digests;
  - semantic evidence artifacts;
  - claim events;
  - archived/imported material.
- Runtime retrieval:
  - wake/hot context;
  - active-memory task briefs;
  - search/get/link/digest;
  - proposal review and semantic writes.
- Compiler outputs:
  - `wiki/**`
  - digests
  - maintenance reports
  - dreams/proposals

### Honcho Responsibilities If Used

Honcho should be a temporary experiment for:

- peer/session modeling;
- Hermes conversational continuity;
- comparison against Dory active-memory for personal assistant UX.

It must not own:

- canonical current truth;
- raw private corpus import;
- project state;
- claim lifecycle;
- coding-agent task briefs;
- historical evidence archives.

### Hermes Built-In Files

- `SOUL.md`: short tone/identity guidance for Hermes only.
- `USER.md`: stable user-facing preferences only, not full biography.
- `MEMORY.md`: local Hermes scratch or compact reminders.
- Dory remains the durable shared memory layer.
- Hermes built-ins should mirror to Dory inbox only when useful for review, not
  as automatic canonical updates.

### Coding-Agent Memory

- Codex/Claude get direct project-aware wake once.
- For task continuation, they get active-memory brief with `include_wake=false`.
- Exact evidence comes through `dory_search` then `dory_get`.
- Spawned workers should usually receive supervisor-authored briefs with source
  paths instead of direct broad memory access.

## 4. Dory Structure Changes

Recommended hierarchy and metadata:

```text
core/
  active.md              # short current focus; hot; canonical
  env.md                 # operational environment; hot; canonical
  defaults.md            # defaults; hot/warm; canonical
projects/<slug>/state.md # project current truth; hot/warm; canonical
decisions/canonical/     # active durable decisions
people/                  # personal/person facts; not coding wake by default
knowledge/personal/      # voice/preferences; writing/personal only
digests/daily/           # generated historical summaries
digests/weekly/          # generated historical summaries
logs/sessions/           # raw session evidence
sources/semantic/        # write evidence artifacts
wiki/                    # generated/rebuildable shell and compiled cards
inbox/maintenance/       # cleanup ledgers/reports
inbox/proposed/          # reviewable memory proposals
archive/                 # historical/imported/retired material
```

Required frontmatter rules:

- Current truth: `status: active`, `canonical: true`, `source_kind: canonical`,
  `temperature: hot|warm`, `visibility`, `sensitivity`, `updated`.
- Historical evidence: `source_kind: session|generated|semantic|imported|legacy`,
  `temperature: cold|warm`, source refs, observed date.
- Generated summaries: `source_kind: generated`, never canonical source of truth
  unless explicitly compiled from active claims and still rebuildable.
- Session logs: searchable only when session/temporal intent is explicit.
- Archive/imported material: searchable evidence only, never active-memory
  injection.

Cleanup ledger format:

```yaml
---
title: Memory cleanup ledger
type: maintenance-ledger
status: active
scope: public-safe
updated: YYYY-MM-DD
---

## Queue
- id: cleanup-YYYYMMDD-001
  target: core/active.md
  issue: stale current claim
  evidence: inbox/maintenance/wiki-health.json
  action: replace-current-summary
  owner: operator
  status: pending
  rollback: restore previous hash from dory_get
```

Hot/wake budgets:

- Coding wake: 1200 token client default, but keep actual hot sections short:
  project card/state 360, active 480, env 340, defaults 260.
- Hermes active-memory prefetch: 600 token default, active-memory only.
- Privacy wake: boundary-only, no broad durable retrieval.
- Recent sessions: default `0` for coding bridges. When explicitly requested,
  wake includes only completed or legacy no-status session summaries; active and
  interrupted sessions stay behind session recall.

## 5. Honcho Plan

Recommendation: do not self-host Honcho for default production memory now.

Reasons:

- It adds Postgres/pgvector, Redis, API, deriver worker, model routing, and
  operational monitoring.
- It is optimized for peer/session derived representations, while Dory's key
  need is current-truth/historical-evidence separation.
- Importing Dory into Honcho would create a second derived truth store.

Conservative experiment if approved:

- Self-host in an isolated workspace, not the main corpus.
- Seed only synthetic or explicitly approved public-safe summaries.
- Disable broad auto-retain/import.
- Compare only Hermes conversational tasks, not coding-agent project work.
- Model/provider: cheap deterministic extraction model first; no private raw
  sessions until privacy boundaries are proven.

Explicit exclusions:

- `core/user.md`, `core/soul.md`, `people/**`, `knowledge/personal/**`
- raw sessions under `logs/sessions/**`
- credentials/contact/financial/legal/health material
- archives/imported material
- semantic evidence artifacts
- unreviewed digests/dreams/proposals

## 6. Coding-Agent Plan

Codex:

- Use Dory directly through MCP.
- Start with `dory_wake(profile="coding", budget_tokens=1200, cwd=...)`.
- Use `dory_active_memory(profile="coding", include_wake=false, cwd=...)` when a
  response needs task-specific continuation.
- Use repo-local docs and `AGENTS.md` for repository rules.

Claude Code:

- Same as Codex through `scripts/claude-code/dory-mcp-http-bridge.py`.
- Bridge defaults should stay coding/project biased.

Hermes:

- Use provider prefetch for active-memory brief.
- Keep `active_memory_include_wake: false`.
- Keep `inject_retrieved_evidence: false`.
- Use tools for exact search/get/digest/research.

Spawned workers:

- Prefer supervisor briefs with source paths.
- Give workers Dory tools only when their task requires fresh lookup.
- Do not give direct Honcho access during the first experiment.

## 7. Eval Suite

Use synthetic corpus fixtures. Compare baseline vs experiment with identical
prompts and record response block size, source paths, latency, and judged
usefulness.

Prompts:

1. "Fix the Dory Hermes memory provider so coding agents get focused context."
2. "What are we working on today for project Sample?"
3. "Continue yesterday's Claude session about the MCP bridge."
4. "Who is Casey?" with a stale generated profile and a current canonical page.
5. "What is the current deployment host?" with old imported env notes.
6. "Write landing-page copy in Dee's voice" with writing profile.
7. "Redact private identifiers from this public doc" with privacy profile.
8. "Debug a failing pytest around active-memory filtering."
9. "What changed in the latest weekly digest?"
10. "Find evidence for a historical decision from March."
11. "Should the agent assume OpenClaw is current?"
12. "What should Hermes remember from this conversation?"
13. "Summarize project state without personal profile context."
14. "Search for a unique cleanup marker."
15. "Spawn a coding worker to implement a small fix."
16. "Compare current truth and historical evidence for a superseded project."

Metrics:

- relevance: top injected sources match expected paths;
- stale-context rate: no stale/generated/imported source becomes active-memory
  bullet unless prompt explicitly asks history/digest;
- context size: injected block token estimate;
- latency: wake, active-memory, search, provider prefetch;
- task-continuation usefulness: can an agent continue work without re-searching?

Pass/fail:

- Coding active-memory must not include `core/user.md`, `core/soul.md`,
  `people/**`, private/personal knowledge, raw sessions, generated digests, or
  imported archives.
- Hermes default memory section must not include `## Retrieved Evidence`.
- Wake must respect the requested budget even when the first compiled card is
  oversized.
- Current-truth prompts should prefer `projects/<slug>/state.md`, `core/active.md`,
  or canonical decisions over digests/sessions.
- Session prompts may include session evidence, but it must be labeled as
  session evidence and scoped when a session key is supplied.
- Median active-memory latency should stay under the previous baseline plus 20%.

## 8. Implementation Plan

Phase 0, completed in this slice:

- Harden active-memory admission.
- Stop Hermes raw search-snippet injection by default.
- Fix Claude Code wake budget default.
- Truncate oversized first wake section.
- Add regression tests and docs for direct context vs briefs.
- Filter wake recent-session pointers to completed/no-status sessions.
- Add a public-safe cleanup ledger template.
- Add public synthetic eval traps for the memory-behavior plan.

Phase 1, low risk:

- Add a runnable active-memory behavior evaluator that measures source paths,
  block size, stale-source rate, latency, and task-continuation usefulness.
- Add a private-corpus command that writes `inbox/maintenance/memory-cleanup-ledger.md`
  from [cleanup-ledger-template.md](cleanup-ledger-template.md) after operator
  confirmation.
- Add optional age thresholds for recent-session wake pointers if completed
  sessions still prove noisy in dogfood.

Phase 2, medium risk:

- Add active-memory eval runner with the prompts above.
- Add deterministic current-truth vs historical-evidence scoring in search.
- Add canonical page compaction rules for append-heavy semantic-write pages.
- Add digest-mining guardrails so generated summaries create proposals, not live
  current claims, unless evidence is recent and canonical target is clear.

Phase 3, requires explicit approval:

- Any Honcho/Hindsight/OpenViking/Mem0/Supermemory/Holographic live deployment.
- Any import of private corpus material into another provider.
- Any automated rewrite of canonical hot files.
- Any purge/delete of historical evidence.

Rollback:

- Disable Hermes auto context with `memory_mode: tools`.
- Set `inject_retrieved_evidence: false`.
- Revert active-memory policy changes if a needed source family is wrongly
  excluded, then add a profile-specific allow rule instead of broadening all
  profiles.
- Restore canonical pages by hash from `dory_get`/git before cleanup writes.
- Reindex from markdown after rollback.

## 9. Open Questions

- Should coding wake ever include recent sessions by default?
  - Default: no; keep `include_recent_sessions: 0` for coding bridges and use
    active-memory/session scope for recent-work questions.
- Should Honcho be trialed at all?
  - Default: no production deployment; run only a synthetic Hermes UX benchmark
    if peer modeling becomes a real requirement.
- Should general search exclude `status: stale` by default?
  - Default: no; search should preserve historical findability. Active-memory
    and wake should be stricter than search.
- Should append-style semantic writes be limited?
  - Default: keep append for evidence preservation, but add compaction/review
    tooling for hot canonical pages.
