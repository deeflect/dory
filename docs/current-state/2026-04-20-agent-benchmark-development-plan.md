---
title: Agent Benchmark Development Plan
type: report
status: draft
canonical: false
source_kind: human
created: 2026-04-20
updated: 2026-04-20
---

# Agent Benchmark Development Plan

Temporary working handoff for the Codex, Claude, OpenClaw, and Hermes benchmark reports from 2026-04-20. This exists so the issue list and development plan survive context compaction. It should be replaced by issues or merged into current-state docs after fixes land.

## Executive Summary

Dory is usable across MCP, OpenClaw, and Hermes. The strongest parts are exact reads, canonical semantic write guards, force-inbox previews, exact search, wake profiles, and the current HTTP/MCP/plugin surface. The remaining work is not endpoint availability; it is retrieval policy, schema fidelity, privacy-aware ranking, active-memory focus, link output control, and Hermes operational hardening.

Primary risks:

- Default search still lets session transcripts or generated mirrors outrank canonical docs on broad project and privacy queries.
- Privacy boundaries are represented in wake/profile behavior, but not enforced strongly enough in generic search ranking and corpus metadata.
- `active_memory` can still pull unrelated recent/wiki/helper context into focused coding tasks.
- Native MCP `dory_write` schema is less precise than server validation; Hermes schema is better than native MCP here.
- `dory_link` can return huge edge sets with no max/filter knobs.
- Hermes mirror file can grow unbounded and flattens error types.

Do not treat this as a public-release blocker list only. Some items are corpus hygiene, some are product behavior, and some are docs/schema polish.

## Benchmark Inputs

- Codex MCP benchmark: live MCP only, no live writes, found session-ranking noise, active-memory noise, link bloat, exact-write schema/documentation friction.
- Claude MCP benchmark: live MCP only, found strong write safety, session/generator ranking issues, duplicated canonical/wiki/source snippets, missing private frontmatter gates, native `dory_write` schema drift.
- OpenClaw benchmark: live memory plugin works and is useful, but source-level audit was not possible from that harness; broad searches still favor sessions.
- Hermes benchmark: provider shape/config/hooks are broadly correct; deployed mode is tools-only; mirror appends to one inbox file indefinitely; errors are flattened.

## Current Code Reality Checked

Implemented surfaces that are still implicated by the reports:

These are not "done" verdicts. They mean the field/endpoint/guard exists in current code, so the fix should target behavior, ranking policy, schema fidelity, docs, or corpus hygiene instead of re-adding the same surface.

- `SearchResult` includes `score_normalized`, `rank_score`, `evidence_class`, and `confidence` in [src/dory_core/types.py](src/dory_core/types.py).
- Search suppresses retired/quarantine docs and dedupes multiple chunks from the same path in [src/dory_core/search.py](src/dory_core/search.py).
- Exact search exists and is tested for cleanup markers.
- Privacy queries have an initial canonical-boundary prior, but live benchmarks show it is not strong enough against sessions and raw logs.
- Semantic memory writes expose `dry_run`, `force_inbox`, and `allow_canonical`, and canonical routing is guarded.
- `active_memory` supports `include_wake=false` and profile policies in [src/dory_core/active_memory.py](src/dory_core/active_memory.py).
- Hermes tool schema already exposes `dory_write.kind` enum, but native MCP schema does not.
- Hermes README already mentions `on_session_end` and `on_memory_write`; older benchmark note may be stale.

Local uncommitted changes unrelated to this plan:

- [docker-compose.yml](docker-compose.yml)
- [.env.example](.env.example)

## Issue Register

### P0 - Canonical Search Must Beat Session Noise By Default

Status: fixed in this pass for default merge policy; live Zima smoke passed; external harness re-run pending.

Reports:

- Codex: `Dory Docker ZimaBoard deployment` returned a session transcript first, then canonical docs.
- Claude: session logs climb to `rank_score=1.0` on generic Dory-meta queries, including benchmark prompt recursion.
- OpenClaw: broad `Docker MCP` queries pull sessions aggressively.

