# Dory Refactor — Implementation Sequencing Plan

**Status:** Plan (no file edits yet)
**Created:** 2026-05-22
**Scope:** public-safe
**Based on:** `docs/dory-refactor-synthesis.md` + live code inspection of `src/dory_core/`, `src/dory_cli/`, `src/dory_http/`, `src/dory_mcp/`, `plugins/hermes-dory/`, and `tests/`.

---

## Current Code Reality (Measured)

| File | Lines | Problem |
|------|-------|---------|
| `src/dory_core/search.py` | 1,556 | Monolithic: FTS building, mode routing, scoring, dedup, hardcoded priors, confidence, session fallback, rerank orchestration |
| `src/dory_http/app.py` | 1,081 | Contains `HttpRuntime` — duplicates builder logic from `runtime.py` |
| `src/dory_mcp/server.py` | 604 | Contains `RuntimeCore` — duplicates builder logic from `runtime.py` |
| `src/dory_cli/main.py` | 1,585 | Monolithic Typer app; imports via `_internals.py` |
| `plugins/hermes-dory/provider.py` | 2,221 | Monolithic: config + client + schemas + tools + provider |
| `src/dory_core/active_memory.py` | 1,206 | Still has timeout fragility, hardcoded snippet sizes |
| `src/dory_core/semantic_write.py` | 887 | Imports `migration_normalize` helpers |
| `src/dory_core/ops.py` | 584 | **Core→CLI violation:** `from dory_cli.eval import run_eval` |
| `src/dory_core/migration_*.py` | 16 files | Bloat hot core, some helpers used by `semantic_write.py` |
| `src/dory_core/retrieval_planner.py` | 391 | **Already exists** — typed, frozen, schemas, fallbacks |
| `src/dory_core/slug.py` | 17 | **Already extracted** from migration code |
| `src/dory_core/query_expansion.py` | 75 | **Already extracted** — small, standalone |
| `src/dory_core/runtime.py` | 98 | `SurfaceRuntime` exists but is NOT yet consumed by all surfaces |
| `src/dory_core/compiled_wiki.py` | 228 | Already exists |
| `src/dory_core/claim_store.py` | 452 | Already exists |
| Tests | 808 | Healthy baseline |

---

## Guiding Principles for Sequencing

1. **Lowest-risk first.** Pure extraction/move operations before behavioral changes.
2. **No broad rewrites.** Every task targets specific files, keeps imports working at each step.
3. **Testable gates.** Every task must pass `uv run pytest -q` (or the relevant subset) before proceeding.
4. **Subagent-suitable.** Each task is self-contained with clear inputs, outputs, and verification commands.
5. **Dependency order.** Task N+1 depends on Task N passing.

---

## Implementation Sequence (12 Tasks)

### Phase 0 — Safety & Hygiene (Tasks 1–3)

#### Task 1: Fix `core → CLI` Import Violation

**Problem:** `src/dory_core/ops.py` line 13: `from dory_cli.eval import run_eval` creates a circular-ish dependency where `dory_core` imports `dory_cli`.

**Solution:** Extract the eval runner that `dory_core` needs into a small neutral module that `dory_cli.eval` and `dory_core.ops` can both import. The simplest approach: move `run_eval` into `src/dory_core/eval_runner.py` (or a neutral `_eval.py`), have `dory_cli.eval` re-export it. If that's too tangled, have `ops.py` accept the eval function via dependency injection instead of a top-level import.

**Files touched:**
- `src/dory_core/ops.py` — remove `from dory_cli.eval import run_eval`; import from new neutral location or inject
- `src/dory_core/eval_runner.py` — **new file** (if move approach)
- `src/dory_cli/eval.py` — re-export from new location
- Maybe `src/dory_core/__init__.py` — if needed

**Verification gate:**
```bash
uv run pytest -q tests/unit/ tests/integration/core/ tests/integration/cli/
# Specifically:
uv run python -c "from dory_core.ops import EvalOnceRunner; print('OK')"
```

