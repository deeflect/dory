from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from dory_core.llm_rerank import RerankCandidate, RerankResult
from dory_core.search import SearchEngine, _ChunkRow


@dataclass
class _FakeReranker:
    order_by_chunk_id: dict[str, float]
    last_query: str | None = None
    last_candidates: tuple[RerankCandidate, ...] = field(default_factory=tuple)

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> RerankResult | None:
        self.last_query = query
        self.last_candidates = tuple(candidates)
        scores = {c.chunk_id: self.order_by_chunk_id.get(c.chunk_id, 0.0) for c in candidates}
        ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))
        return RerankResult(ordered_chunk_ids=tuple(ordered), scores=scores)


def test_rerank_applies_reranker_output(tmp_path: Path, fake_embedder) -> None:
    reranker = _FakeReranker(order_by_chunk_id={"a": 0.95, "b": 0.20})
    engine = SearchEngine(tmp_path, fake_embedder, reranker=reranker)
    rows = [
        _ChunkRow(
            chunk_id="b",
            path="projects/alpha/state.md",
            content="Alpha is active.",
            start_line=1,
            end_line=3,
            frontmatter_json='{"title":"Alpha","type":"project","status":"active","canonical":true}',
            score=0.92,
        ),
        _ChunkRow(
            chunk_id="a",
            path="core/homeserver.md",
            content="HomeServer is the active focus.",
            start_line=1,
            end_line=3,
            frontmatter_json='{"title":"HomeServer","type":"core","status":"active","canonical":true}',
            score=0.10,
        ),
    ]

    warnings: list[str] = []
    reranked = engine.rerank_orchestrator.rerank(rows, query="HomeServer", warnings=warnings)

    assert [row.chunk_id for row in reranked] == ["a", "b"]
    assert reranked[0].path == "core/homeserver.md"
    assert reranker.last_query == "HomeServer"


def test_rerank_noops_when_no_reranker(tmp_path: Path, fake_embedder) -> None:
    engine = SearchEngine(tmp_path, fake_embedder, reranker=None)
    rows = [
        _ChunkRow(
            chunk_id="a",
            path="docs/a.md",
            content="a",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.5,
        ),
        _ChunkRow(
            chunk_id="b",
            path="docs/b.md",
            content="b",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.4,
        ),
    ]

    warnings: list[str] = []
    assert engine.rerank_orchestrator.rerank(rows, query="q", warnings=warnings) == rows
    assert warnings == []


def test_rerank_falls_through_when_result_is_none(tmp_path: Path, fake_embedder) -> None:
    class _NoneReranker:
        def rerank(self, *, query, candidates):
            return None

    engine = SearchEngine(tmp_path, fake_embedder, reranker=_NoneReranker())
    rows = [
        _ChunkRow(
            chunk_id="a",
            path="docs/a.md",
            content="a",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.5,
        ),
        _ChunkRow(
            chunk_id="b",
            path="docs/b.md",
            content="b",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.4,
        ),
    ]

    warnings: list[str] = []
    assert engine.rerank_orchestrator.rerank(rows, query="q", warnings=warnings) == rows
    assert any("Rerank returned no usable ranking" in w for w in warnings)


def test_rerank_warns_and_falls_through_on_exception(tmp_path: Path, fake_embedder) -> None:
    class _BrokenReranker:
        def rerank(self, *, query, candidates):
            raise RuntimeError("boom")

    engine = SearchEngine(tmp_path, fake_embedder, reranker=_BrokenReranker())
    rows = [
        _ChunkRow(
            chunk_id="a",
            path="docs/a.md",
            content="a",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.5,
        ),
        _ChunkRow(
            chunk_id="b",
            path="docs/b.md",
            content="b",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.4,
        ),
    ]

    warnings: list[str] = []
    assert engine.rerank_orchestrator.rerank(rows, query="q", warnings=warnings) == rows
    assert any("Rerank failed" in w for w in warnings)


def test_rerank_limited_only_sends_top_candidates(tmp_path: Path, fake_embedder) -> None:
    reranker = _FakeReranker(order_by_chunk_id={"b": 0.9, "a": 0.8, "c": 0.1})
    engine = SearchEngine(tmp_path, fake_embedder, reranker=reranker, rerank_candidate_limit=2)
    rows = [
        _ChunkRow(
            chunk_id="a",
            path="docs/a.md",
            content="a",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.5,
        ),
        _ChunkRow(
            chunk_id="b",
            path="docs/b.md",
            content="b",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.4,
        ),
        _ChunkRow(
            chunk_id="c",
            path="docs/c.md",
            content="c",
            start_line=1,
            end_line=1,
            frontmatter_json="{}",
            score=0.3,
        ),
    ]

    warnings: list[str] = []
    reranked = engine.rerank_orchestrator.rerank(rows, query="q", warnings=warnings)

    assert [candidate.chunk_id for candidate in reranker.last_candidates] == ["a", "b"]
    assert [row.chunk_id for row in reranked] == ["b", "a", "c"]
    assert any("Rerank considered the top 2 candidates" in warning for warning in warnings)
