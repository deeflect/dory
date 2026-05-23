from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Sequence

from dory_core.search.fts import (
    _extract_normalized_tokens,
    _has_close_identifier_match,
    _normalize_text_for_matching,
)
from dory_core.search.policies import (
    _merge_source_prior,
    _score_document_prior,
)
from dory_core.search.types import QueryProfile, _ChunkRow
from dory_core.search.utils import (
    _load_frontmatter,
)

_HYBRID_MIN_CANDIDATES = 20
_HYBRID_CANDIDATE_MULTIPLIER = 4


def merge_rankings(
    rankings: Sequence[Sequence[str]],
    *,
    limit: int = 10,
    fusion_k: int = 60,
) -> list[str]:
    scores = _fuse_scores(rankings, fusion_k=fusion_k)
    ordered_ids = sorted(scores, key=lambda item: (-scores[item], item))
    return ordered_ids[:limit]


def _fuse_scores(
    rankings: Sequence[Sequence[str]],
    *,
    fusion_k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (fusion_k + position)
    return dict(scores)


def _apply_hybrid_priors(
    rows: Sequence[_ChunkRow],
    query_profile: QueryProfile,
) -> list[_ChunkRow]:
    from dataclasses import replace

    boosted_rows: list[_ChunkRow] = []
    for row in rows:
        frontmatter = _load_frontmatter(row.frontmatter_json)
        lexical_signal = _score_lexical_signal(row, frontmatter, query_profile)
        document_prior = _score_document_prior(row, frontmatter, query_profile)
        boosted_rows.append(replace(row, score=row.score + lexical_signal + document_prior))
    return sorted(boosted_rows, key=lambda row: (-row.score, row.chunk_id))


def _apply_min_relevance_score(response: object, threshold: float) -> object:
    """Drop results whose normalized score is below ``threshold`` (0..1)."""
    from dory_core.types import SearchResp

    if threshold <= 0.0:
        return response
    results = [
        result
        for result in response.results
        if (result.score_normalized or 0.0) >= threshold
    ]
    return SearchResp(
        query=response.query,
        count=len(results),
        results=results,
        took_ms=response.took_ms,
        warnings=list(response.warnings),
    )


def _normalized_scores(rows: Sequence[_ChunkRow], *, mode: str) -> dict[str, float]:
    if not rows:
        return {}
    if mode == "exact":
        return {row.chunk_id: 1.0 for row in rows}
    values = [-row.score if mode == "bm25" else row.score for row in rows]
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return {row.chunk_id: _rank_normalized_score(index, total=len(rows)) for index, row in enumerate(rows, start=1)}
    return {
        row.chunk_id: max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))
        for row, value in zip(rows, values, strict=True)
    }


def _rank_normalized_score(position: int, *, total: int) -> float:
    if total <= 1:
        return 1.0
    return max(0.0, 1.0 - ((position - 1) / (total - 1)))


def _with_rank_scores(results: Sequence[object]) -> list[object]:
    total = len(results)
    return [
        result.model_copy(
            update={
                "rank_score": _rank_normalized_score(position, total=total),
                "score_normalized": _rank_normalized_score(position, total=total),
            }
        )
        for position, result in enumerate(results, start=1)
    ]


def _score_lexical_signal(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    query_profile: QueryProfile,
) -> float:
    if not query_profile.tokens and not query_profile.phrases:
        return 0.0
    title = frontmatter.get("title")
    title_text = title if isinstance(title, str) else ""
    content_tokens = set(_extract_normalized_tokens(row.content))
    identifier_tokens = set(_extract_normalized_tokens(" ".join(Path(row.path).parts)))
    identifier_tokens.update(_extract_normalized_tokens(title_text))
    document_tokens = content_tokens | identifier_tokens

    exact_hits = 0
    fuzzy_hits = 0
    for token in query_profile.tokens:
        if token in document_tokens:
            exact_hits += 1
            continue
        if _has_close_identifier_match(token, identifier_tokens):
            fuzzy_hits += 1

    token_count = len(query_profile.tokens)
    coverage_score = 0.0
    if token_count:
        weighted_hits = exact_hits + (fuzzy_hits * 0.55)
        coverage_ratio = weighted_hits / token_count
        coverage_score += min(0.032, coverage_ratio * 0.026)
        if exact_hits == token_count:
            coverage_score += 0.006

    normalized_haystack = _normalize_text_for_matching(f"{title_text}\n{row.content}")
    phrase_matches = sum(1 for phrase in query_profile.phrases if phrase in normalized_haystack)
    if phrase_matches:
        coverage_score += min(0.012, phrase_matches * 0.006)

    return coverage_score


def _score_chunk_exact_coverage(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    query_profile: QueryProfile,
) -> float:
    if not query_profile.tokens:
        return 0.0
    title = frontmatter.get("title")
    title_text = title if isinstance(title, str) else ""
    haystack = " ".join([row.path, title_text, row.content])
    document_tokens = set(_extract_normalized_tokens(haystack))
    exact_hits = sum(1 for token in query_profile.tokens if token in document_tokens)
    return exact_hits / len(query_profile.tokens)


def _confidence_for_row(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    *,
    query_profile: QueryProfile,
    mode: str,
    position: int,
) -> str:
    if mode == "exact":
        return "high"
    coverage = _score_chunk_exact_coverage(row, frontmatter, query_profile)
    if coverage >= 0.75 and position <= 3:
        return "high"
    if coverage >= 0.4 or (position <= 2 and row.score >= 0.04):
        return "medium"
    return "low"


def _score_exact_result_coverage(result: object, query_profile: QueryProfile) -> float:
    if not query_profile.tokens:
        return 1.0
    haystack = " ".join(
        [
            result.path,
            str(result.frontmatter.get("title", "")),
            result.snippet,
        ]
    )
    matched_tokens = set(_extract_normalized_tokens(haystack))
    exact_hits = sum(1 for token in query_profile.tokens if token in matched_tokens)
    return exact_hits / len(query_profile.tokens)


def _evidence_class_for_document(path: str, frontmatter: dict[str, object]) -> str:
    if path.startswith("logs/sessions/"):
        return "session"
    if path.startswith("inbox/"):
        return "inbox"
    if path.startswith("archive/"):
        return "archive"
    status = str(frontmatter.get("status", "")).strip().lower()
    if status == "raw":
        return "raw"
    source_kind = str(frontmatter.get("source_kind", "")).strip().lower()
    if source_kind == "generated":
        return "generated"
    if frontmatter.get("canonical") is True or source_kind == "canonical":
        return "canonical"
    return "other"


def _merge_result_score(
    result: object,
    *,
    position: int,
    query_profile: QueryProfile,
    source: str,
) -> float:
    coverage = _score_exact_result_coverage(result, query_profile)
    rank_score = 1.0 / (20 + position)
    source_bonus = _merge_source_prior(result, query_profile=query_profile, source=source)
    return rank_score + (coverage * 0.08) + source_bonus