**Risk:** Low. Pure refactor. If `dory_cli.eval` has deep imports into private runtime internals, the injection approach is safer.

---

#### Task 2: Unknown Profile Fails Closed

**Problem:** `wake(profile="nonexistent")` silently returns personal/core context instead of erroring.

**Solution:** In `src/dory_core/profiles.py` and/or `src/dory_core/wake.py`, add validation that unknown profile strings raise `DoryValidationError` (or return a minimal safe profile). Identify where the fallback-to-default happens and insert a guard.

**Files touched:**
- `src/dory_core/profiles.py` — `ProfileRegistry.lookup()` or similar
- `src/dory_core/wake.py` — wake builder entry point
- `src/dory_core/types.py` — possible new error type

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_profiles.py tests/integration/core/test_wake_builder.py
uv run python -c "
from dory_core.profiles import ProfileRegistry
r = ProfileRegistry()
try:
    r.lookup('nonexistent_profile')
    print('FAIL: should have raised')
except Exception as e:
    print(f'OK: {type(e).__name__}')
"
```

**Risk:** Low. Clear expected behavior in synthesis doc.

---

#### Task 3: Extract Migration Helpers from Hot Core

**Problem:** `semantic_write.py` imports `canonical_target_for_subject` and `normalize_migration_slug` from `dory_core.migration_normalize`. These are general-purpose slug/path helpers that live inside migration code.

**Solution:** Move those two functions from `dory_core.migration_normalize` into `dory_core.slug` (which already exists with `slugify_path_segment`). Update `migration_normalize` to import from the new location. Update `semantic_write.py` import.

**Files touched:**
- `src/dory_core/slug.py` — add `normalize_migration_slug`, `canonical_target_for_subject`
- `src/dory_core/migration_normalize.py` — re-export from `dory_core.slug`
- `src/dory_core/semantic_write.py` — update import

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_semantic_write.py
uv run python -c "
from dory_core.slug import normalize_migration_slug, canonical_target_for_subject
print(f'normalize: {normalize_migration_slug(\"Hello World\")}')
print(f'target: {canonical_target_for_subject(\"project:test\")}')
"
```

**Risk:** Very low. Pure import relocation.

---

### Phase 1 — De-Bloat Core (Tasks 4–7)

#### Task 4: Split `search.py` into `search/` Package

**Problem:** `search.py` (1,556 lines) mixes FTS query building, mode routing, scoring, dedup, hardcoded path priors, confidence logic, session search, rerank orchestration.

**Solution:** Extract into `src/dory_core/search/` package:

```
src/dory_core/search/
  __init__.py        ← re-exports SearchEngine, public API
  engine.py          ← SearchEngine class (search(), _search_*, etc.)
  fts.py             ← _build_fts_query, FTS helpers
  scoring.py         ← merge_rankings, _fuse_scores, path boosts, confidence
  dedup.py           ← deduplication helpers
  rerank.py          ← rerank integration (or keep in engine.py if small)
  session.py         ← session fallback search logic
```

**Critical constraint:** Keep `SearchEngine` class signature and all public exports identical. The `search.py` `__init__.py` must re-export everything that current importers expect.

**Files touched:**
- Create `src/dory_core/search/`
- `src/dory_core/search.py` → becomes `src/dory_core/search/__init__.py` (re-export) + individual modules
- Update any `from dory_core.search import X` across codebase (there are many)
- Update `src/dory_core/runtime.py`

