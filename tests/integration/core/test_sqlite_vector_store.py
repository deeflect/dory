from __future__ import annotations

from pathlib import Path

from dory_core.index.json_vector_store import JsonVectorStore, VectorRecord
from dory_core.index.sqlite_store import SqliteStore
from dory_core.index.sqlite_vector_store import SqliteVectorStore


def test_sqlite_vector_store_persists_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "dory.db"
    _seed_chunk(db_path, chunk_id="chunk-1")
    store = SqliteVectorStore(db_path, dimension=4)
    record = VectorRecord(
        chunk_id="chunk-1",
        content_hash="sha256:abc",
        vector=[1.0, 2.0, 3.0, 4.0],
    )

    written = store.upsert([record])

    assert written == 1
    assert store.count() == 1
    assert store.get("chunk-1") == record

    reloaded = SqliteVectorStore(db_path, dimension=4)
    assert reloaded.get("chunk-1") == record


def test_sqlite_vector_store_imports_legacy_json_when_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "dory.db"
    legacy_root = tmp_path / "lance"
    _seed_chunk(db_path, chunk_id="chunk-1")
    legacy = JsonVectorStore(legacy_root, dimension=4)
    record = VectorRecord(
        chunk_id="chunk-1",
        content_hash="sha256:abc",
        vector=[1.0, 0.0, 0.0, 0.0],
    )
    legacy.upsert([record])
    store = SqliteVectorStore(db_path, dimension=4)

    imported = store.import_legacy_json_if_empty(legacy_root)
    imported_again = store.import_legacy_json_if_empty(legacy_root)

    assert imported == 1
    assert imported_again == 0
    assert store.get("chunk-1") == record


def _seed_chunk(db_path: Path, *, chunk_id: str) -> None:
    store = SqliteStore(db_path)
    store.replace_documents(
        [
            {
                "path": "inbox/vector.md",
                "hash": "sha256:file",
                "mtime": "0",
                "size": 1,
                "frontmatter": {"title": "Vector", "type": "capture"},
            }
        ],
        [
            {
                "chunk_id": chunk_id,
                "path": "inbox/vector.md",
                "chunk_index": 0,
                "content": "Vector body",
                "start_line": 1,
                "end_line": 1,
                "hash": "sha256:chunk",
                "frontmatter": {"title": "Vector", "type": "capture"},
            }
        ],
    )
