from __future__ import annotations

from dataclasses import dataclass

from dory_core.llm_rerank import RerankCandidate, RerankResult
from dory_core.rerank_orchestrator import RerankOrchestrator
from dory_core.search import _ChunkRow


@dataclass
class RecordingReranker:
    candidates: list[RerankCandidate] | None = None

    def rerank(self, *, query: str, candidates: list[RerankCandidate]) -> RerankResult:
        self.candidates = list(candidates)
        return RerankResult(
            ordered_chunk_ids=tuple(candidate.chunk_id for candidate in candidates),
            scores={candidate.chunk_id: 1.0 for candidate in candidates},
        )


def _row(chunk_id: str, path: str, content: str, *, score: float = 1.0) -> _ChunkRow:
    return _ChunkRow(
        chunk_id=chunk_id,
        path=path,
        content=content,
        start_line=1,
        end_line=1,
        frontmatter_json='{"title":"Test","type":"note"}',
        score=score,
    )


def test_rerank_uses_focused_query_window_instead_of_entire_chunk() -> None:
    reranker = RecordingReranker()
    orchestrator = RerankOrchestrator(reranker, candidate_limit=10)
    long_intro = "irrelevant filler " * 600
    long_outro = "more irrelevant filler " * 600
    row = _row("a", "notes/long.md", f"{long_intro}needle detail lives here with context.{long_outro}")

    orchestrator.rerank([row, _row("b", "notes/other.md", "needle elsewhere")], query="needle detail", warnings=[])

    assert reranker.candidates is not None
    focused = reranker.candidates[0].snippet
    assert "needle detail lives here" in focused
    assert len(focused) < 2500
    assert len(focused) < len(row.content) // 4


def test_rerank_focus_scores_paragraphs_instead_of_first_token_match() -> None:
    reranker = RecordingReranker()
    orchestrator = RerankOrchestrator(reranker, candidate_limit=10)
    early_weak = "needle appears here but not the rest. " + ("filler " * 300)
    later_strong = "needle detail context all appear together in this paragraph with the answer."
    row = _row("a", "notes/paragraphs.md", f"{early_weak}\n\n{later_strong}\n\n" + ("tail filler " * 300))

    orchestrator.rerank([row, _row("b", "notes/other.md", "needle detail context")], query="needle detail context", warnings=[])

    assert reranker.candidates is not None
    focused = reranker.candidates[0].snippet
    assert later_strong in focused
    assert "needle appears here but not the rest" not in focused


def test_rerank_diversifies_duplicate_path_candidates_before_calling_model() -> None:
    reranker = RecordingReranker()
    orchestrator = RerankOrchestrator(reranker, candidate_limit=3)
    rows = [
        _row("same-1", "projects/alpha/state.md", "alpha memory first", score=0.99),
        _row("same-2", "projects/alpha/state.md", "alpha memory duplicate", score=0.98),
        _row("same-3", "projects/alpha/state.md", "alpha memory duplicate again", score=0.97),
        _row("other", "projects/beta/state.md", "beta memory", score=0.96),
    ]

    result = orchestrator.rerank(rows, query="memory", warnings=[])

    assert reranker.candidates is not None
    assert [candidate.chunk_id for candidate in reranker.candidates] == ["same-1", "other", "same-2"]
    assert [row.chunk_id for row in result] == ["same-1", "other", "same-2", "same-3"]


def test_rerank_diversifies_semantically_redundant_candidates() -> None:
    reranker = RecordingReranker()
    orchestrator = RerankOrchestrator(reranker, candidate_limit=3)
    rows = [
        _row("a", "docs/a.md", "alpha beta gamma memory note", score=0.99),
        _row("b", "docs/b.md", "alpha beta gamma memory duplicate", score=0.98),
        _row("c", "docs/c.md", "delta epsilon zeta different note", score=0.97),
        _row("d", "docs/d.md", "alpha beta gamma another duplicate", score=0.96),
    ]

    orchestrator.rerank(rows, query="memory note", warnings=[])

    assert reranker.candidates is not None
    assert [candidate.chunk_id for candidate in reranker.candidates] == ["a", "c", "b"]


def test_rerank_telemetry_logs_safe_metrics_without_content(caplog) -> None:
    reranker = RecordingReranker()
    orchestrator = RerankOrchestrator(reranker, candidate_limit=10)
    sensitive_text = "sensitive fixture sentence should never be logged"
    row = _row("a", "notes/private.md", f"needle detail {sensitive_text}")

    with caplog.at_level("INFO", logger="dory_core.rerank_orchestrator"):
        orchestrator.rerank([row, _row("b", "notes/other.md", "needle")], query="needle detail", warnings=[])

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "rerank payload prepared" in messages
    assert "candidate_count=2" in messages
    assert "query_chars=13" in messages
    assert "snippet_chars_before=" in messages
    assert "snippet_chars_after=" in messages
    assert sensitive_text not in messages
    assert "needle detail" not in messages


def test_rerank_orchestrator_default_candidate_limit_matches_local_latency_budget() -> None:
    from dory_core.config import DorySettings

    settings = DorySettings()

    assert settings.query_reranker_candidate_limit == 8
    assert settings.local_reranker_timeout_seconds == 5.0
