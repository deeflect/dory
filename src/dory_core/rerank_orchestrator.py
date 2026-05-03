from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from dory_core.llm_rerank import LLMReranker, RerankCandidate

if TYPE_CHECKING:
    from dory_core.search import _ChunkRow

_logger = logging.getLogger(__name__)


class RerankOrchestrator:
    def __init__(self, reranker: LLMReranker | None, candidate_limit: int) -> None:
        self.reranker = reranker
        self.candidate_limit = max(2, candidate_limit)

    def rerank(
        self,
        rows: list[_ChunkRow],
        *,
        query: str,
        warnings: list[str],
    ) -> list[_ChunkRow]:
        if self.reranker is None or len(rows) < 2:
            return rows
        if len(rows) <= self.candidate_limit:
            return self._apply(rows, query, warnings=warnings)
        rerank_rows = _diverse_prefix(rows, self.candidate_limit, query=query)
        rerank_ids = {row.chunk_id for row in rerank_rows}
        warnings.append(
            f"Rerank considered {len(rerank_rows)} diverse candidates from the top {len(rows)} "
            "and kept the remaining base order."
        )
        return [
            *self._apply(rerank_rows, query, warnings=warnings),
            *(row for row in rows if row.chunk_id not in rerank_ids),
        ]

    def _apply(
        self,
        rows: list[_ChunkRow],
        query: str,
        *,
        warnings: list[str],
    ) -> list[_ChunkRow]:
        from dory_core.search import _rerank_candidate_from_row

        original_candidates = [_rerank_candidate_from_row(row) for row in rows]
        candidates = [_focused_candidate(candidate, query=query) for candidate in original_candidates]
        before_chars = sum(len(candidate.snippet) for candidate in original_candidates)
        after_chars = sum(len(candidate.snippet) for candidate in candidates)
        _logger.info(
            "rerank payload prepared candidate_count=%s query_chars=%s snippet_chars_before=%s snippet_chars_after=%s",
            len(candidates),
            len(query),
            before_chars,
            after_chars,
        )
        started_at = time.perf_counter()
        try:
            result = self.reranker.rerank(query=query, candidates=candidates)
        except Exception:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            _logger.exception("rerank call failed elapsed_ms=%s; falling back to base hybrid ranking", elapsed_ms)
            warnings.append("Rerank failed; kept the base hybrid ranking.")
            return rows
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        _logger.info(
            "rerank call completed candidate_count=%s elapsed_ms=%s returned_count=%s",
            len(candidates),
            elapsed_ms,
            0 if result is None else len(result.ordered_chunk_ids),
        )
        if result is None:
            warnings.append("Rerank returned no usable ranking; kept the base hybrid ranking.")
            return rows
        rows_by_id = {row.chunk_id: row for row in rows}
        reranked: list[_ChunkRow] = []
        for chunk_id in result.ordered_chunk_ids:
            row = rows_by_id.get(chunk_id)
            if row is None:
                continue
            reranked.append(replace(row, score=result.scores.get(chunk_id, row.score)))
        for row in rows:
            if row.chunk_id not in result.ordered_chunk_ids:
                reranked.append(row)
        return reranked


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@/-]*")
_FOCUSED_SNIPPET_CHARS = 1800
_FOCUSED_WINDOW_CHARS = 700
_MIN_MMR_RELEVANCE = 0.15


def _diverse_prefix(rows: list[_ChunkRow], limit: int, *, query: str) -> list[_ChunkRow]:
    if len(rows) <= limit:
        return rows
    query_tokens = set(_query_tokens(query))
    selected: list[_ChunkRow] = []
    remaining = list(rows)
    while remaining and len(selected) < limit:
        next_index = _next_diverse_index(remaining, selected, query_tokens)
        selected.append(remaining.pop(next_index))
    return selected


