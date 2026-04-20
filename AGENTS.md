# Agent Guide

This is the public Dory repository. Dory is a local-first memory daemon for agents: markdown is the source of truth, SQLite is the disposable index sidecar, and clients talk to it through CLI, HTTP, MCP, Hermes, and OpenClaw integrations.

## First Principles

- Keep the public tree clean. Never add private corpus files, real personal memories, direct contact details, private hostnames, tokens, or local absolute paths.
- Use synthetic examples in tests, docs, fixtures, and evals. Do not use real user biography, finance, health, private life events, or private relationship details as fixtures.
- Prefer the smallest correct diff. Avoid broad refactors unless the task explicitly calls for them.
- Do not invent new framework layers when the existing `src/dory_core`, `src/dory_http`, `src/dory_mcp`, `src/dory_cli`, `plugins/`, and `packages/` boundaries already fit.
- Keep generated artifacts intentional. `packages/openclaw-dory/dist/index.js` is tracked because OpenClaw loads it; most other build output is not.

## Repository Map

- `src/dory_core/` - memory model, search, wake, active memory, semantic writes, indexing, operations.
- `src/dory_http/` - FastAPI daemon and HTTP contract.
- `src/dory_mcp/` - native MCP server and tool schemas.
- `src/dory_cli/` - Typer CLI.
- `plugins/hermes-dory/` - Hermes provider.
- `packages/openclaw-dory/` - OpenClaw memory plugin package. Rebuild `dist/index.js` after TypeScript changes.
- `docs/`, `references/`, `eval/`, `examples/` - public documentation, runbooks, public evals, and safe sample corpus material.
- `scripts/release/check-public-safety.py` - public leak guard for obvious secrets, private paths, and configured private terms.

## Dory Memory Policy

Use Dory as the shared memory layer for agent work in this repo.

1. At session start or when switching tasks, call `wake` with the right profile.
2. Before making factual claims about memory, projects, people, priorities, decisions, or current environment, use Dory first.
3. Preferred read flow: `wake`, then `search`, then exact `get`, then `link` only when graph context is useful.
4. Cite exact Dory source paths when an answer depends on Dory memory.
5. If evidence is weak, stale, private, or conflicting, say that directly instead of guessing.

Useful profile defaults:

- Coding work: `wake(profile="coding", budget=1200)`.
- Writing work: use writing/voice sources only when needed; do not copy private user details into repo docs.
- Privacy work: use privacy/boundary context, not full profile data.

## Dory Write Policy

Write through Dory, not by freestyle-editing memory files directly.

Only write when at least one of these is true:

- the user explicitly says `remember`, `save`, or `update`;
- a clear durable decision was made;
- project state materially changed;
- a durable people/project/current-truth fact was established.

Do not write memory for transient conversation turns. Always dry-run semantic writes first. Use `force_inbox=true` for uncertain notes. Use canonical writes only after the target is clear and intentional.

## Development Commands

```bash
uv sync --frozen --all-groups
uv run ruff check .
uv run pytest -q
uv build --wheel --sdist
uv run python eval/validate.py
python3 scripts/release/check-public-safety.py
```

For focused changes, run the smallest test that proves the behavior. For broad changes, run the full unit or full CI-equivalent checks.

OpenClaw plugin changes:

```bash
cd packages/openclaw-dory
npm install
npm run build
```

## Commit Rules

- Do not create commits unless the user asks for a commit.
- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`, `ci:`, `build:`, `chore:`, `security:`.
- Keep the subject imperative, specific, and under 72 characters.
- Put one logical change in one commit. Do not mix behavior, docs, formatting, and release churn unless they are inseparable.
- Before committing, run relevant tests and leak checks. For public docs/evals/examples, run `python3 scripts/release/check-public-safety.py` on the touched paths.
- Never commit `.env`, `.dory/`, `.index/`, `data/`, private corpora, raw session logs, real tokens, or local machine paths.

## Pull Request Expectations

- Explain the user-visible change and the reason for it.
- List verification commands actually run.
- Call out unverified areas and follow-up risk.
- Add tests when behavior changes, especially for search ranking, active-memory privacy, write safety, HTTP/MCP contracts, and plugin interfaces.
- Keep docs aligned with behavior. If a tool schema, CLI option, or plugin config changes, update the relevant docs in the same PR.
