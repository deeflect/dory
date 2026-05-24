from __future__ import annotations

from pathlib import Path
from typing import Literal

from dory_core.active_memory_policy import (
    SourcePolicy,
    active_memory_path_weight,
    is_active_memory_candidate,
)
from dory_core.markdown_excerpt import canonical_file_excerpt, first_content_excerpt, strip_frontmatter
from dory_core.project_context import resolve_project_handle, resolve_project_path
from dory_core.types import ActiveMemoryReq, SearchReq, SearchResult, SearchScope


def expanded_candidate_limit(k: int, *, source_policy: SourcePolicy | None, corpus: str) -> int:
    if source_policy is None or not source_policy.needs_prefilter_expansion(corpus=corpus):
        return k
    return min(50, max(k, k * 4))


def search_candidates(
    search_engine,
    *,
    queries: tuple[str, ...],
    k: int,
    mode: str,
    corpus: str,
    include_content: bool,
    rerank: Literal["auto", "true", "false"],
    scope: SearchScope | None = None,
    deadline=None,
    source_policy: SourcePolicy | None = None,
    min_remaining_ms: int = 0,
) -> list[object]:
    scored_results: dict[str, tuple[float, object]] = {}
    request_k = expanded_candidate_limit(k, source_policy=source_policy, corpus=corpus)
    for query_index, query in enumerate(query for query in queries if query.strip()):
        if deadline is not None and (deadline.expired or deadline.remaining_ms < min_remaining_ms):
            break
        response = search_engine.search(
            SearchReq(
                query=query,
                k=request_k,
                mode=mode,
                corpus=corpus,
                include_content=include_content,
                rerank=rerank,
                scope=scope or SearchScope(),
            )
        )
        for result_index, result in enumerate(list(getattr(response, "results", [])), start=1):
            path = str(getattr(result, "path", "") or "")
            if not path:
                continue
            if not is_active_memory_candidate(result, corpus=corpus):
                continue
            if source_policy is not None and not source_policy.allows_result_path(path, corpus=corpus):
                continue
            raw_score = float(getattr(result, "score", 0.0) or 0.0)
            rank_score = getattr(result, "rank_score", None)
            normalized_score = getattr(result, "score_normalized", None)
            if isinstance(rank_score, (int, float)):
                base_score = float(rank_score)
            elif isinstance(normalized_score, (int, float)):
                base_score = float(normalized_score)
            else:
                base_score = raw_score
            stale_penalty = 0.15 if str(getattr(result, "stale_warning", "") or "").strip() else 0.0
            path_weight = (
                source_policy.path_weight(path) if source_policy is not None else active_memory_path_weight(path)
            )
            score = base_score + path_weight - (query_index * 0.06) - (result_index * 0.01)
            score -= stale_penalty
            existing = scored_results.get(path)
            if existing is None or score > existing[0]:
                scored_results[path] = (score, result)
    ordered = sorted(scored_results.values(), key=lambda item: (-item[0], str(getattr(item[1], "path", ""))))
    return [result for _score, result in ordered[:k]]


def active_memory_rerank_mode(
    requested: Literal["auto", "true", "false"],
    deadline,
    budget,
) -> Literal["auto", "true", "false"]:
    if requested == "false":
        return "false"
    if deadline.total_ms <= budget.rerank_timeout_headroom_ms:
        return "false"
    return "true" if requested == "auto" else requested


def preferred_active_memory_results(results: list[object]) -> list[object]:
    fresh_results = [result for result in results if not str(getattr(result, "stale_warning", "") or "").strip()]
    return fresh_results or results


def dedupe_results_by_path(results: list[object]) -> list[object]:
    seen: set[str] = set()
    deduped: list[object] = []
    for result in results:
        path = str(getattr(result, "path", "") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        deduped.append(result)
    return deduped


def project_state_result(req: ActiveMemoryReq, *, root: Path | None) -> SearchResult | None:
    if root is None:
        return None
    handle = resolve_project_handle(project=req.project, cwd=req.cwd, root=root)
    if handle is None:
        return None
    project_path = resolve_project_path(root, handle)
    if project_path is None:
        return None
    rel_path = project_path.relative_to(root).as_posix()
    try:
        text = project_path.read_text(encoding="utf-8")
    except OSError:
        return None
    snippet = canonical_file_excerpt(root, rel_path)
    if not snippet:
        snippet = first_content_excerpt(strip_frontmatter(text))
    try:
        from dory_core.frontmatter import load_markdown_document

        frontmatter = load_markdown_document(text).frontmatter
    except ValueError:
        frontmatter = {}
    total_lines = max(1, len(text.splitlines()))
    return SearchResult(
        path=rel_path,
        lines=f"1-{total_lines}",
        score=1.0,
        score_normalized=1.0,
        rank_score=1.0,
        evidence_class="canonical",
        snippet=snippet,
        frontmatter=frontmatter,
        stale_warning=None,
        confidence="high",
    )


def with_project_result(
    req: ActiveMemoryReq,
    results: list[object],
    *,
    root: Path | None,
    source_policy: SourcePolicy,
) -> list[object]:
    project_result = project_state_result(req, root=root)
    if project_result is None or not source_policy.allows_result_path(
        str(getattr(project_result, "path", "") or ""), corpus="durable"
    ):
        return results
    return dedupe_results_by_path([project_result, *results])
