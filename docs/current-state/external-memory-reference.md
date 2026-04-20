# External memory reference

Compact comparison of external memory systems and patterns useful to Dory: Karpathy's LLM Wiki pattern, `gbrain`, `mem0`, and `MemPalace`.

Design reference, not a spec for the current repo.

## One-line summary

- Karpathy's LLM Wiki is the strongest reference for a persistent markdown/wiki layer that compounds over time instead of making the model rederive synthesis from raw sources on every query.
- `gbrain` is the strongest reference for durable, human-readable memory pages with compiled truth, append-only evidence, and explicit entity resolution.
- `mem0` is the strongest reference for scoped CRUD semantics, filter-based retrieval, and managed-vs-OSS migration boundaries.
- `MemPalace` is the strongest reference for local-first raw transcript storage, layered wake-up context, and a lightweight index sitting beside verbatim chunks.

## Entity resolution

### `gbrain`

- Uses canonical slugs as stable IDs for pages.
- Routes entities into MECE directories with per-directory `README.md` resolvers.
- Treats aliases as alternate names on the same entity page, not separate pages.
- Deduplicates before creating a page and merges duplicates into one survivor page.
- Good fit for Dory when you want one canonical page per person/project/idea and backlinks everywhere.

### `mem0`

- Does not model human-readable entity pages.
- Uses scoped identifiers instead: `user_id`, `agent_id`, `app_id`, and `run_id`.
- Entity separation is mostly a query/filter concern, not a page-identity concern.
- Good fit for Dory when you want clean scope boundaries, not a semantic entity graph.

### `MemPalace`

- Uses an `EntityRegistry` to disambiguate names locally.
- Separates projects into wings and topics into rooms.
- Builds a temporal knowledge graph with normalized entity IDs and triple validity windows.
- Good fit for Dory when you want local entity typing plus a graph layer, but not automatic page synthesis.

## Bucket / schema

### Karpathy LLM Wiki

- Uses raw sources, a generated wiki, and a schema/instructions file as separate layers.
- Treats the wiki as a persistent artifact the LLM maintains over time.
- Uses markdown links, an index, and a log to make the accumulated synthesis navigable.
- Good fit for Dory's compiled `wiki/` shell, but Dory keeps canonical memory and claim history outside the generated wiki so the wiki can be regenerated.

### `gbrain`

- Schema is file-first and markdown-native.
- Canonical layout is:
  - compiled truth above `---`
  - timeline evidence below `---`
- Directory choice is part of the schema: people, companies, projects, concepts, meetings, ideas, originals, and more.
- Frontmatter and `.raw/` sidecars carry structured metadata and provenance.

### `mem0`

- The main schema is identity + metadata:
  - `user_id`, `agent_id`, `app_id`, `run_id`
  - optional `metadata`
- In OSS, custom categorization is typically modeled as metadata such as `memory_bucket`.
- In Platform, filtering and category handling are more managed and use `filters` JSON.
- The underlying storage can be vector plus optional graph, but that is hidden behind the API.

### `MemPalace`

- The file/chunk schema is:
  - wing = project or top-level domain
  - room = topic slice
  - drawer = verbatim chunk
  - closet = compact pointer index
- The knowledge graph schema is SQLite:
  - `entities`
  - `triples`
  - `attributes`
- Drawer metadata carries `wing`, `room`, `source_file`, `chunk_index`, and normalization/version fields.

## Migration / import strategy

### `gbrain`

- Import markdown into the brain repo, then sync and embed.
- Treat import as repo population, not as a database dump.
- Best pattern: first get the files into the MECE directory structure, then let sync/indexing catch up.

### `mem0`

- Historical backfills are usually a loop over `add(...)` calls.
- Raw imports should use `infer=False` when you want exact transcript storage.
- Platform migration from OSS requires switching retrieval filters into `filters={...}` and accepting that Platform is the managed service boundary.
- For exports/migration, Platform supports export jobs and filtered download paths.

### `MemPalace`

- `mempalace mine` is the main import path.
- Conversation exports are normalized first, then mined into drawers.
- `mempalace split` should run before mining large combined chat exports.
- `mempalace migrate` can rebuild a palace by reading ChromaDB SQLite directly and re-importing into a fresh collection when the on-disk version is incompatible.

## Save / update / forget semantics

### `gbrain`

- Save by rewriting compiled truth and appending new timeline evidence.
- Never edit timeline entries in place.
- Corrections are appended as new evidence rather than overwriting history.
- Back-links are mandatory when a page references another entity.

### `mem0`

- `add` infers facts by default and resolves duplicates/contradictions in the inference pipeline.
- `update` edits an existing memory by `memory_id`.
- `delete` removes a single memory; `delete_all` removes scoped sets of memories.
- `infer=False` skips dedupe and conflict resolution, so mixing it with inferred writes will create duplicates.

### `MemPalace`

- `add_drawer` stores verbatim content, not a synthesized memory.
- Closets are rebuilt on re-mine; they are snapshot indexes, not an append-only history.
- The knowledge graph can invalidate a fact by setting `valid_to`, which preserves history without keeping the fact current.
- `delete_drawer` is the hard delete path.

