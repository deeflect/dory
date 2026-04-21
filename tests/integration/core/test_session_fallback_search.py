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


def test_hybrid_search_falls_back_to_session_plane_for_recent_work_queries(
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
    response = engine.search(SearchReq(query="recent Rooster active focus session", mode="hybrid", k=5))

    assert any(result.path.startswith("logs/sessions/") for result in response.results)


def test_hybrid_all_demotes_sessions_for_generic_project_queries(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "projects" / "dory").mkdir(parents=True)
    (corpus_root / "core" / "env.md").write_text(
        """---
title: Environment
type: core
status: active
canonical: true
source_kind: canonical
---

Dory Docker deployment runs behind the local HTTPS gateway.
""",
        encoding="utf-8",
    )
    (corpus_root / "projects" / "dory" / "state.md").write_text(
        """---
title: Dory
type: project
status: active
canonical: true
source_kind: canonical
---

Dory Docker MCP deployment work is tracked here.
""",
        encoding="utf-8",
    )
    reindex_corpus(corpus_root, index_root, fake_embedder)
    SessionEvidencePlane(index_root / "session_plane.db").upsert_session_chunk(
        path="logs/sessions/codex/mac/2026-04-20-dory-docker.md",
        content="Dory Docker MCP deployment benchmark transcript with the exact same terms.",
        updated="2026-04-20T10:00:00Z",
        agent="codex",
        device="mac",
        session_id="dory-docker",
        status="done",
    )

    engine = SearchEngine(index_root, fake_embedder)
    response = engine.search(SearchReq(query="Dory Docker MCP deployment", mode="hybrid", corpus="all", k=5))

    assert response.results
    assert response.results[0].path in {"core/env.md", "projects/dory/state.md"}
    assert response.results[0].evidence_class == "canonical"
    assert any(result.path.startswith("logs/sessions/") for result in response.results)
    assert not response.results[0].path.startswith("logs/sessions/")


def test_hybrid_all_suppresses_live_session_tail_for_generic_project_queries(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "projects" / "dory").mkdir(parents=True)
    (corpus_root / "projects" / "dory" / "state.md").write_text(
        """---
title: Dory
type: project
status: active
canonical: true
source_kind: canonical
---

Dory Docker MCP deployment work is tracked in canonical project state.
""",
        encoding="utf-8",
    )
    reindex_corpus(corpus_root, index_root, fake_embedder)
    SessionEvidencePlane(index_root / "session_plane.db").upsert_session_chunk(
        path="logs/sessions/codex/mac/2026-04-20-live-benchmark.md",
        content="Live benchmark transcript repeats Dory Docker MCP deployment terms.",
        updated="2026-04-20T10:00:00Z",
        agent="codex",
        device="mac",
        session_id="live-benchmark",
        status="active",
    )

    engine = SearchEngine(index_root, fake_embedder)
    response = engine.search(SearchReq(query="Dory Docker MCP deployment", mode="hybrid", corpus="all", k=5))

    assert response.results
    assert [result.path for result in response.results] == ["projects/dory/state.md"]


def test_hybrid_all_keeps_live_session_results_when_query_asks_for_sessions(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (corpus_root / "projects" / "dory").mkdir(parents=True)
    (corpus_root / "projects" / "dory" / "state.md").write_text(
        """---
title: Dory
type: project
status: active
canonical: true
source_kind: canonical
---

Dory Docker MCP deployment work is tracked in canonical project state.
""",
        encoding="utf-8",
    )
    reindex_corpus(corpus_root, index_root, fake_embedder)
    SessionEvidencePlane(index_root / "session_plane.db").upsert_session_chunk(
        path="logs/sessions/codex/mac/2026-04-20-live-benchmark.md",
        content="Live benchmark transcript repeats Dory Docker MCP deployment terms.",
        updated="2026-04-20T10:00:00Z",
        agent="codex",
        device="mac",
        session_id="live-benchmark",
        status="active",
    )

    engine = SearchEngine(index_root, fake_embedder)
    response = engine.search(
        SearchReq(query="recent session Dory Docker MCP deployment", mode="hybrid", corpus="all", k=5)
    )

    assert any(result.path.startswith("logs/sessions/") for result in response.results)