def _next_diverse_index(
    remaining: list[_ChunkRow],
    selected: list[_ChunkRow],
    query_tokens: set[str],
) -> int:
    if not selected:
        return 0
    best_index = 0
    best_score = -math.inf
    for index, row in enumerate(remaining):
        relevance = _relevance_score(row.content, query_tokens)
        if relevance < _MIN_MMR_RELEVANCE:
            relevance = max(_MIN_MMR_RELEVANCE, 1.0 / (index + 2))
        redundancy = max(_row_similarity(row, selected_row) for selected_row in selected)
        path_penalty = 0.20 if any(row.path == selected_row.path for selected_row in selected) else 0.0
        rank_bonus = 1.0 / (index + 2)
        mmr_score = (0.30 * relevance) + (0.55 * rank_bonus) - (0.35 * redundancy) - path_penalty
        if mmr_score > best_score:
            best_index = index
            best_score = mmr_score
    return best_index


def _focused_candidate(candidate: RerankCandidate, *, query: str) -> RerankCandidate:
    snippet = _focused_snippet(candidate.snippet, query=query)
    if snippet == candidate.snippet:
        return candidate
    return replace(candidate, snippet=snippet)


def _focused_snippet(text: str, *, query: str) -> str:
    if len(" ".join(text.split())) <= _FOCUSED_SNIPPET_CHARS:
        return " ".join(text.split())
    query_tokens = set(_query_tokens(query))
    if not query_tokens:
        return " ".join(text.split())[:_FOCUSED_SNIPPET_CHARS]
    best_window = _best_focus_window(text, query_tokens)
    if best_window is None:
        return " ".join(text.split())[:_FOCUSED_SNIPPET_CHARS]
    return " ".join(best_window.split())[:_FOCUSED_SNIPPET_CHARS].strip()


def _best_focus_window(text: str, query_tokens: set[str]) -> str | None:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    if not paragraphs:
        paragraphs = [text]
    best_paragraph = max(paragraphs, key=lambda paragraph: _focus_score(paragraph, query_tokens))
    if _focus_score(best_paragraph, query_tokens) <= 0:
        return None
    normalized = " ".join(best_paragraph.split())
    if len(normalized) <= _FOCUSED_SNIPPET_CHARS:
        return normalized
    lowered = normalized.lower()
    matches = [lowered.find(token) for token in query_tokens if lowered.find(token) >= 0]
    if not matches:
        return normalized[:_FOCUSED_SNIPPET_CHARS]
    center = round(sum(matches) / len(matches))
    start = max(0, center - _FOCUSED_WINDOW_CHARS)
    end = min(len(normalized), center + _FOCUSED_WINDOW_CHARS)
    if end - start < _FOCUSED_SNIPPET_CHARS:
        start = max(0, min(start, len(normalized) - _FOCUSED_SNIPPET_CHARS))
        end = min(len(normalized), start + _FOCUSED_SNIPPET_CHARS)
    return normalized[start:end]


def _focus_score(text: str, query_tokens: set[str]) -> float:
    tokens = set(_query_tokens(text))
    if not tokens:
        return 0.0
    overlap = len(tokens & query_tokens)
    if overlap == 0:
        return 0.0
    coverage = overlap / len(query_tokens)
    density = overlap / max(len(tokens), 1)
    return coverage + density


def _row_similarity(first: _ChunkRow, second: _ChunkRow) -> float:
    first_tokens = set(_query_tokens(first.content))
    second_tokens = set(_query_tokens(second.content))
    if not first_tokens or not second_tokens:
        return 0.0
    return len(first_tokens & second_tokens) / len(first_tokens | second_tokens)


def _relevance_score(text: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 1.0
    tokens = set(_query_tokens(text))
    if not tokens:
        return 0.0
    return len(tokens & query_tokens) / len(query_tokens)


def _query_tokens(query: str) -> list[str]:
    tokens = [match.group(0).lower() for match in _TOKEN_RE.finditer(query)]
    return [token for token in tokens if len(token) > 2]
