from __future__ import annotations

from pathlib import Path

from watchdog.events import FileDeletedEvent, FileModifiedEvent, FileMovedEvent

from dory_core.index.reindex import ReindexResult
from dory_core.session_plane import SessionEvidencePlane, SessionSearchQuery
from dory_core.session_sync import sync_session_files
from dory_core.watch import MarkdownChangeHandler, is_markdown_change


def test_watch_handler_reindexes_changed_file(
    tmp_path: Path,
    fake_embedder: object,
) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (root / "core").mkdir(parents=True)
    (root / "core" / "user.md").write_text(
        """---
title: User
created: 2026-04-07
type: core
status: active
---

Hello world.
""",
        encoding="utf-8",
    )

    handler = MarkdownChangeHandler(root=root, index_root=index_root, embedder=fake_embedder)
    result = handler.on_modified(FileModifiedEvent(str(root / "core" / "user.md")))

    assert result is not None
    assert result.files_indexed == 1
    assert handler.last_result == result


def test_watch_ignores_non_markdown_changes(tmp_path: Path, fake_embedder: object) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    handler = MarkdownChangeHandler(root=root, index_root=index_root, embedder=fake_embedder)

    assert is_markdown_change(FileModifiedEvent(str(tmp_path / "notes.txt"))) is False
    assert handler.on_modified(FileModifiedEvent(str(tmp_path / "notes.txt"))) is None


def test_watch_handler_uses_incremental_reindex_for_relative_path(
    tmp_path: Path,
    fake_embedder: object,
    monkeypatch,
) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (root / "core").mkdir(parents=True)
    target = root / "core" / "user.md"
    target.write_text(
        """---
title: User
created: 2026-04-07
type: core
status: active
---

Hello world.
""",
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def _fake_reindex_paths(
        root_path: Path, index_path: Path, embedder: object, relative_paths: list[str]
    ) -> ReindexResult:
        assert root_path == root
        assert index_path == index_root
        assert embedder is fake_embedder
        calls.append(relative_paths)
        return ReindexResult(files_indexed=1, chunks_indexed=1, vectors_indexed=1)

    monkeypatch.setattr("dory_core.watch.reindex_paths", _fake_reindex_paths)

    handler = MarkdownChangeHandler(root=root, index_root=index_root, embedder=fake_embedder)
    result = handler.on_modified(FileModifiedEvent(str(target)))

    assert result is not None
    assert calls == [["core/user.md"]]


def test_watch_handler_reindexes_deleted_path(
    tmp_path: Path,
    fake_embedder: object,
    monkeypatch,
) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (root / "core").mkdir(parents=True)
    target = root / "core" / "user.md"

    calls: list[list[str]] = []

    def _fake_reindex_paths(
        root_path: Path, index_path: Path, embedder: object, relative_paths: list[str]
    ) -> ReindexResult:
        assert root_path == root
        assert index_path == index_root
        assert embedder is fake_embedder
        calls.append(relative_paths)
        return ReindexResult(files_indexed=0, chunks_indexed=0, vectors_indexed=0, skipped_files=1)

    monkeypatch.setattr("dory_core.watch.reindex_paths", _fake_reindex_paths)

    handler = MarkdownChangeHandler(root=root, index_root=index_root, embedder=fake_embedder)
    result = handler.on_deleted(FileDeletedEvent(str(target)))

    assert result is not None
    assert calls == [["core/user.md"]]


def test_watch_handler_reindexes_moved_paths(
    tmp_path: Path,
    fake_embedder: object,
    monkeypatch,
) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    (root / "core").mkdir(parents=True)
    source = root / "core" / "old.md"
    target = root / "core" / "new.md"

    calls: list[list[str]] = []

    def _fake_reindex_paths(
        root_path: Path, index_path: Path, embedder: object, relative_paths: list[str]
    ) -> ReindexResult:
        assert root_path == root
        assert index_path == index_root
        assert embedder is fake_embedder
        calls.append(relative_paths)
        return ReindexResult(files_indexed=1, chunks_indexed=1, vectors_indexed=1, skipped_files=1)

    monkeypatch.setattr("dory_core.watch.reindex_paths", _fake_reindex_paths)

    handler = MarkdownChangeHandler(root=root, index_root=index_root, embedder=fake_embedder)
    result = handler.on_moved(FileMovedEvent(str(source), str(target)))

    assert result is not None
    assert calls == [["core/old.md", "core/new.md"]]


def test_session_file_sync_uses_session_plane_not_durable_reindex(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    target = root / "logs" / "sessions" / "codex" / "mini" / "2026-04-22-s1.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        """---
title: Session
type: session
agent: codex
device: mini
session_id: s1
updated: 2026-04-22T12:00:00Z
---

Dory watcher session sync.
""",
        encoding="utf-8",
    )

    result = sync_session_files(
        root,
        index_root / "session_plane.db",
        ["logs/sessions/codex/mini/2026-04-22-s1.md"],
    )

    assert result.docs_indexed == 1
    response = SessionEvidencePlane(index_root / "session_plane.db").search(
        SessionSearchQuery(query="watcher session")
    )
    assert response.count == 1
