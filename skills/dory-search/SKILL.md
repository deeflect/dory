# Dory Search

Use `dory search` before answering factual questions about people, projects, decisions, dates, or prior sessions.

Workflow:
1. Start with `uv run dory --corpus-root <corpus> --index-root <index> search "<query>"`
2. If needed, follow with `dory get <path>` on the best hit
3. If a result has `stale_warning`, treat it as possibly outdated and look for fresher evidence

Rules:
- Prefer retrieval over guessing
- Use search even if wake seems related; wake is cached context, search is live evidence
- Use `mode=exact` for cleanup markers, unique strings, and artifact existence checks
- Use `mode=text`, `mode=keyword`, or `mode=lexical` when you want BM25-only search; all three normalize to `bm25`
- Use `mode=semantic` when you want vector-only search; it normalizes to `vector`
- Hybrid search is deterministic by default; LLM-assisted planning, expansion, and reranking require explicit `DORY_QUERY_*` server flags