Root cause hypothesis:

- Durable search and session-plane search are merged by `_merge_with_session_results` in [src/dory_core/search.py](src/dory_core/search.py). The merge score uses position plus exact coverage, but no meaningful source penalty/bonus. Session fallback also deliberately injects a session if one is close to the cutoff.
- Session results get normalized rank scores after merge, so clients see session rows as equally authoritative unless they inspect `evidence_class`.

Fix plan:

- Add explicit source priors in `_merge_result_score`: canonical/core/project-state positive, generated/wiki neutral or slight negative, session negative unless query is session/temporal/recent.
- Change the forced-session injection rule so it only appends a session for temporal/session queries or when durable results are weak/empty.
- Add a request-level control later if needed: `include_sessions=auto|never|always`, but first fix default policy without API churn.
- Ensure `corpus="sessions"` and `mode="recall"` still return sessions directly for recent-work workflows.

Acceptance tests:

- `SearchReq(query="Dory Docker deployment", corpus="all", mode="hybrid")` ranks `core/env.md` or `projects/dory/state.md` before `logs/sessions/**`.
- `SearchReq(query="what did I work on last", corpus="all", mode="hybrid")` can still include sessions near the top.
- Existing session fallback tests continue to pass or are updated to reflect explicit temporal/session intent.

Implemented:

- Added source priors in `src/dory_core/search.py` so canonical/core/project-state results beat sessions on broad queries.
- Session tail evidence is still included for `corpus="all"` when there is room, but it is not promoted above canonical docs.
- Added regression coverage in `tests/integration/core/test_session_fallback_search.py`.

Live verification:

- `Dory Docker ZimaBoard deployment` ranked `core/env.md` first, then canonical/generated Dory docs, with session logs demoted to the tail.
- `current active projects` ranked `core/active.md` first and kept the session hit last.
- `OpenClaw Hermes Dory plugin setup` ranked canonical setup/env/project docs before session evidence.

### P0 - Privacy Queries Need Boundary-First Ranking And Metadata Gates

Status: fixed in ranking and metadata validation; corpus backfill complete on the live private corpus.

Reports:

- Codex: private-boundary queries around money/legal status returned raw session logs, not privacy docs.
- Claude: raw sensitive files lack `visibility: private` or equivalent metadata and can surface freely in generic search.

Root cause hypothesis:

- Durable privacy prior boosts `core/user.md`, `core/identity.md`, `core/defaults.md`, and `core/soul.md`, but session-plane merge can override it.
- Corpus metadata does not consistently label raw personal/sensitive files.
- Search has no profile/client policy that can filter private/raw files for less-trusted clients.

Fix plan:

- Strengthen privacy query priors in `_score_document_prior` and session merge:
  - heavy boost for `core/user.md`, `core/identity.md`, `core/defaults.md`, `core/soul.md`, and explicit privacy/boundary docs.
  - stronger penalty for `logs/sessions/**`, `knowledge/personal-db/**`, raw/imported personal docs, and files with `visibility: private`.
- Add corpus metadata convention:
  - `visibility: private|internal|public`
  - optional `sensitivity: personal|financial|legal|contact|credentials|health|none`
- Update migration/scrub docs and corpus hygiene tools to detect missing visibility on personal/raw paths.
- Do not redact local trusted results by default yet; first make boundary docs rank first and expose `evidence_class`/frontmatter clearly.

Acceptance tests:

- Privacy query with raw sensitive durable doc plus session transcript ranks canonical boundary doc first.
- Search result for private/raw docs carries `frontmatter.visibility` when present and lower confidence/rank unless explicitly scoped.
- `profile=privacy` active-memory never includes raw/session sensitive snippets.

Implemented:

- Privacy merge priors now strongly prefer canonical boundary docs and demote session/raw personal evidence.
- Added regression coverage in `tests/integration/core/test_search_engine.py`.
- Public-facing sensitivity taxonomy uses the broad `legal` category instead of exposing specific private-status labels.

Implemented:

