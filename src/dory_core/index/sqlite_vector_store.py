from __future__ import annotations

import sqlite3
from array import array
from collections.abc import Iterable
from pathlib import Path

from dory_core.index.json_vector_store import JsonVectorStore, VectorRecord
from dory_core.index.migrations import apply_migrations


class SqliteVectorStore:
    """SQLite-backed vector store.

    The corpus is small enough for brute-force cosine ranking, but vectors
    should still live beside the chunk index so incremental writes do not
    rewrite a large JSON file.
    """

    def __init__(self, db_path: Path, dimension: int = 768) -> None:
        self.db_path = Path(db_path)
        self.dimension = dimension
        apply_migrations(self.db_path)

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        rows = [self._normalize_record(record) for record in records]
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executemany(
                """
                INSERT OR REPLACE INTO chunk_vectors(chunk_id, content_hash, vector, dimension)
                VALUES (:chunk_id, :content_hash, :vector, :dimension)
                """,
                rows,
            )
            connection.commit()
        return len(rows)

    def replace(self, records: Iterable[VectorRecord]) -> int:
        rows = [self._normalize_record(record) for record in records]
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM chunk_vectors")
            if rows:
                connection.executemany(
                    """
                    INSERT INTO chunk_vectors(chunk_id, content_hash, vector, dimension)
                    VALUES (:chunk_id, :content_hash, :vector, :dimension)
                    """,
                    rows,
                )
            connection.commit()
        return len(rows)

    def delete_many(self, chunk_ids: Iterable[str]) -> int:
        normalized_ids = sorted({str(chunk_id) for chunk_id in chunk_ids})
        if not normalized_ids:
            return 0
        with sqlite3.connect(self.db_path) as connection:
            placeholders = ", ".join("?" for _ in normalized_ids)
            cursor = connection.execute(
                f"""
                DELETE FROM chunk_vectors
                WHERE chunk_id IN ({placeholders})
                """,
                normalized_ids,
            )
            connection.commit()
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def get(self, chunk_id: str) -> VectorRecord | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT chunk_id, content_hash, vector, dimension
                FROM chunk_vectors
                WHERE chunk_id = ?
                """,
                (chunk_id,),
            ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def all(self) -> list[VectorRecord]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, content_hash, vector, dimension
                FROM chunk_vectors
                WHERE dimension = ?
                ORDER BY chunk_id
                """,
                (self.dimension,),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()
        return int(row[0] if row is not None else 0)

    def import_legacy_json_if_empty(self, legacy_root: Path) -> int:
        if self.count() > 0:
            return 0
        legacy_path = Path(legacy_root) / "chunks_vec.json"
        if not legacy_path.exists():
            return 0
        legacy_store = JsonVectorStore(Path(legacy_root), dimension=self.dimension)
        records = legacy_store.all()
        existing_chunk_ids = self._existing_chunk_ids(record.chunk_id for record in records)
        return self.upsert([record for record in records if record.chunk_id in existing_chunk_ids])

    def _normalize_record(self, record: VectorRecord) -> dict[str, object]:
        self._validate_record(record)
        return {
            "chunk_id": record.chunk_id,
            "content_hash": record.content_hash,
            "vector": _pack_vector(record.vector),
            "dimension": self.dimension,
        }

    def _validate_record(self, record: VectorRecord) -> None:
        if len(record.vector) != self.dimension:
            raise ValueError(
                f"vector for {record.chunk_id!r} has dimension {len(record.vector)}; expected {self.dimension}"
            )

    @staticmethod
    def _record_from_row(row: tuple[object, object, object, object]) -> VectorRecord:
        chunk_id, content_hash, vector_blob, _dimension = row
        return VectorRecord(
            chunk_id=str(chunk_id),
            content_hash=str(content_hash),
            vector=_unpack_vector(bytes(vector_blob)),
        )

    def _existing_chunk_ids(self, chunk_ids: Iterable[str]) -> set[str]:
        normalized_ids = sorted({str(chunk_id) for chunk_id in chunk_ids})
        if not normalized_ids:
            return set()
        with sqlite3.connect(self.db_path) as connection:
            placeholders = ", ".join("?" for _ in normalized_ids)
            rows = connection.execute(
                f"""
                SELECT chunk_id
                FROM chunks
                WHERE chunk_id IN ({placeholders})
                """,
                normalized_ids,
            ).fetchall()
        return {str(chunk_id) for (chunk_id,) in rows}


def _pack_vector(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack_vector(payload: bytes) -> list[float]:
    values = array("f")
    values.frombytes(payload)
    return list(values)
