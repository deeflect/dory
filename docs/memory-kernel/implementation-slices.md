---
title: Memory Kernel Implementation Slices
status: draft
type: implementation-plan
created: 2026-05-24
scope: public-safe
---

# Memory Kernel Implementation Slices

This is the small-step execution plan. Each slice should be reviewable by Codex on its own.

## Slice 0 — Keep docs and review surface clean

**Goal:** Make the repo easy to review before new code.

**Files:**

- `docs/memory-kernel/*`
- `docs/archive/2026-05-refactor-planning/*`
- `docs/operations/private-public-release-workflow.md`
- `README.md`
- `docs/current-state/README.md`

**Steps:**

1. Move old broad refactor notes into `docs/archive/2026-05-refactor-planning/`.
2. Add this memory-kernel folder as the current planning surface.
3. Link it from README/current-state docs.
4. Run public-safety scan on docs.
5. Commit as docs-only.

**Done when:** Codex can open one folder, understand the roadmap, and compare old notes only if needed.

## Slice 1 — Kernel contract inventory, no behavior change

**Goal:** Write down the exact internal contracts that already exist.

**Create:**

- `docs/memory-kernel/kernel-contract.md`

**Inventory:**

- `src/dory_core/entity_registry.py`
- `src/dory_core/claim_store.py`
- `src/dory_core/semantic_write.py`
- `src/dory_core/active_memory.py`
- `src/dory_core/wake.py`
- `src/dory_core/search.py` or `src/dory_core/search/`
- `src/dory_core/session_ingest.py`
- `src/dory_core/compiled_wiki.py`
- `src/dory_core/retrieval_planner.py`

**Output:**

- Current request/response objects.
- What is canonical vs derived.
- Which paths/tables are source of truth.
- Which functions are safe to call from HTTP/MCP/CLI.
- What is missing for observations/relationships/hot context.

**Validation:**

```bash
uv run ruff check . --select E,F
uv run pytest -q tests/unit/test_semantic_write.py tests/unit/test_active_memory.py tests/unit/test_query_planner_toggle.py
```

## Slice 2 — Entity lookup packet for active memory

**Goal:** Add a tiny deterministic entity/project lookup packet before broader search.

**Behavior:**

- Given `project`, `cwd`, or obvious entity mention in prompt, resolve entity through existing `EntityRegistry`.
- If matched, return a small `EntityContext` packet with:
  - `entity_id`
  - `canonical_name`
  - `type`
  - `canonical_path`
  - `matched_by`
  - `source_refs`
- Do not add new LLM behavior.
- Do not create entities during retrieval.

**Likely files:**

- `src/dory_core/entity_context.py` new
- `src/dory_core/active_memory.py`
- `src/dory_core/wake.py`
- tests under `tests/unit/`

**Validation:**

```bash
uv run pytest -q tests/unit/test_entity_registry.py tests/unit/test_active_memory.py
uv run python -c "from dory_core.entity_context import EntityContext; print('OK')"
```

## Slice 3 — Observation model as an index over evidence

**Goal:** Add observations without inventing a second canonical memory store.

**Behavior:**

- Observations point to source evidence.
- Observations can be regenerated from evidence.
- Claims remain the durable active-truth layer.
- Markdown remains canonical human-editable memory.

**Likely files:**

- `src/dory_core/observations.py` new
- migration/schema for SQLite observation index if needed
- tests for create/list/supersede/rebuild behavior

**Minimal schema:**

```text
Observation(id, entity_id, kind, content, confidence, freshness, status, created_at, observed_at)
ObservationSource(observation_id, path, line_start, line_end, hash, quote)
```

**Validation:**

```bash
uv run pytest -q tests/unit/test_observations.py
uv run ruff check . --select E,F
```

## Slice 4 — Retrieval planner emits typed kernel attempts

**Goal:** Make the planner output execution attempts instead of vague query strings.

**Behavior:**

Planner may emit:

- `entity_lookup`
- `claim_lookup`
- `observation_lookup`
- `session_recall`
- `durable_search`
- `link_neighbors`

Execution remains deterministic and budgeted. Planner failure falls back to existing deterministic behavior.

**Likely files:**

- `src/dory_core/retrieval_planner.py`
- `src/dory_core/active_memory.py`
- `src/dory_core/search.py` or `src/dory_core/search/*`
- tests for strict schema and fallback

**Validation:**

```bash
uv run pytest -q tests/unit/test_query_planner_toggle.py tests/unit/test_active_memory.py
```

## Slice 5 — Hot context packet builder

**Goal:** One internal builder for wake/active-memory/client context.

**Behavior:**

Build ordered packets:

1. profile guardrails
2. project/entity context
3. active claims
4. recent/session evidence
5. durable search evidence
6. source links and warnings

**Likely files:**

- `src/dory_core/hot_context.py` new
- `src/dory_core/wake.py`
- `src/dory_core/active_memory.py`
- HTTP/MCP adapters only if response shape needs metadata

**Validation:**

```bash
uv run pytest -q tests/unit/test_wake_builder.py tests/unit/test_active_memory.py tests/integration/core/test_active_memory_flow.py
```

## Slice 6 — Compiler job skeleton

**Goal:** Prepare background observation/card generation without running it on every turn.

**Behavior:**

- CLI/ops command can run one compiler pass.
- Reads recent session/evidence entries.
- Produces proposed observations/cards with source refs.
- Does not auto-promote to canonical markdown unless explicitly allowed.

**Likely files:**

- `src/dory_core/compiler.py` new
- `src/dory_core/ops.py`
- CLI ops command
- tests with synthetic corpus/session evidence

**Validation:**

```bash
uv run pytest -q tests/unit/test_compiler.py tests/integration/core/test_ops_*.py
```

## Slice 7 — Cleanup/de-bloat only after contracts stabilize

**Goal:** Split large modules only when behavior has tests and contract docs.

Candidates:

- split `search.py`
- consolidate runtime builders into one `DoryRuntime`
- finish Hermes provider split if not already complete
- move migration-only modules out of hot runtime imports

**Rule:** pure extraction first, behavior change second.

## Universal gates

Run for every code slice:

```bash
uv run ruff check . --select E,F
uv run ruff check .
uv run python scripts/release/check-public-safety.py --path docs src tests plugins packages scripts
uv run pytest -q
```

For full-suite file descriptor issues, raise the shell limit first:

```bash
ulimit -n 4096
uv run pytest -q
```
