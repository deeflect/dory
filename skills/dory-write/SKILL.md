# Dory Write

Use Dory write surfaces through CLI/MCP/HTTP when durable memory should change.

Workflow:
1. Search first to see what already exists
2. Prefer semantic `dory_memory_write` / `memory-write` with action `write`, `replace`, or `forget`
3. Use `dry_run=true` first when the subject or target is not obvious
4. Use exact-path `dory_write` only through MCP/HTTP when the target path is known and you have read the current hash
5. Use `dory_purge` only for exact scratch/test artifact cleanup, and keep its default `dry_run=true` until the target/hash are verified

Rules:
- Do not invent frontmatter
- Do not create `core/` files casually
- Use `force_inbox=true` for tentative or scratch captures
- Use `allow_canonical=true` only after preview when a semantic write intentionally resolves to canonical memory
- CLI exposes semantic `memory-write` and guarded `purge`; exact-path `dory_write` is an MCP/HTTP surface, not a top-level CLI command
- Legacy `add`/`create` write actions normalize to `write`; `remove`/`delete` normalize to `forget`, but new instructions should use the canonical action names
- Plain-text mentions of known people/projects will create graph edges automatically; wikilinks are optional, not required
