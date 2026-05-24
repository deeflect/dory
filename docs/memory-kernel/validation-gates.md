---
title: Memory Kernel Validation Gates
status: draft
type: validation
created: 2026-05-24
scope: public-safe
---

# Memory Kernel Validation Gates

Use these gates for memory-kernel work. The point is to keep each slice small, safe, and reviewable.

## Docs-only gate

For planning/docs changes:

```bash
uv run python scripts/release/check-public-safety.py --path docs README.md
uv run ruff check . --select E,F
```

Also inspect:

```bash
git diff --stat
git diff -- docs README.md
```

Stop if docs include:

- private hostnames or local LAN details
- real people/contact details
- private corpus excerpts
- tokens/secrets
- absolute local paths that are not generic examples

## Code slice gate

For code changes:

```bash
uv run ruff check . --select E,F
uv run ruff check .
ulimit -n 4096
uv run pytest -q
uv run python scripts/release/check-public-safety.py --path src tests docs plugins packages scripts README.md
```

If a full suite is too slow for an intermediate review, run the targeted tests listed in `implementation-slices.md`, then run the full suite before commit.

## Public API compatibility gate

For HTTP/MCP/Hermes/provider changes:

```bash
uv run pytest -q tests/integration/http tests/integration/mcp tests/integration/acceptance
uv run python -c "import dory_core, dory_cli; print('imports ok')"
```

Keep backwards compatibility unless the change is deliberately marked as breaking.

## Search/retrieval behavior gate

For search, retrieval planner, hot-context, or active-memory changes:

```bash
uv run pytest -q tests/unit/test_active_memory.py tests/unit/test_query_planner_toggle.py tests/integration/core/test_active_memory_flow.py
```

Check manually:

- unknown profile does not leak default private context
- timeout returns partial context or a structured warning, not a crash
- deterministic fallback works when LLM planner is disabled
- session evidence only appears when the request asks for sessions/recent history or scope allows it

## Semantic/entity gate

For semantic writes, entity registry, claim store, or observations:

```bash
uv run pytest -q tests/unit/test_semantic_write.py tests/unit/test_entity_registry.py tests/unit/test_claim_store.py
```

Check manually:

- no new entity is silently created during retrieval
- observation/claim outputs include source refs
- canonical writes require explicit permission
- forget/supersede behavior preserves audit trail

## Commit gate

Before committing:

```bash
git status --short
git diff --stat
git diff --check
```

Commit message format:

```text
docs: add memory kernel roadmap
feat: add entity context packet
fix: fail closed for unknown profiles
refactor: extract search scoring helpers
```

## Review handoff checklist

Every handoff to Codex should say:

- branch name
- commit hash
- files changed
- what to review first
- exact validation commands run
- known caveats / what was intentionally not done