- Added normalized `visibility` and `sensitivity` frontmatter fields.
- `wiki-health` now reports `missing_privacy_metadata` for personal/raw/imported docs.
- Added `dory maintain backfill-privacy-metadata`, dry-run by default, to insert only missing `visibility` / `sensitivity` fields from the latest health report or explicit paths.

Live verification:

- `private boundaries crypto legal status` ranked `core/user.md` first and kept session evidence last.
- Zima `wiki-health --write-report` initially reported `179` files missing privacy metadata and no claim/wiki contradictions, missing evidence, stale pages, or open questions.
- Backed up those `179` files to `inbox/maintenance/backups/privacy-metadata-backfill-20260420.tar.gz`.
- Applied privacy metadata backfill on Zima: `179` changed, `0` skipped, `0` errors.
- Refreshed `wiki-health`: `missing_privacy_metadata=0`, `contradictions=0`, `missing_evidence=0`, `stale_pages=0`, `open_questions=0`.
- Reindexed live corpus after apply: `1418` files, `4313` chunks/vectors, `0` skipped.

### P1 - Active Memory Needs Entity-Scoped Evidence, Not Generic Recent Tail

Status: fixed for deterministic helper/wiki bleed-through; live Zima smoke passed; external harness re-run pending.

Reports:

- Codex: Docker/MCP active_memory pulled unrelated CCC/tweet context.
- Claude: active_memory mixed Dory Docker context with recent marketing/product notes.
- Hermes: `include_wake=true/false` returned identical active-memory blocks in one test, possibly because wake was not actually selected or content did not exercise the flag.

Root cause hypothesis:

- `active_memory` loads wiki helper context (`wiki/hot.md`, `wiki/index.md`) and synthesizes bullets from helper recent pages even when those pages are unrelated to the prompt.
- `_search_candidates` gives path weights but not strong enough entity/topic gating.
- Planner/composer can return useful queries, but deterministic helper bullets can still leak generic active threads.

Fix plan:

- Add entity/topic gating before rendering bullets:
  - infer prompt tokens/entities from query.
  - keep helper recent pages only when they overlap prompt/project tokens or when profile is `general`.
  - for `coding`, prefer `core/active.md`, `core/env.md`, `core/defaults.md`, and `projects/<slug>/state.md`; drop unrelated `knowledge/`, marketing, and generated wiki hits unless lexical overlap is strong.
- Add an `active_memory` diagnostic field later if useful: `dropped_sources` or warnings for excluded low-trust evidence.
- Keep `include_wake=false` behavior; add a targeted test that proves wake text is omitted when durable/session evidence exists and included only when no evidence exists.

Acceptance tests:

- Coding prompt about Dory Docker/MCP does not include unrelated marketing/content project snippets.
- Writing prompt can still retrieve voice docs.
- Privacy prompt returns boundary summary only, no sessions.

Implemented:

- Added topic-scoped helper filtering in `src/dory_core/active_memory.py` for coding/writing profiles.
- Added regression coverage in `tests/unit/test_active_memory.py`.

Live verification:

- Focused coding prompt `Fix Dory Docker MCP deployment and tool schema ranking issues` returned only `projects/dory/state.md` as source, with no unrelated content/writing/marketing tail.

### P1 - Native MCP `dory_write` Schema Must Match Server Validation

Status: fixed.

Reports:

- Codex: exact-path write required `kind=create`, and `type=note` under `inbox/` was rejected; schema did not make this obvious.
- Claude: native MCP schema exposes `kind` as string without enum and does not explain `frontmatter` requirements/path-type constraint.
- Hermes schema already exposes the enum, so the gap is native MCP and docs.

Root cause:

- [src/dory_mcp/tools.py](src/dory_mcp/tools.py) defines `dory_write.kind` as plain string, while [src/dory_core/types.py](src/dory_core/types.py) enforces `append|create|replace|forget`.
- [src/dory_core/write.py](src/dory_core/write.py) requires `frontmatter.title` and `frontmatter.type` for new files and routes/validates path by frontmatter type via `resolve_write_target`.

