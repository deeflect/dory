<!-- dory-policy:START -->
## Dory — Shared Memory Layer

Dory is the shared memory service for this workspace. Agents read and write
through Dory instead of keeping separate durable memory silos.

**Read flow**

1. At session start or when task switches, call `dory_wake`. Use
   `profile="coding"` for project work, `profile="writing"` for content work,
   and `profile="privacy"` for boundary-sensitive questions.
2. Before making factual claims about projects, people, decisions, priorities,
   or current environment, use `dory_search`.
3. Use `dory_get` for exact source text and hashes, then cite the source path
   when an answer depends on memory.
4. Use `dory_link` for neighbors/backlinks only when relationships matter.
5. Use `dory_search(mode="exact")` for cleanup markers or unique strings.
6. Use `dory_active_memory(include_wake=false)` when wake already ran and the
   reply needs task-specific retrieval.

Search mode notes: `text`, `keyword`, and `lexical` normalize to BM25;
`semantic` normalizes to vector search. Hybrid search is deterministic by
default. LLM-assisted planning, expansion, and reranking only run when the
server opts into the `DORY_QUERY_*` feature flags.

**Write flow**

Write only when at least one condition is true:

- the user explicitly says remember, save, or update
- a durable decision was made
- project state materially changed
- a durable people/project/current-truth fact was established

Use `dry_run=true` first when the write route is not obvious. Use
`dory_memory_write` for durable semantic writes, but keep subjects specific.
After preview, live canonical semantic writes require `allow_canonical=true`.
If the fact is tentative or needs review, use
`dory_memory_write(force_inbox=true)` or write to an explicit `inbox/` target
with `dory_write`. Use `dory_write` only when you know the exact target path
and have read the current hash first. New exact-path files require
`frontmatter.title` and `frontmatter.type`; use `type: capture` for `inbox/**`.
`forget` retires/supersedes; it is not a hard delete. Use `dory_purge` only for
exact eval/test/scratch cleanup; live purge requires `reason` and matching
`expected_hash`.

Do not persist transient conversation turns.

**Core files** to prefer as authoritative sources:

- `core/user.md` — user profile and stable preferences
- `core/soul.md` — writing/communication style
- `core/env.md` — local environment and topology
- `core/identity.md` — public identity/positioning rules
- `core/active.md` — current focus
- `core/defaults.md` — default models, tools, and operating assumptions

**Full integration guide:** see `docs/agent-integration.md`.
<!-- dory-policy:END -->