**Files that import from `dory_core.search` (must verify all):**
- `src/dory_core/runtime.py`
- `src/dory_core/active_memory.py`
- `src/dory_core/ops.py`
- `src/dory_core/research.py` (if it exists)
- `src/dory_http/app.py`
- `src/dory_mcp/server.py`
- `src/dory_cli/_internals.py`
- Various test files

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_search_*.py tests/integration/core/test_search_*.py
uv run python -c "
from dory_core.search import SearchEngine, merge_rankings, SearchMode
print('SearchEngine:', SearchEngine)
print('merge_rankings:', merge_rankings)
"
# Then run full test suite
uv run pytest -q
```

**Risk:** Medium — many importers. Must be done carefully with `__init__.py` re-exports. Use the "move file → create `__init__.py` that re-exports from new module → one by one extract into sub-modules" pattern. Never break the external API.

---

#### Task 5: Remove Hardcoded Path Priors from search

**Problem:** `search.py` has `_CURRENT_QUERY_TOKENS`, `_ENV_QUERY_TOKENS`, `_PRIVACY_QUERY_TOKENS` etc. — keyword-based heuristic routing.

**Solution:** After Task 4, extract these into a `search/policies.py` or `search/priors.py` module. Mark them as deprecated with a code comment pointing to the retrieval planner as the replacement. Do NOT change behavior yet — just isolate the code so it's easy to remove later.

**Files touched:**
- `src/dory_core/search/policies.py` — **new file**, move heuristic sets + _build_query_profile + path penalty/boost logic
- `src/dory_core/search/engine.py` — update import

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_search_*.py tests/integration/core/test_search_*.py
```

**Risk:** Very low if done as pure extraction (no behavior change).

---

#### Task 6: Create Unified `DoryRuntime`

**Problem:** `runtime.py` has `SurfaceRuntime` but HTTP/MCP/CLI each duplicate builder logic.

**Solution:** Promote `SurfaceRuntime` → `DoryRuntime` in `src/dory_core/runtime.py`. Consolidate everything it needs. Update `build_surface_runtime` → `build_dory_runtime`. Have HTTP and MCP use `build_dory_runtime` instead of building their own components.

**Files touched:**
- `src/dory_core/runtime.py` — rename, add missing fields (link service, artifact writer, etc.)
- `src/dory_http/app.py` — use `build_dory_runtime`, remove redundant builder code
- `src/dory_mcp/server.py` — use `build_dory_runtime`, remove `RuntimeCore`
- `src/dory_cli/_internals.py` — optionally use `build_dory_runtime`

**Verification gate:**
```bash
uv run pytest -q tests/integration/http/ tests/integration/mcp/ tests/unit/
# Start HTTP daemon briefly to verify it boots:
uv run python -c "
from dory_core.runtime import build_dory_runtime
from dory_core.config import DorySettings
rt = build_dory_runtime(corpus_root='/tmp/test-dory', index_root='/tmp/test-dory/.dory/index')
print('Runtime OK:', type(rt).__name__)
"
```

**Risk:** Medium. HTTP and MCP daemons have different dependencies (link service, artifact writer, purge engine, research engine). Must ensure `DoryRuntime` covers all surfaces.

---

#### Task 7: Split `active_memory.py`'s Timeout Logic

**Problem:** `active_memory.py` (1,206 lines) has hardcoded timeout stages that can still fail.

**Solution:** Extract budget/stage constants into a config object. Add `partial_ok` mode: if a stage times out, return whatever context was gathered so far with a warning instead of raising.

**Files touched:**
- `src/dory_core/active_memory.py` — refactor timeout into `BudgetConfig`, add `partial_ok` flag
- Possibly `src/dory_core/types.py` — add `partial: bool` to `ActiveMemoryResp`

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_active_memory.py tests/integration/http/test_active_memory_http.py
```

**Risk:** Low-Medium. Adding a `partial_ok` mode is backwards compatible.

---

### Phase 2 — Vertical Slice: Compiled Cards + Retrieval Planner (Tasks 8–10)

#### Task 8: Wire Retrieval Planner into Active Memory Default Path

**Problem:** `retrieval_planner.py` exists (391 lines, typed, schemas) but active memory only uses it optionally (controlled by `active_memory_llm_stages` config). The default fallback is `fallback_active_memory_plan()` which uses keyword matching (`_active_memory_prompt_needs_sessions`).

**Solution:** Make the retrieval planner the default for active memory (when an LLM client is available), with clean fallback to existing behavior when no LLM. This is the first vertical slice of the "model-assisted retrieval" vision.

**Files touched:**
- `src/dory_core/llm/active_memory.py` — adjust `build_active_memory_components` defaults
- `src/dory_core/active_memory.py` — ensure `ActiveMemoryEngine` uses planner when available
- `src/dory_core/config.py` — adjust default for `active_memory_llm_stages`

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_active_memory.py tests/unit/test_query_planner_toggle.py
uv run python -c "
from dory_core.config import DorySettings
s = DorySettings()
print('active_memory_llm_stages default:', s.active_memory_llm_stages)
"
```