Fix plan:

- Update native MCP `dory_write` schema:
  - `kind` enum: `append|create|replace|forget`
  - description: create/append to new file requires `frontmatter.title` and `frontmatter.type`
  - describe common path/type pairing: `inbox/**` should use `type: capture`; `references/notes/**` should use `type: note`
  - mention replace/forget require `expected_hash`; forget also requires `reason`.
- Add tests in [tests/integration/mcp/test_tool_schema.py](tests/integration/mcp/test_tool_schema.py).
- Consider a follow-up API improvement: route by path alone for exact-path writes and only warn on type mismatch. For now, schema/docs should match the current validator.

Acceptance tests:

- Tool schema asserts enum and description strings.
- HTTP `/v1/tools` and stdio MCP both expose the updated schema.
- Skills/docs reflect `type: capture` for inbox examples.

Implemented:

- Native MCP schema now exposes exact write kind enum and frontmatter/path-type guidance.
- Updated agent docs and `dory-write` skill.

Live verification:

- Zima `/v1/tools` exposes `dory_write.kind` enum `append|create|replace|forget`.
- Zima `/v1/tools` exposes `dry_run` for `dory_write`.
- Exact-path dry-run create to `inbox/dory-live-check-dry-run.md` returned `action=would_create`, `indexed=false`.

### P1 - Link Output Needs Caps And Filters

Status: fixed.

Reports:

- Codex: `link(projects/dory/state.md)` returned 85 edges and took ~41s, too bloated for normal agent use.

Root cause hypothesis:

- `LinkReq` only has `op/path/direction/depth`; `LinkService.neighbors` returns all collected edges.
- Known-entity edges can be dense for core/project docs.

Fix plan:

- Extend `LinkReq` with:
  - `max_edges: int = 40`
  - `exclude_prefixes: list[str] = []`
  - optional `evidence_class`/`include_generated` later if needed
- Apply cap in `LinkService.neighbors` and include `truncated: true` plus `total_count`.
- Update MCP/HTTP/Hermes/OpenClaw schemas and docs.

Acceptance tests:

- Dense link graph returns at most `max_edges`.
- Response includes total/truncated metadata.
- `depth` behavior remains deterministic.

Implemented:

- `LinkReq` now supports `max_edges` and `exclude_prefixes`.
- `LinkService` returns `count`, `total_count`, and `truncated`.
- HTTP, native MCP, CLI, and Hermes schemas pass the new fields.

Live verification:

- `link(projects/dory/state.md, max_edges=3, exclude_prefixes=["logs/sessions/"])` returned `count=3`, `total_count=15`, and `truncated=true`.

### P1 - Dedup Canonical/Wiki/Source Mirrors In Retrieval Results

Status: fixed for near-duplicate generated/wiki/source mirrors; live Zima smoke partially passed; external harness re-run pending.

Reports:

- Claude: same hardening paragraph appeared across canonical project state, wiki page, hot wiki, and semantic source artifact.
- Codex: generated/session results waste ranking slots.

Root cause hypothesis:

- Current dedupe is path-level only.
- Generated wiki/source files often contain copied canonical snippets and compete with canonical paths.

Fix plan:

- Add content-similarity or canonical-target collapse after scoring but before final `k`.
- Prefer canonical path when snippets are near-duplicates.
- Keep hidden backing evidence later if needed, but do not expose duplicates as separate top-level results.

Acceptance tests:

- Three near-identical docs under `projects/`, `wiki/`, and `sources/semantic/` return the canonical project doc plus distinct alternatives, not all three copies.
- Exact search should not over-collapse unique marker results.

Implemented:

- Non-exact search now collapses near-duplicate generated/wiki/source mirrors behind the preferred canonical document before final `k`.
- Exact search skips duplicate collapse so cleanup marker checks remain literal.

### P2 - Hermes Mirror Needs Rotation/Size Cap

Status: fixed with date partitioning; configurable size caps remain optional future work.

Reports:

