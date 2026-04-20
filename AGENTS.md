# Dory Memory Policy

Use Dory as the shared memory layer for agent work in this repo.

## Default Lifecycle

1. At session start or when switching tasks, call `wake`.
2. Before making factual claims about memory, projects, people, priorities, decisions, or current environment, use Dory first.
3. Preferred read flow:
   - `wake`
   - `search`
   - `get` for exact inspection
   - `link` for neighbors/backlinks/related context
4. Cite exact source file paths when an answer depends on Dory memory.
5. If evidence is weak, stale, or conflicting, say that directly instead of guessing.

## Intent Recipes

- `who am I`
  - prefer `core/user.md`, `core/soul.md`, `core/env.md`
- `what are we working on today`
  - use `wake`, then inspect `core/active.md`, then recent logs if needed
- `what did I work on last`
  - search recent `logs/sessions` and `logs/daily`, not just broad project recall
- decision questions
  - prefer `decisions/canonical/` and current project state before old logs

## Write Policy

Write through Dory, not by freestyle-editing memory files directly.

Only write when at least one of these is true:
- the user explicitly says `remember`, `save`, or `update`
- a clear durable decision was made
- project state materially changed
- a durable people/project/current-truth fact was established

Do not write memory for transient conversation turns.

## Write Targets

Prefer canonical targets:
- `core/` for hot identity/current-truth docs
- `people/<slug>.md` for durable person facts
- `projects/<slug>/state.md` for canonical project truth
- `decisions/canonical/<date>-<slug>.md` for explicit durable decisions
- `inbox/` for uncertain or review-needed material

If a fact is uncertain or only tentatively inferred, write it to `inbox/` or do not persist it yet.
