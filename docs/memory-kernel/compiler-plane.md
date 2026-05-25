---
title: Compiler Plane
status: active
type: reference
created: 2026-05-24
scope: public-safe
---

# Compiler Plane

The compiler plane is one of four Dory memory kernel planes. It encompasses all
jobs that transform evidence into reviewable artifacts and, optionally, canonical
memory.

## Pipeline Contract

Every compiler job follows the same general pipeline:

```
input evidence -> derived candidate -> reviewable proposal/artifact -> optional canonical promotion
```

Each stage is a typed boundary:

| Stage | Description | Example |
|---|---|---|
| Input evidence | Primary source material (raw sessions, canonical pages, recall events) | `logs/sessions/codex/2026-04-11.md` |
| Derived candidate | A processed, summarized, or compiled intermediate artifact | `inbox/distilled/codex-2026-04-11.md` |
| Reviewable artifact | An artifact that can be reviewed before taking effect | `inbox/proposed/codex-2026-04-11.json` |
| Canonical promotion | A reviewed artifact written to durable canonical memory | `people/avery.md`, `decisions/xyz.md` |

Not every pipeline goes through all four stages. Some stop at the reviewable
artifact stage (e.g., maintenance reports, health reports). Some skip directly
from input to compiled output (e.g., wiki refresh from canonical pages).

## Rules

1. **Compiler jobs can be async or scheduled.** Every pipeline declared as
   `is_schedulable=True` can be run on a cron expression or triggered by file
   system watches (`ops watch`).

2. **Compiler jobs produce reviewable artifacts.** Outputs that modify durable
   memory must pass through `inbox/proposed/` as reviewable proposal JSON files.
   The `proposal_apply` pipeline is the only path from proposal to canonical.

3. **No silent canonical rewrite from raw sessions.** Session content is indexed
   for search but never automatically promoted to canonical memory. The path from
   session to canonical is:
   ```
   raw session -> [distillation] -> distilled note -> [proposal generation] -> proposal -> [apply] -> canonical
   ```
   Every step requires explicit action (CLI command or proposal review).

4. **No hot-path LLM dependency.** Deterministic pipelines (wiki refresh, wiki
   health, recall promotion, session ingest) never call an LLM. LLM-dependent
   pipelines (distillation, proposal generation, digests, maintenance reports)
   must be triggered explicitly by the operator or a schedule.

5. **Context fencing prevents feedback loops.** Compiler artifacts
   (`inbox/distilled/`, `inbox/proposed/`, `wiki/`, `digests/`) are blocked from
   re-ingestion as evidence. The `context_fence_for_ingest()` function in
   `compiler.py` checks paths and returns a warning if re-ingestion would create
   a feedback loop.

## Pipeline Inventory

### Session Distillation

| Field | Value |
|---|---|
| Pipeline | `session_distillation` |
| Input | Raw session markdown files (`logs/sessions/**/*.md`) |
| Output | Distilled markdown notes (`inbox/distilled/{agent}-{session-name}.md`) |
| LLM | Yes (OpenRouter) |
| Reviewable | Yes |
| CLI | `ops dream-once --session <path>` |

Summarizes session text into key facts, decisions, follow-ups, and entity
references. The output frontmatter marks `source_kind: distilled` and
references the original session path.

### Proposal Generation

| Field | Value |
|---|---|
| Pipeline | `proposal_generation` |
| Input | Distilled notes, daily/weekly digests, recall-promotion notes |
| Output | Proposal JSON (`inbox/proposed/{name}.json`) |
| LLM | Yes (OpenRouter) |
| Reviewable | Yes (must be applied or rejected) |
| CLI | `ops dream-once` |

Converts distilled notes into typed write/replace/forget actions. Each action
is scoped to a subject, has a confidence level, and is grounded in the source
note. Proposals sit in `inbox/proposed/` until explicitly applied or rejected.

### Daily Digest

| Field | Value |
|---|---|
| Pipeline | `daily_digest` |
| Input | Session logs for a day |
| Output | Daily digest markdown (`digests/daily/{date}.md`) |
| LLM | Yes (OpenRouter) |
| Reviewable | Yes |
| CLI | `ops daily-digest-once` |