## Retrieval / index architecture

### Karpathy LLM Wiki

- Starts with direct markdown navigation through an index file.
- Adds search tooling when the wiki grows beyond what an agent can inspect directly.
- Dory adopts the compounding wiki idea but uses search/index APIs as first-class runtime surfaces instead of relying on prose instructions alone.

### `gbrain`

- Uses three lookup modes:
  - direct get when the slug is known
  - keyword search for exact terms
  - hybrid query for semantic questions
- Hybrid search combines vector and keyword retrieval, then fuses results.
- Search should run before external APIs.
- The skillpack is as important as the index: it teaches agents when to read, write, and enrich.

### `mem0`

- Search is vector-first with filters, thresholds, and optional reranking.
- Retrieval is scoped through the same identity keys used for storage.
- Platform and OSS differ mainly in where filtering and managed features live, not in the mental model.
- Optional graph memory complements vector retrieval, but the public API stays CRUD-shaped.

### `MemPalace`

- Search is vector semantic search over drawers.
- Closets are a short-text index that boosts retrieval, but should not block drawer search.
- The current implementation does hybrid ranking over direct drawer hits plus closet signals.
- The wake-up stack is intentionally bounded:
  - L0 identity
  - L1 essential story
  - L2 wing/room recall
  - L3 deep search

## What Dory should steal

- From Karpathy's LLM Wiki: persistent markdown synthesis, human-browsable wiki pages, index/log conventions, and the idea that knowledge should compound rather than be rederived every chat.
- From `gbrain`: compiled truth above the line, append-only evidence below it, and a strict read-before-write loop.
- From `gbrain`: canonical entity pages, alias handling, backlinks, and explicit source attribution.
- From `mem0`: scoped identity keys, clean delete-all semantics, and filter-based retrieval.
- From `mem0`: raw import vs inferred import as a deliberate choice.
- From `MemPalace`: a small bounded wake-up context and a separation between raw content and a compact index.
- From `MemPalace`: local-first migration repair for storage-version drift.

## What Dory should avoid

- Avoid treating generated wiki pages as the only source of truth; Dory should be able to rebuild them from canonical pages, claims, and evidence.
- Avoid `gbrain`'s assumption that a large operational skillpack can be maintained just by prose if the tooling loop is not actually enforced.
- Avoid `mem0`'s tendency to hide too much behind managed APIs when Dory benefits from explicit, inspectable files.
- Avoid mixing inferred and raw writes in one scope.
- Avoid treating lossy compression or aggressive abbreviation as a default memory representation.
- Avoid any architecture where the index becomes the source of truth instead of the content.

## Recommended Dory mapping

- `gbrain` maps best to `people/`, `projects/`, `decisions/`, and other canonical markdown pages.
- `mem0` maps best to Dory's write filters, session/user scoping, and delete semantics.
- `MemPalace` maps best to Dory's corpus scan, recall stack, and raw artifact storage.

## Source files reviewed

- `karpathy/llm-wiki.md` gist: `https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f`
- `garrytan/gbrain/docs/GBRAIN_RECOMMENDED_SCHEMA.md`
- `garrytan/gbrain/docs/GBRAIN_SKILLPACK.md`
- `garrytan/gbrain/docs/guides/compiled-truth.md`
- `garrytan/gbrain/docs/guides/entity-detection.md`
- `garrytan/gbrain/docs/guides/search-modes.md`
- `garrytan/gbrain/docs/guides/brain-first-lookup.md`
- `mem0ai/mem0/README.md`
- `mem0ai/mem0/MIGRATION_GUIDE_v1.0.md`
- `mem0ai/mem0/docs/core-concepts/memory-types.mdx`
- `mem0ai/mem0/docs/core-concepts/memory-operations/add.mdx`
- `mem0ai/mem0/docs/core-concepts/memory-operations/search.mdx`
- `mem0ai/mem0/docs/core-concepts/memory-operations/update.mdx`
- `mem0ai/mem0/docs/core-concepts/memory-operations/delete.mdx`
- `mem0ai/mem0/docs/cookbooks/essentials/entity-partitioning-playbook.mdx`
- `mem0ai/mem0/docs/cookbooks/essentials/building-ai-companion.mdx`
- `mem0ai/mem0/docs/cookbooks/essentials/controlling-memory-ingestion.mdx`
- `mem0ai/mem0/docs/migration/oss-to-platform.mdx`
- `MemPalace/mempalace/README.md`
- `MemPalace/mempalace/docs/CLOSETS.md`
- `MemPalace/mempalace/docs/schema.sql`
- `MemPalace/mempalace/website/concepts/memory-stack.md`
- `MemPalace/mempalace/website/concepts/knowledge-graph.md`
- `MemPalace/mempalace/website/guide/searching.md`
- `MemPalace/mempalace/website/guide/mining.md`
- `MemPalace/mempalace/mempalace/entity_detector.py`
- `MemPalace/mempalace/mempalace/entity_registry.py`
- `MemPalace/mempalace/mempalace/migrate.py`
- `MemPalace/mempalace/mempalace/searcher.py`
