from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.session_plane import SessionEvidencePlane
from dory_core.types import SearchReq


def test_recall_mode_uses_session_plane_only(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    index_root = tmp_path / "index"
    reindex_corpus(sample_corpus_root, index_root, fake_embedder)
    SessionEvidencePlane(index_root / "session_plane.db").upsert_session_chunk(
        path="logs/sessions/claude/macbook/2026-04-12-s1.md",
        content="We cleaned up SOUL yesterday.",
        updated="2026-04-12T10:00:00Z",
        agent="claude",
        device="macbook",
        session_id="s1",
        status="active",
    )

    engine = SearchEngine(index_root, fake_embedder)
    response = engine.search(SearchReq(query="cleaned up SOUL", mode="recall", k=5))

    assert response.results
    assert all(result.path.startswith("logs/sessions/") for result in response.results)


def test_hybrid_search_falls_back_to_session_plane_when_durable_is_weak(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    index_root = tmp_path / "index"
    reindex_corpus(sample_corpus_root, index_root, fake_embedder)
    SessionEvidencePlane(index_root / "session_plane.db").upsert_session_chunk(
        path="logs/sessions/codex/mac/2026-04-12-s2.md",
        content="Rooster is the active focus this week.",
        updated="2026-04-12T10:00:00Z",
        agent="codex",
        device="mac",
        session_id="s2",
        status="active",
    )

    engine = SearchEngine(index_root, fake_embedder)
    response = engine.search(SearchReq(query="Rooster active focus", mode="hybrid", k=5))

    assert any(result.path.startswith("logs/sessions/") for result in response.results)