**Risk:** Low. The planner already exists and has fallbacks. Just changing the default priority.

---

#### Task 9: Add Project Card / Compiled Card Retrieval to Wake

**Problem:** `wake` currently searches raw markdown. The vision says wake should first check compiled cards/mental models.

**Solution:** In `src/dory_core/wake.py`, add a new step at the start of the wake builder: check `wiki/projects/`, `wiki/people/`, `wiki/concepts/` for pre-compiled cards matching the profile. If found, include them as high-priority context before doing full search. This is the minimal vertical slice of L0/L1 tiered retrieval.

**Files touched:**
- `src/dory_core/wake.py` — add `_collect_compiled_cards(profile)` step
- `src/dory_core/compiled_wiki.py` — may need a lookup helper
- Tests for wake behavior

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_wake_budget.py tests/integration/core/test_compiled_wiki_search.py
uv run python -c "
from dory_core.wake import WakeBuilder
from pathlib import Path
wb = WakeBuilder(Path('/tmp/test-dory'))
# Just verify it doesn't crash when compiled wiki exists
print('WakeBuilder loaded')
"
```

**Risk:** Medium. Changes to wake path ordering could affect downstream behavior. Must be additive only — existing wake behavior preserved when no compiled cards exist.

---

#### Task 10: Budget-First Active Memory Stages

**Problem:** Active memory doesn't have explicit per-stage budgets (0–100ms cards, 100–800ms BM25, etc. per the synthesis).

**Solution:** Implement the budget-first stages from the synthesis doc. Use `time.perf_counter()` to enforce stage cutoffs. Return partial context with warnings if budget exhausted.

```
0–100ms:    profile/project cards (compiled)
100–800ms:  scoped BM25/exact search
800–2500ms: optional vector search
2500–4000ms: optional rerank
4000–5000ms: compose / fallback
```

**Files touched:**
- `src/dory_core/active_memory.py` — major change to `ActiveMemoryEngine` run loop
- `src/dory_core/types.py` — `ActiveMemoryResp` may need `partial_warning` field

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_active_memory.py tests/integration/http/test_active_memory_http.py
uv run python -c "
from dory_core.active_memory import ActiveMemoryEngine
from dory_core.config import DorySettings
# Verify budget constants are accessible
print('Active memory module loaded OK')
"
```

**Risk:** Medium. Budget changes could cause regressions if stage cutoffs are too aggressive. Start with generous defaults.

---

### Phase 3 — Surface Splits (Tasks 11–12)

#### Task 11: Split Hermes Provider

**Problem:** `plugins/hermes-dory/provider.py` (2,221 lines) — config, client, schemas, tools, provider all in one file.

**Solution:** Split into:
```
plugins/hermes-dory/
  __init__.py    ← re-export HermesDoryMemoryProvider
  config.py      ← HermesDoryProviderConfig, env parsing
  client.py      ← HTTP client wrapper
  schemas.py     ← tool schemas, type aliases
  tools.py       ← tool handler implementations
  provider.py    ← HermesDoryMemoryProvider class (imports from siblings)
```

**Files touched:**
- Create `plugins/hermes-dory/config.py`, `client.py`, `schemas.py`, `tools.py`
- Modify `plugins/hermes-dory/__init__.py`
- Modify `plugins/hermes-dory/provider.py` — thin down to just the provider class

**Verification gate:**
```bash
uv run pytest -q tests/unit/test_hermes_provider_config.py
uv run python -c "
from plugins.hermes_dory import HermesDoryMemoryProvider
print('Provider import OK:', HermesDoryMemoryProvider)
"
```

**Risk:** Low-Medium. Many internal imports. Must carefully preserve all public API exports.