Compiles session activity for a single date into a structured daily digest
with decisions, key outcomes, and follow-ups.

### Weekly Digest

| Field | Value |
|---|---|
| Pipeline | `weekly_digest` |
| Input | Daily digests for an ISO week |
| Output | Weekly digest markdown (`digests/weekly/{week}.md`) |
| LLM | Yes (OpenRouter) |
| Reviewable | Yes |
| CLI | `ops weekly-digest-once --week <week>` |

Aggregates daily digest content into a weekly overview. Consumes daily digest
markdown files, not raw sessions.

### Wiki Refresh

| Field | Value |
|---|---|
| Pipeline | `wiki_refresh` |
| Input | Canonical pages (`core/active.md`, `people/*.md`, `projects/*/state.md`, `concepts/*.md`, `decisions/*.md`) + claim store |
| Output | Compiled wiki pages (`wiki/people/*.md`, `wiki/projects/*.md`, `wiki/concepts/*.md`, `wiki/decisions/*.md`) |
| LLM | No |
| Reviewable | No (deterministic render) |
| CLI | `ops wiki-refresh-once` |

Deterministically regenerates compiled wiki pages from canonical sources and
the claim store. Prunes stale generated pages. Also refreshes wiki index pages
(people/projects/concepts/decisions indexes).

### Wiki Index Refresh

| Field | Value |
|---|---|
| Pipeline | `wiki_index_refresh` |
| Input | Wiki pages |
| Output | Wiki index pages (`wiki/index.md`, `wiki/hot.md`, `wiki/log.md`, family indexes) |
| LLM | No |
| Reviewable | No (deterministic render) |
| CLI | `ops wiki-refresh-indexes` |

Regenerates the wiki index pages that aggregate and list wiki content by
family and metadata.

### Wiki Health

| Field | Value |
|---|---|
| Pipeline | `wiki_health` |
| Input | Canonical pages + wiki pages |
| Output | Health inspection report (printed or saved to `inbox/maintenance/`) |
| LLM | No |
| Reviewable | Yes |
| CLI | `ops wiki-health --write-report` |

Inspects corpus health: checks for stale content, missing frontmatter,
orphaned files, and structural issues. No LLM dependency — pure deterministic
inspection.

### Maintenance Report

| Field | Value |
|---|---|
| Pipeline | `maintenance_report` |
| Input | Canonical pages (`core/`, `people/`, `projects/*/state.md`, `concepts/`, `decisions/`) |
| Output | JSON maintenance report (`inbox/maintenance/{path-slug}.json`) |
| LLM | Yes (OpenRouter) |
| Reviewable | Yes |
| CLI | `ops maintain-once --path <path>` |

LLM-driven inspection of specific canonical pages. Suggests type, status,
area, canonical flag, and target path changes. Reports are written to
`inbox/maintenance/` for review.

### Recall Promotion

| Field | Value |
|---|---|
| Pipeline | `recall_promotion` |
| Input | OpenClaw recall events (frequently-recalled paths) |
| Output | Distilled notes (`inbox/distilled/recall-{slug}.md`) |
| LLM | No |
| Reviewable | Yes |
| CLI | Automatic during `ops dream-once` |

Promotes frequently-recalled memory paths into distilled notes. Uses the
OpenClaw recall event store to find candidates that have been recalled
multiple times across distinct queries. The output is consumed by
proposal_generation for optional canonical promotion.

### Session Ingest

| Field | Value |
|---|---|
| Pipeline | `session_ingest` |
| Input | Raw session markdown files |
| Output | Session plane index (SQLite FTS5) |
| LLM | No |
| Reviewable | No (direct index) |
| CLI | `session-ingest` |

Indexes session files into the session plane database for FTS5 search.
Session content is indexed but never silently promoted to canonical memory.
The path to canonical goes through distillation → proposal → apply.

### Proposal Apply

| Field | Value |
|---|---|
| Pipeline | `proposal_apply` |
| Input | Pending proposal JSON (`inbox/proposed/{id}.json`) |
| Output | Canonical memory write + archived proposal (`inbox/applied/{id}.json`) |
| LLM | No |
| Reviewable | No (on-demand execution) |
| CLI | Memory write proposal apply |

