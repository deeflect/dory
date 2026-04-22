from __future__ import annotations

from pathlib import Path

from dory_core.session_plane import SessionEvidencePlane, SessionSearchQuery
from dory_core.session_sync import plan_session_sync, sync_session_files


def test_session_sync_indexes_session_logs_into_session_plane(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    session_path = corpus_root / "logs" / "sessions" / "codex" / "mini" / "2026-04-22-abc.md"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        """---
title: Codex session abc
type: session
status: closed
agent: codex
device: mini
session_id: abc
updated: 2026-04-22T12:00:00Z
---

Discussed Dory session plane indexing.
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "index" / "session_plane.db"
    result = sync_session_files(corpus_root, db_path)

    assert result.docs_indexed == 1
    assert result.docs_deleted == 0

    response = SessionEvidencePlane(db_path).search(SessionSearchQuery(query="session plane"))
    assert response.count == 1
    assert response.results[0].path == "logs/sessions/codex/mini/2026-04-22-abc.md"


def test_session_sync_deletes_removed_session_logs(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    db_path = tmp_path / "index" / "session_plane.db"
    plane = SessionEvidencePlane(db_path)
    plane.upsert_session_chunk(
        path="logs/sessions/codex/mini/2026-04-22-old.md",
        content="old session",
        updated="2026-04-22T12:00:00Z",
        agent="codex",
        device="mini",
        session_id="old",
        status="closed",
    )

    result = sync_session_files(corpus_root, db_path)

    assert result.docs_deleted == 1
    assert plane.count_docs() == 0


def test_session_sync_plan_reports_missing_and_stale_docs(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    session_path = corpus_root / "logs" / "sessions" / "codex" / "mini" / "2026-04-22-new.md"
    session_path.parent.mkdir(parents=True)
    session_path.write_text("---\ntitle: New\ntype: session\n---\n\nnew session\n", encoding="utf-8")
    db_path = tmp_path / "index" / "session_plane.db"
    SessionEvidencePlane(db_path).upsert_session_chunk(
        path="logs/sessions/codex/mini/2026-04-22-old.md",
        content="old session",
        updated="2026-04-22T12:00:00Z",
        agent="codex",
        device="mini",
        session_id="old",
        status="closed",
    )

    plan = plan_session_sync(corpus_root, db_path)

    assert plan.session_files == 1
    assert plan.session_docs_indexed == 1
    assert plan.missing_docs == 1
    assert plan.stale_docs == 1
