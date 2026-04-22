from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from dory_core.llm_rerank import LLMReranker

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
        warnings.append(
            f"Rerank considered the top {self.candidate_limit} candidates and kept the remaining base order."
        )
        return [
            *self._apply(rows[: self.candidate_limit], query, warnings=warnings),
            *rows[self.candidate_limit :],
        ]

    def _apply(
        self,
        rows: list[_ChunkRow],
        query: str,
        *,
        warnings: list[str],
    ) -> list[_ChunkRow]:
        from dory_core.search import _rerank_candidate_from_row

        candidates = [_rerank_candidate_from_row(row) for row in rows]
        try:
            result = self.reranker.rerank(query=query, candidates=candidates)
        except Exception:
            _logger.exception("rerank call failed; falling back to base hybrid ranking")
            warnings.append("Rerank failed; kept the base hybrid ranking.")
            return rows
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
        return reranked