Applies an approved proposal to canonical memory. Validates that the proposal's
dry-run still matches the current state before executing. Moves the proposal
from `inbox/proposed/` to `inbox/applied/`.

## Context Fencing

Compiler artifacts must not be re-ingested as primary evidence. The
`compiler.py` module provides these fencing utilities:

- `is_compiler_artifact(rel_path)` — True if path is under `wiki/`, `inbox/`,
  `digests/`, or `eval/runs/`.
- `is_recall_promotion_artifact(rel_path)` — True if path is a recall-promotion
  distilled note (`inbox/distilled/recall-*`).
- `context_fence_for_ingest(rel_path)` — Returns a warning string if the path
  is a compiler artifact that should not be re-ingested, or None if safe.
- `assert_safe_to_ingest(rel_path)` — Raises ValueError if the path is unsafe.

The critical rule: **recalled memory blocks must not be re-ingested as new
facts.** A recall promotion note references existing durable memory; re-ingesting
it would create a feedback loop where recalled content is treated as new evidence.

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        COMPILER PLANE                                │
│                                                                     │
│  Raw Sessions ──► Session Distillation ──► Distilled Note ──┐       │
│                   (LLM)                                     │       │
│                                                             ├─►      │
│  Recall Events ──► Recall Promotion ──► Recall Note ────────┘       │
│                   (deterministic)                                     │
│                                                              ▼      │
│                                                    Proposal Generation│
│  Daily Digests ───────────────────────────────────►   (LLM)          │
│  Weekly Digests ─────────────────────────────────►                   │
│                                                                      │
│                                                              ▼      │
│                                                     Proposal JSON    │
│                                                    (inbox/proposed/) │
│                                                         │            │
│                                              ┌──────────┴──────────┐ │
│                                              ▼                     ▼ │
│                                        Proposal Apply     Proposal    │
│                                        (deterministic)    Reject     │
│                                              │                     │ │
│                                              ▼                     ▼ │
│                                         Canonical             Archived│
│                                         memory               (rejected)│
│                                                                      │
│  Canonical Pages ──► Wiki Refresh ──► Compiled Wiki Pages            │
│  + Claims           (deterministic)     (wiki/)                      │
│                                                                      │
│  Wiki Pages ──► Wiki Index Refresh ──► Index Pages                   │
│                  (deterministic)                                      │
│                                                                      │
│  Canonical Pages ──► Wiki Health / Maintenance Reports               │
│                      (deterministic / LLM)                            │
└─────────────────────────────────────────────────────────────────────┘
```

## Related Code

| File | Purpose |
|---|---|
| `src/dory_core/compiler.py` | Compiler contract types, pipeline registry, context fencing |
| `src/dory_core/ops.py` | Runner classes: `DreamOnceRunner`, `MaintenanceOnceRunner`, `WikiHealthRunner`, `OpsWatchRunner` |
| `src/dory_core/dreaming/extract.py` | Session distillation: `LLMSessionDistiller`, `DistillationWriter` |
| `src/dory_core/dreaming/proposals.py` | Proposal generation: `ProposalGenerator`, `ProposalStore`, `apply_proposal`, `reject_proposal` |
| `src/dory_core/dreaming/recall.py` | Recall promotion: `RecallPromotionRunner`, `RecallPromotionWriter` |
| `src/dory_core/dreaming/events.py` | `SessionClosedEvent` |
| `src/dory_core/digest_writer.py` | `DailyDigestWriter`, `WeeklyDigestWriter`, digest generators |
| `src/dory_core/digests.py` | `DigestReader` |
| `src/dory_core/compiled_wiki.py` | Wiki page compilation from claims |
| `src/dory_core/wiki_indexes.py` | `WikiIndexBuilder` for index page regeneration |
| `src/dory_core/maintenance.py` | `MaintenanceReportWriter`, `MemoryHealthDashboard` |
| `src/dory_cli/commands/ops.py` | CLI commands for all ops jobs |
