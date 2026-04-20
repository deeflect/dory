from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dory_core.markdown_store import MarkdownStore
from dory_core.openclaw_parity import OpenClawParityStore
from dory_core.types import OpenClawParityDiagnostics

_MAX_STATUS_VECTOR_JSON_BYTES = 5_000_000


@dataclass(frozen=True, slots=True)
class DoryStatus:
    api_version: str
    corpus_root: str
    index_root: str
    corpus_files: int
    files_indexed: int
    chunks_indexed: int
    vectors_indexed: int
    openclaw: OpenClawParityDiagnostics
    compat_matrix: dict[str, str]


def build_status(corpus_root: Path, index_root: Path) -> DoryStatus:
    corpus_root = Path(corpus_root)
    index_root = Path(index_root)
    db_path = index_root / "dory.db"
    lance_path = index_root / "lance" / "chunks_vec.json"
    files_indexed = _count_sqlite_rows(db_path, "files")
    chunks_indexed = _count_sqlite_rows(db_path, "chunks")
    vectors_indexed = _count_sqlite_rows(db_path, "chunk_vectors") or _count_vector_rows(
        lance_path,
        fallback_count=chunks_indexed,
    )

    return DoryStatus(
        api_version="v1",
        corpus_root=str(corpus_root),
        index_root=str(index_root),
        corpus_files=files_indexed or _count_corpus_files(corpus_root),
        files_indexed=files_indexed,
        chunks_indexed=chunks_indexed,
        vectors_indexed=vectors_indexed,
        openclaw=_load_openclaw_diagnostics(db_path),
        compat_matrix={
            "wake": "ok",
            "search": "ok",
            "get": "ok",
            "memory-write": "ok",
            "recall-event": "ok",
            "public-artifacts": "ok",
            "status": "ok",
            "reindex": "ok",
        },
    )


def format_status(status: DoryStatus) -> str:
    payload = serialize_status(status)
    return json.dumps(payload, indent=2, sort_keys=True)


def serialize_status(status: DoryStatus) -> dict[str, Any]:
    payload = asdict(status)
    payload["openclaw"] = status.openclaw.model_dump(mode="json")
    return payload


def _count_corpus_files(corpus_root: Path) -> int:
    if not corpus_root.exists():
        return 0
    return len(MarkdownStore().walk(corpus_root))


def _count_sqlite_rows(db_path: Path, table: str) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as connection:
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0] if row is not None else 0)


def _count_vector_rows(records_path: Path, *, fallback_count: int) -> int:
    if not records_path.exists():
        return 0
    if records_path.stat().st_size > _MAX_STATUS_VECTOR_JSON_BYTES:
        return fallback_count
    return len(json.loads(records_path.read_text(encoding="utf-8")))


def _load_openclaw_diagnostics(db_path: Path) -> OpenClawParityDiagnostics:
    return OpenClawParityStore(db_path.parent, readonly=True).diagnostics()