---

#### Task 12: Split CLI

**Problem:** `src/dory_cli/main.py` (1,585 lines) + `src/dory_cli/_internals.py`.

**Solution:** Split into command modules:
```
src/dory_cli/
  __init__.py
  main.py            ← Typer app (thin), imports commands
  commands/
    __init__.py
    search.py
    wake.py
    write.py
    admin.py
    migration.py
    research.py
```

**Files touched:**
- Create `src/dory_cli/commands/` package
- Move command groups from `main.py` into command modules
- Keep `main.py` as thin app with `app.add_typer()` for each subcommand
- Update `_internals.py` if needed

**Verification gate:**
```bash
uv run python -m dory_cli.main --help
uv run python -m dory_cli.main search --help
uv run python -m dory_cli.main wake --help
uv run pytest -q tests/integration/cli/
```

**Risk:** Low-Medium. CLI commands are mostly self-contained. The main risk is if command handlers share a lot of mutable state via `_internals.py`.

---

## Dependency Graph

```
Task 1 (core→CLI fix)
  └── Task 4 (split search.py) — avoids dragging CLI dependency
      └── Task 5 (extract priors)
Task 2 (unknown profile)
  └── Task 10 (budget-first active memory) — safety baseline
Task 3 (migration helpers)
  └── no downstream deps
Task 6 (unified DoryRuntime)
  depends on: Task 1, Task 4 (for clean imports)
  └── Task 7 (active memory timeout)
Task 8 (wire retrieval planner)
  depends on: Task 6 (needs unified runtime)
  └── Task 9 (compiled card wake)
  └── Task 10 (budget-first stages)
Task 11 (split Hermes) — independent of core changes
Task 12 (split CLI) — independent but benefits from Task 1
```

**Parallelizable groups:**
- Group A: Tasks 1, 2, 3 (no deps between them)
- Group B: Tasks 4, 5 (Task 5 after Task 4)
- Group C: Tasks 6, 11, 12 (after Task 1, 4)
- Group D: Tasks 7, 8 (after Task 6)
- Group E: Tasks 9, 10 (after Task 7, 8)

---

## Per-Task Subagent Briefing Template

Each task should be assigned to a subagent with:

```markdown
## Task N: [Title]

**Goal:** [one-sentence goal]
**Risk:** Low / Medium / High
**Input files:** [list of files to read before starting]
**Files to modify:** [list of files to change or create]
**Exit criteria:**
1. [command] passes
2. [command] passes
3. No new lint warnings (uv run ruff check .)
4. No public API breakage (verify with import test)

**Context:**
- [Relevant code structure facts]
- [Key design constraints]
- [What NOT to do]
```

---

## Rollback & Safety Strategy

1. **Each task commits** (or would commit) independently — no mega-changesets.
2. **Before each task**, run `uv run pytest -q` to confirm baseline is green.
3. **After each task**, run `uv run pytest -q` again. If tests fail, the task is reverted/refined.
4. **Lint before commit:** `uv run ruff check .`
5. **Leak check for public safety:** `uv run python scripts/release/check-public-safety.py`
6. **If a task touches migration/ writes, run:** `uv run python scripts/release/check-public-safety.py --path dist`
7. **Any behavioral change must add or update a test** — no silent behavior drifts.

---

## What This Plan Does NOT Do

- ❌ Does not implement the new data model (Fact, Observation, Entity) — that's Phase 3 in the synthesis
- ❌ Does not implement async compiler worker — Phase 3+
- ❌ Does not implement L0/L1/L2 tiered summaries for all docs — Phase 4
- ❌ Does not implement project context tree with content-hash snapshots — Phase 5
- ❌ Does not add archive ghost cues — Phase 5
- ❌ Does not rewrite search from scratch — it splits and isolates, preserving behavior
- ❌ Does not add the full `RetrievalPlan` with typed planes — it wires the existing planner into the default path

The plan targets the **highest-ROI low-risk changes** that unblock deeper work, while keeping the system passing all 808 tests at every step.
