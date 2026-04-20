from __future__ import annotations

from pathlib import Path

from dory_core.index.sqlite_store import SqliteStore


def test_replace_documents_writes_rows(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "dory.db")

    store.replace_documents(
        files=[
            {
                "path": "core/user.md",
                "hash": "sha256:file-1",
                "mtime": "2026-04-07T00:00:00Z",
                "size": 123,
                "frontmatter": {"title": "User", "type": "core"},
            }
        ],
        chunks=[
            {
                "chunk_id": "core/user.md#0",
                "path": "core/user.md",
                "chunk_index": 0,
                "content": "hello world",
                "start_line": 1,
                "end_line": 2,
                "hash": "sha256:chunk-1",
                "frontmatter": {"title": "User", "type": "core"},
            }
        ],
    )

    assert store.count_rows("files") == 1
    assert store.count_rows("chunks") == 1
    assert store.count_rows("chunks_fts") == 1
