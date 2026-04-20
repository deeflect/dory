from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Sequence

from dory_core.fs import atomic_write_text


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: str
    content_hash: str
    vector: list[float]


class JsonVectorStore:
    """JSON-backed vector store. Rewrites the whole file on every upsert.

    Intended as a local-first placeholder. Replace with a real vector index
    (LanceDB, sqlite-vss, hnswlib) when the corpus outgrows brute-force
    cosine search or when upsert throughput becomes a bottleneck.
    """

    def __init__(self, root: Path, dimension: int = 768) -> None:
        self.root = root
        self.dimension = dimension
        self.root.mkdir(parents=True, exist_ok=True)
        self._records_path = self.root / "chunks_vec.json"
        self._records: dict[str, VectorRecord] = self._load()

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        written = 0
        for record in records:
            self._validate_record(record)
            self._records[record.chunk_id] = record
            written += 1
        self._persist()
        return written

    def replace(self, records: Iterable[VectorRecord]) -> int:
        self._records = {}
        return self.upsert(records)

    def delete_many(self, chunk_ids: Iterable[str]) -> int:
        deleted = 0
        for chunk_id in chunk_ids:
            if chunk_id in self._records:
                del self._records[chunk_id]
                deleted += 1
        if deleted:
            self._persist()
        return deleted

    def get(self, chunk_id: str) -> VectorRecord | None:
        return self._records.get(chunk_id)

    def all(self) -> list[VectorRecord]:
        return list(self._records.values())

    def count(self) -> int:
        return len(self._records)

    def _validate_record(self, record: VectorRecord) -> None:
        if len(record.vector) != self.dimension:
            raise ValueError(
                f"vector for {record.chunk_id!r} has dimension {len(record.vector)}; "
                f"expected {self.dimension}"
            )

    def _load(self) -> dict[str, VectorRecord]:
        if not self._records_path.exists():
            return {}

        data = json.loads(self._records_path.read_text(encoding="utf-8"))
        return {
            item["chunk_id"]: VectorRecord(
                chunk_id=item["chunk_id"],
                content_hash=item["content_hash"],
                vector=list(item["vector"]),
            )
            for item in data
        }

    def _persist(self) -> None:
        payload = [asdict(record) for record in self._records.values()]
        atomic_write_text(
            self._records_path,
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