- Hermes: `inbox/hermes-memory-mirror.md` appends forever through `on_memory_write`.

Root cause:

- [plugins/hermes-dory/provider.py](plugins/hermes-dory/provider.py) appends every non-user built-in memory write to one fixed file.

Fix plan:

- Change mirror target to a date-partitioned path, for example `inbox/hermes-memory-mirror/YYYY-MM-DD.md`, or rotate by size.
- Add config:
  - `mirror_enabled: bool = true`
  - `mirror_max_bytes: int = 65536`
  - `mirror_path_template: str = "inbox/hermes-memory-mirror/{date}.md"`
- Keep exact-path write frontmatter as `type: capture`.

Acceptance tests:

- `on_memory_write` writes to date-partitioned path by default.
- Large existing mirror uses new target instead of appending indefinitely.
- Token/secrets still never appear in errors/logs.

Implemented:

- Hermes built-in memory mirror target changed to `inbox/hermes-memory-mirror/YYYY-MM-DD.md`.
- Added unit coverage in `tests/unit/test_hermes_provider_config.py`.

### P2 - Hermes Error Types Should Be Structured

Status: fixed for HTTP/tool-call errors.

Reports:

- Hermes: `handle_tool_call` returns `{"ok": false, "error": str(err)}` for all failures.

Root cause:

- Provider `_parse_response` raises `RuntimeError("dory request failed: status body")`; `handle_tool_call` catches broad exceptions and stringifies.

Fix plan:

- Introduce a small provider exception type carrying `status_code`, `error_type`, and message.
- Parse Dory HTTP error JSON when available.
- Return:
  - `ok: false`
  - `error`
  - `error_type: not_found|validation_error|permission_denied|rate_limited|server_error|network_error`
  - `status_code`
- Avoid including Authorization headers or token-bearing URLs.

Acceptance tests:

- 404 maps to `not_found`.
- 400/422 maps to `validation_error`.
- 401/403 maps to `permission_denied`.
- 429 maps to `rate_limited`.
- 5xx maps to `server_error`.

Implemented:

- Hermes provider now raises `DoryProviderError` with `status_code` and `error_type`.
- `handle_tool_call` returns structured error payloads without secrets.

### P2 - Corpus Project State Hygiene

Status: fixed in live Dory corpus.

Reports:

- Codex: `projects/dory/state.md` contains stale/contradictory architecture notes: older LanceDB statements coexist with current SQLite vector-store notes.
- Claude: canonical/wiki/source triplets duplicate text.

Fix plan:

- Treat corpus cleanup separately from code:
  - use Dory exact get and hash-guarded replace.
  - update `projects/dory/state.md` to reflect current SQLite vector store and no LanceDB default if true.
  - preserve timeline history but make compiled/current sections unambiguous.
  - refresh wiki generated pages after canonical cleanup.
- Do not solve this by hiding stale warnings only; canonical docs must become clean.

Acceptance tests:

- Search for Dory architecture returns one current statement.
- Stale timeline entries remain in timeline/history, not current-state summary.

Implemented:

- Replaced `projects/dory/state.md` through Dory with a hash-guarded exact write after benchmarks found stale LanceDB/current SQLite contradictions and eval noise in current sections.
- The current canonical state now describes SQLite FTS5/vector storage, separated session evidence, deterministic search defaults, guarded writes, portable deployment, and pending live deployment/re-benchmarking for this fix pass.

Note:

- The MCP tool schema available in this Codex session did not expose `dry_run` for `dory_write`, so the intended dry-run call executed live. The content was intentional and verified afterward, but this proves already-open client schemas can remain stale and must be restarted after schema changes.

### P2 - OpenClaw Source Audit Gap

Status: documented; external harness still needs re-run.

Reports:

- OpenClaw live plugin works, but the harness could not inspect package source.

Current repo reality:

- Source exists locally under [packages/openclaw-dory](packages/openclaw-dory).
- Local build/tests previously passed.

Fix plan:

