# Claude Agent Guide

Claude agents should follow `AGENTS.md`. This file adds Claude-specific reminders for working in the public Dory repository.

## Operating Mode

- Use Dory MCP tools directly when available. Start with `dory_wake(profile="coding", budget_tokens=1200)` for code work.
- Prefer `dory_search` followed by exact `dory_get` before making claims about project state, prior decisions, or current runtime assumptions.
- Do not copy private Dory memory into repository files. Public docs, tests, evals, examples, and issue templates must use synthetic data.
- Keep responses and patches scoped. If a change is cross-cutting, state the plan briefly and then implement.

## Codebase Norms

- Python code targets Python 3.12 and is managed with `uv`.
- Run `uv run ruff check <paths>` for touched Python paths.
- Run focused `uv run pytest ...` tests for behavior changes; use `uv run pytest -q` when the change has broad impact.
- For OpenClaw plugin changes, update `packages/openclaw-dory/src/index.ts`, run `npm run build`, and include the rebuilt `packages/openclaw-dory/dist/index.js`.
- Do not edit generated or local runtime files unless the task explicitly asks for them.

## Public-Safety Rules

- No real tokens, API keys, bearer values, private hostnames, personal identifiers, or local absolute paths.
- No real private memories in fixtures. Use names like `Atlas`, `Demo User`, `Example Project`, and placeholder domains like `example.com`.
- Do not add raw session logs or private corpus material to the public tree.
- Run `python3 scripts/release/check-public-safety.py --path <path>` for changed public docs, fixtures, evals, examples, or release artifacts.

## Commit And PR Rules

- Only commit when the user asks.
- Use Conventional Commits with a clear scope when useful, for example `docs: add contribution guide` or `fix(search): rank canonical hits first`.
- Include verification in the final response and in PR notes.
- Mention any skipped checks explicitly.
