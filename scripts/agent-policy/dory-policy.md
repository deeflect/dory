<!-- dory-policy:START -->
## Dory — Shared Memory Layer

Dory is the shared memory service for this machine across all projects, not
only the Dory repository. Agents read and write through Dory instead of keeping
separate durable memory silos.

**Read flow**

1. At the start of every new session, before substantive work, call
   `dory_wake`. Do this even when the current repo is not the Dory repo. Choose
   the profile from the task: `profile="coding"` for software/project
   implementation, `profile="writing"` for content/copy/voice work, and
   `profile="privacy"` for boundary-sensitive questions. If the current task or
   repo clearly maps to a known project, pass `project="<name-or-slug-or-path>"`
   or `cwd="<current-working-directory>"` so wake includes that project state
   page. Project matching is exact first and then unambiguous fuzzy/path-based.
2. Before making factual claims about projects, people, decisions, priorities,
   or current environment, use `dory_search`.
3. For a fast recent recap, use `dory_digest` directly. It resolves daily and
   weekly digest files by path; do not search for or require a `digest` tag.
4. Use `dory_get` for exact source text and hashes, then cite the source path
   when an answer depends on memory.
5. Use `dory_link` for neighbors/backlinks only when relationships matter.
6. Use `dory_search(mode="exact")` for cleanup markers or unique strings.
7. Use `dory_active_memory(include_wake=false)` when wake already ran and the
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

**Profile-specific sources** to prefer:

- Coding/project work: prefer `projects/<slug>/state.md`, `core/active.md`,
  `core/env.md`, and `core/defaults.md`. Do not pull `core/user.md`,
  `core/soul.md`, `core/identity.md`, `people/**`, or
  `knowledge/personal/**` unless the user asks for personal/profile context.
- Writing/voice work: prefer `knowledge/personal/writing-voice.md`,
  `core/soul.md`, and task/project state. Avoid unrelated people/profile pages.
- Privacy/boundary work: use `profile="privacy"` and boundary-only wake/context.
  Treat private identifiers, contact details, credentials, financial, legal, and
  health material as searchable evidence only when the task explicitly requires
  it.
- General assistant work: use `dory_active_memory` for the brief first, then
  exact `dory_get` on cited sources when details matter.

**Full integration guide:** see `docs/agent-integration.md`.
<!-- dory-policy:END -->