- Add or update OpenClaw benchmark instructions so the harness knows where source lives or how to install from package archive.
- Ensure published package includes README, manifest, `dist`, and source map expectations.
- Add live parity checks only if OpenClaw exposes diagnostics.

Acceptance tests:

- `npm install && npm run build` passes.
- Package manifest and `dist/index.js` are in sync with `src/index.ts`.
- Prompt/tool docs point to the right package location.

Implemented:

- `packages/openclaw-dory/README.md` now has a source-audit/benchmark section pointing harnesses to package source, manifest, and dist files.

### P3 - Deployment Config Clarity

Status: documented.

Implemented:

- Hermes config example and getting-started docs now explain `hybrid`, `context`, and `tools` memory modes.

Reports:

- Hermes deployed config has `memory_mode: tools`, so context is not injected automatically.
- This is a deployment choice, not a code bug.

Fix plan:

- Document recommended modes clearly:
  - `tools` for low-bloat manual recall.
  - `hybrid` when auto context injection is desired.
  - `context` for no tools.
- Add a diagnostics line in Hermes status or README: “current memory mode disables prefetch context”.

Acceptance tests:

- README and config example explain the tradeoff.
- Provider `system_prompt_block` reflects current mode accurately.

## Proposed Development Sequence

1. Fix native MCP schema and docs for `dory_write`.
   - Small, low-risk, directly addresses agent UX.
   - Touches `src/dory_mcp/tools.py`, tests, skills/docs.

2. Fix search/session merge ranking.
   - Highest behavior impact.
   - Touches `src/dory_core/search.py` and search/session tests.

3. Add privacy-aware ranking and metadata conventions.
   - Code priors first, corpus metadata hygiene second.
   - Touches search tests, corpus docs, scrub/maintenance docs.

4. Tighten active_memory evidence selection.
   - Add topic/entity gating and tests for focused coding/writing/privacy prompts.

5. Add link caps/filters.
   - API-compatible if fields are optional.
   - Update all schemas and plugin wrappers.

6. Harden Hermes provider.
   - Date-partition mirror path or size cap.
   - Structured error typing.
   - Update tests and README.

7. Corpus cleanup pass.
   - Hash-guarded Dory writes only.
   - Resolve Dory state contradictions and regenerate wiki.

8. Re-run harness benchmarks.
   - Codex/Claude MCP.
   - OpenClaw plugin.
   - Hermes plugin.
   - Compare against scorecard and check for context bloat/regressions.

## Test Matrix

Targeted commands before commit:

```bash
uv run pytest tests/integration/mcp/test_tool_schema.py tests/integration/core/test_search_engine.py tests/integration/core/test_active_memory_flow.py tests/unit/test_active_memory.py -q
uv run pytest tests/unit/test_hermes_provider_config.py tests/integration/http/test_hermes_shim_contract.py -q
cd packages/openclaw-dory && npm install --silent && npm run build --silent
```

Optional broader checks:

```bash
uv run ruff check src tests plugins scripts
uv run pytest -q
git diff --check
```

Live smoke after deploy:

```bash
dory_status
dory_search query="Dory Docker deployment" corpus="all" mode="hybrid" k=5
dory_search query="private boundaries crypto legal status" corpus="all" mode="hybrid" k=5
dory_active_memory prompt="Fix Dory Docker MCP issue" profile="coding" include_wake=false
dory_write kind=create target=inbox/dory-write-schema-smoke.md dry_run=true frontmatter='{"title":"Dory write schema smoke","type":"capture"}'
```

## Done Criteria

- Default broad project searches rank canonical docs before sessions unless the query explicitly asks for recent/session history.
- Privacy queries rank boundary docs before raw sensitive/session content.
- Active memory for focused coding tasks contains no unrelated marketing/writing/project snippets.
- Native MCP, HTTP `/v1/tools`, Claude bridge, Hermes, OpenClaw, skills, and docs agree on write/search/link schemas.
- Link results are bounded by default and report truncation.
- Hermes mirror cannot grow a single unbounded file indefinitely.
- Harness reports show lower context bloat with no loss of useful recall.
