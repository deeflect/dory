from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.session_plane import SessionEvidencePlane
from dory_core.types import SearchReq, SearchScope


@dataclass(frozen=True, slots=True)
class KeywordEmbedder:
    dimension: int = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    1.0 if "private mesh vpn" in lowered else 0.0,
                    1.0 if "homeserver" in lowered else 0.0,
                    1.0 if "daemon" in lowered else 0.0,
                    1.0 if "memory" in lowered else 0.0,
                ]
            )
        return vectors


def test_search_engine_supports_all_modes(
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    embedder = KeywordEmbedder()
    reindex_corpus(sample_corpus_root, tmp_path, embedder)
    engine = SearchEngine(tmp_path, embedder)

    bm25 = engine.search(SearchReq(query="private mesh VPN", mode="bm25", k=3))
    vector = engine.search(SearchReq(query="private mesh VPN", mode="vector", k=3))
    hybrid = engine.search(SearchReq(query="private mesh VPN", mode="hybrid", k=3))

    assert bm25.results[0].path == "core/env.md"
    assert vector.results[0].path == "core/env.md"
    assert hybrid.results[0].path == "core/env.md"
    assert hybrid.count == 3


def test_search_result_surfaces_stale_warning_for_timeline_docs(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    target = corpus_root / "projects" / "dory" / "state.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """---
title: Dory state
type: project
status: active
canonical: true
created: 2026-04-01
updated: 2026-04-01
---

Current state summary.

<!-- TIMELINE: append-only below this line -->

- 2026-04-10: Added OpenRouter dreaming tools.
""",
        encoding="utf-8",
    )

    embedder = KeywordEmbedder()
    reindex_corpus(corpus_root, index_root, embedder)
    engine = SearchEngine(index_root, embedder)

    result = engine.search(SearchReq(query="OpenRouter dreaming", mode="hybrid", k=1))

    assert result.results[0].path == "projects/dory/state.md"
    assert result.results[0].stale_warning is not None


def test_search_records_recall_log_rows(
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    embedder = KeywordEmbedder()
    reindex_corpus(sample_corpus_root, tmp_path, embedder)
    engine = SearchEngine(tmp_path, embedder)

    response = engine.search(SearchReq(query="private mesh VPN", mode="hybrid", k=3))

    assert response.results
    with sqlite3.connect(tmp_path / "dory.db") as connection:
        row = connection.execute("SELECT query, chunk_ids FROM recall_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "private mesh VPN"
    assert "core/env.md" in row[1]


def test_search_scope_filters_apply_to_durable_results(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "people").mkdir(parents=True)
    (corpus_root / "projects" / "rooster").mkdir(parents=True)

    (corpus_root / "people" / "alex.md").write_text(
        """---
title: Alex
type: person
status: active
tags:
  - collaborator
created: 2026-04-07
---

Alex owns architecture review notes.
""",
        encoding="utf-8",
    )
    (corpus_root / "projects" / "rooster" / "state.md").write_text(
        """---
title: Rooster
type: project
status: paused
tags:
  - delivery
created: 2026-04-09
---

Alex paused Rooster after the architecture review.
""",
        encoding="utf-8",
    )

    embedder = KeywordEmbedder()
    reindex_corpus(corpus_root, index_root, embedder)
    engine = SearchEngine(index_root, embedder)

    response = engine.search(
        SearchReq(
            query="Alex architecture review",
            mode="hybrid",
            k=5,
            scope=SearchScope(
                path_glob="people/*.md",
                type=["person"],
                status=["active"],
                tags=["collaborator"],
                since="2026-04-01",
                until="2026-04-08",
            ),
        )
    )

    assert [result.path for result in response.results] == ["people/alex.md"]


def test_search_dedupes_multiple_chunks_from_same_path(tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, KeywordEmbedder())
    rows = [
        _make_chunk_row("projects/dory/state.md#0", "projects/dory/state.md", "Dory deployment."),
        _make_chunk_row("projects/dory/state.md#1", "projects/dory/state.md", "HomeServer deployment."),
        _make_chunk_row("core/env.md#0", "core/env.md", "HomeServer environment."),
    ]
    engine._bm25 = lambda query, limit: rows  # type: ignore[method-assign]

    response = engine.search(SearchReq(query="Dory HomeServer deployment", mode="bm25", k=3))

    assert [result.path for result in response.results] == ["projects/dory/state.md", "core/env.md"]


def test_search_suppresses_quarantine_noise_by_default(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "logs" / "daily").mkdir(parents=True)

    (corpus_root / "core" / "env.md").write_text(
        """---
title: Environment
type: core
status: active
canonical: true
source_kind: canonical
---

Dory runs on the HomeServer behind https://dory.example.test.
""",
        encoding="utf-8",
    )
    (corpus_root / "logs" / "daily" / "2026-04-08.md").write_text(
        """---
title: Quarantined Dory deployment note
type: daily
status: raw
canonical: false
migration_quarantined: true
---

Dory HomeServer deployment details from a noisy import.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    response = engine.search(SearchReq(query="Dory HomeServer deployment", mode="hybrid", k=5))

    assert response.results
    assert response.results[0].path == "core/env.md"
    assert all(result.path != "logs/daily/2026-04-08.md" for result in response.results)


def test_search_privacy_queries_prefer_canonical_boundaries_over_raw_personal(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "knowledge" / "personal").mkdir(parents=True)

    (corpus_root / "core" / "user.md").write_text(
        """---
title: User
type: core
status: active
canonical: true
source_kind: canonical
---

Private boundaries: sensitive category alpha is private. Sensitive category beta stays private.
""",
        encoding="utf-8",
    )
    (corpus_root / "knowledge" / "personal" / "raw-sensitive-note.md").write_text(
        """---
title: Raw sensitive notes
type: knowledge
status: active
canonical: false
source_kind: imported
---

Raw sensitive category alpha and beta details. Use privacy boundaries before repeating.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    response = engine.search(SearchReq(query="private boundaries sensitive categories", mode="hybrid", k=5))

    assert response.results
    assert response.results[0].path == "core/user.md"
    assert response.results[0].evidence_class == "canonical"


def test_privacy_queries_prefer_boundaries_over_session_logs(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "user.md").write_text(
        """---
title: User
type: core
status: active
canonical: true
source_kind: canonical
---

Private boundaries: legal status details and financial specifics stay private.
""",
        encoding="utf-8",
    )
    reindex_corpus(corpus_root, index_root, fake_embedder)
    SessionEvidencePlane(index_root / "session_plane.db").upsert_session_chunk(
        path="logs/sessions/codex/mac/2026-04-20-private-boundaries.md",
        content="Raw session mentions private boundaries crypto legal status specifics.",
        updated="2026-04-20T10:00:00Z",
        agent="codex",
        device="mac",
        session_id="private-boundaries",
        status="done",
    )

    engine = SearchEngine(index_root, fake_embedder)
    response = engine.search(
        SearchReq(query="private boundaries crypto legal status", mode="hybrid", corpus="all", k=5)
    )

    assert response.results
    assert response.results[0].path == "core/user.md"
    assert response.results[0].evidence_class == "canonical"
    assert not response.results[0].path.startswith("logs/sessions/")


def test_search_hybrid_prefers_canonical_over_raw_inbox_capture(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "inbox").mkdir(parents=True)

    (corpus_root / "core" / "env.md").write_text(
        """---
title: Environment
type: core
status: active
canonical: true
source_kind: canonical
---

Dory neutral deployment details are in the canonical environment page.
""",
        encoding="utf-8",
    )
    (corpus_root / "inbox" / "neutral-deployment.md").write_text(
        """---
title: Raw deployment capture
type: capture
status: raw
canonical: false
source_kind: imported
---

Dory neutral deployment details from a raw inbox capture.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    response = engine.search(SearchReq(query="Dory neutral deployment details", mode="hybrid", k=5))

    assert response.results
    assert response.results[0].path == "core/env.md"
    assert response.results[0].rank_score == 1.0
    inbox_result = next(result for result in response.results if result.path == "inbox/neutral-deployment.md")
    assert inbox_result.evidence_class == "inbox"


def test_search_collapses_generated_mirrors_behind_canonical_docs(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "projects" / "dory").mkdir(parents=True)
    (corpus_root / "wiki" / "projects").mkdir(parents=True)
    (corpus_root / "sources" / "semantic" / "2026" / "04" / "20").mkdir(parents=True)
    (corpus_root / "core").mkdir(parents=True)
    duplicate_body = (
        "Dory hardening added canonical search ranking, Docker MCP deployment checks, "
        "and active memory filtering for agent benchmark follow-up work."
    )
    (corpus_root / "projects" / "dory" / "state.md").write_text(
        f"""---
title: Dory
type: project
status: active
canonical: true
source_kind: canonical
---

{duplicate_body}
""",
        encoding="utf-8",
    )
    (corpus_root / "wiki" / "projects" / "dory.md").write_text(
        f"""---
title: Dory wiki
type: wiki
status: active
canonical: false
source_kind: generated
---

{duplicate_body}
""",
        encoding="utf-8",
    )
    (corpus_root / "sources" / "semantic" / "2026" / "04" / "20" / "dory-write.md").write_text(
        f"""---
title: Dory semantic source
type: source
status: done
canonical: false
source_kind: semantic
canonical_target: projects/dory/state.md
---

{duplicate_body}
""",
        encoding="utf-8",
    )
    (corpus_root / "core" / "env.md").write_text(
        """---
title: Environment
type: core
status: active
canonical: true
source_kind: canonical
---

Dory Docker MCP deployment also depends on the runtime environment.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    response = engine.search(SearchReq(query="Dory hardening Docker MCP deployment", mode="hybrid", k=3))

    paths = [result.path for result in response.results]
    assert "projects/dory/state.md" in paths
    assert "core/env.md" in paths
    assert "wiki/projects/dory.md" not in paths
    assert "sources/semantic/2026/04/20/dory-write.md" not in paths


def test_exact_search_returns_only_literal_matches_for_cleanup_markers(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "inbox").mkdir(parents=True)
    (corpus_root / "projects" / "dory").mkdir(parents=True)

    marker = "unique-cleanup-marker-20260418"
    (corpus_root / "inbox" / "probe.md").write_text(
        f"""---
title: Probe
type: capture
status: raw
canonical: false
---

This file contains {marker}.
""",
        encoding="utf-8",
    )
    (corpus_root / "projects" / "dory" / "state.md").write_text(
        """---
title: Dory
type: project
status: active
canonical: true
---

Dory has cleanup work but not the exact marker.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    hit = engine.search(SearchReq(query=marker, mode="exact", k=5))
    miss = engine.search(SearchReq(query="unique-cleanup-marker-missing", mode="exact", k=5))

    assert [result.path for result in hit.results] == ["inbox/probe.md"]
    assert hit.results[0].confidence == "high"
    assert miss.results == []


def test_search_results_include_confidence_bands(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "env.md").write_text(
        """---
title: Environment
type: core
status: active
canonical: true
source_kind: canonical
---

HomeServer runs Dory behind https://dory.example.test.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    response = engine.search(SearchReq(query="HomeServer Dory", mode="hybrid", k=1))

    assert response.results
    assert response.results[0].confidence in {"medium", "high"}


def test_search_results_include_normalized_scores(tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, KeywordEmbedder())
    rows = [
        _make_chunk_row("core/env.md#0", "core/env.md", "HomeServer environment.", score=0.9),
        _make_chunk_row("projects/dory/state.md#0", "projects/dory/state.md", "Dory deployment.", score=0.3),
    ]
    engine._vector = lambda query, limit: rows  # type: ignore[method-assign]

    response = engine.search(SearchReq(query="Dory HomeServer", mode="vector", k=2))

    assert response.results[0].score_normalized == 1.0
    assert response.results[0].rank_score == 1.0
    assert response.results[0].evidence_class == "canonical"
    assert response.results[1].score_normalized == 0.0


def _make_chunk_row(chunk_id: str, path: str, content: str, *, score: float = 1.0):
    from dory_core.search import _ChunkRow

    return _ChunkRow(
        chunk_id=chunk_id,
        path=path,
        content=(f"---\ntitle: Test\ntype: project\nstatus: active\ncanonical: true\n---\n\n{content}\n"),
        start_line=1,
        end_line=6,
        frontmatter_json='{"title":"Test","type":"project","status":"active","canonical":true}',
        score=score,
    )
