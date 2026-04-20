from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path

from .migrations import apply_migrations


class SqliteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        apply_migrations(self.db_path)

    def replace_documents(
        self,
        files: Iterable[Mapping[str, object]],
        chunks: Iterable[Mapping[str, object]],
        *,
        embedding_cache: Mapping[str, str] | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM chunks_fts")
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM files")

            file_rows = [self._normalize_file_row(row) for row in files]
            chunk_rows = [self._normalize_chunk_row(row) for row in chunks]

            connection.executemany(
                """
                INSERT INTO files(path, hash, mtime, size, frontmatter_json)
                VALUES (:path, :hash, :mtime, :size, :frontmatter_json)
                """,
                file_rows,
            )
            connection.executemany(
                """
                INSERT INTO chunks(
                    chunk_id, path, chunk_index, content, start_line, end_line, hash, frontmatter_json
                )
                VALUES (
                    :chunk_id, :path, :chunk_index, :content, :start_line, :end_line, :hash, :frontmatter_json
                )
                """,
                chunk_rows,
            )
            connection.executemany(
                """
                INSERT INTO chunks_fts(content, path, chunk_id)
                VALUES (:content, :path, :chunk_id)
                """,
                chunk_rows,
            )
            if embedding_cache is not None:
                connection.execute("DELETE FROM embedding_cache_meta")
                connection.executemany(
                    """
                    INSERT INTO embedding_cache_meta(content_hash, vector_id)
                    VALUES (?, ?)
                    """,
                    sorted(embedding_cache.items()),
                )
            if meta is not None:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO meta(key, value)
                    VALUES (?, ?)
                    """,
                    [(key, str(value)) for key, value in sorted(meta.items())],
                )
            connection.commit()

    def count_rows(self, table: str) -> int:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return int(row[0] if row is not None else 0)

    def load_chunk_ids_for_paths(self, paths: Iterable[str]) -> list[str]:
        normalized_paths = sorted({str(path) for path in paths})
        if not normalized_paths:
            return []
        with sqlite3.connect(self.db_path) as connection:
            placeholders = ", ".join("?" for _ in normalized_paths)
            rows = connection.execute(
                f"""
                SELECT chunk_id
                FROM chunks
                WHERE path IN ({placeholders})
                """,
                normalized_paths,
            ).fetchall()
        return [str(chunk_id) for (chunk_id,) in rows]

    def upsert_documents(
        self,
        files: Iterable[Mapping[str, object]],
        chunks: Iterable[Mapping[str, object]],
        *,
        delete_paths: Iterable[str] = (),
        embedding_cache: Mapping[str, str] | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> None:
        file_rows = [self._normalize_file_row(row) for row in files]
        chunk_rows = [self._normalize_chunk_row(row) for row in chunks]
        delete_targets = sorted({str(path) for path in delete_paths})

        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for path in delete_targets:
                connection.execute("DELETE FROM chunks_fts WHERE path = ?", (path,))
                connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
                connection.execute("DELETE FROM files WHERE path = ?", (path,))

            if file_rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO files(path, hash, mtime, size, frontmatter_json)
                    VALUES (:path, :hash, :mtime, :size, :frontmatter_json)
                    """,
                    file_rows,
                )
            if chunk_rows:
                connection.executemany(
                    """
                    INSERT INTO chunks(
                        chunk_id, path, chunk_index, content, start_line, end_line, hash, frontmatter_json
                    )
                    VALUES (
                        :chunk_id, :path, :chunk_index, :content, :start_line, :end_line, :hash, :frontmatter_json
                    )
                    """,
                    chunk_rows,
                )
                connection.executemany(
                    """
                    INSERT INTO chunks_fts(content, path, chunk_id)
                    VALUES (:content, :path, :chunk_id)
                    """,
                    chunk_rows,
                )
            if embedding_cache is not None:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO embedding_cache_meta(content_hash, vector_id)
                    VALUES (?, ?)
                    """,
                    sorted(embedding_cache.items()),
                )
            if meta is not None:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO meta(key, value)
                    VALUES (?, ?)
                    """,
                    [(key, str(value)) for key, value in sorted(meta.items())],
                )
            connection.commit()

    def load_embedding_cache(self) -> dict[str, str]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT content_hash, vector_id
                FROM embedding_cache_meta
                """
            ).fetchall()
            return {str(content_hash): str(vector_id) for content_hash, vector_id in rows}

    def load_meta(self) -> dict[str, str]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT key, value
                FROM meta
                """
            ).fetchall()
            return {str(key): str(value) for key, value in rows}

    @staticmethod
    def _normalize_file_row(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "path": str(row["path"]),
            "hash": str(row["hash"]),
            "mtime": str(row.get("mtime", "")),
            "size": int(row.get("size", 0)),
            "frontmatter_json": json.dumps(row.get("frontmatter", {}), sort_keys=True),
        }

    @staticmethod
    def _normalize_chunk_row(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "chunk_id": str(row["chunk_id"]),
            "path": str(row["path"]),
            "chunk_index": int(row["chunk_index"]),
            "content": str(row["content"]),
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "hash": str(row["hash"]),
            "frontmatter_json": json.dumps(row.get("frontmatter", {}), sort_keys=True),
        }
