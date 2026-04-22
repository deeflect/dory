from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dory_core.config import DorySettings
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
    index_present: bool
    index_stale: bool
    index_healthy: bool
    index_missing_files: int
    vector_drift: int
    last_reindex_at: str | None
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_batch_size: int
    query_reranker_enabled: bool
    query_reranker_provider: str | None
    query_reranker_model: str | None
    active_memory_llm_provider: str
    active_memory_llm_stages: str
    openclaw: OpenClawParityDiagnostics
    compat_matrix: dict[str, str]


def build_status(corpus_root: Path, index_root: Path, settings: DorySettings | None = None) -> DoryStatus:
    resolved_settings = settings or DorySettings()
    corpus_root = Path(corpus_root)
    index_root = Path(index_root)
    db_path = index_root / "dory.db"
    lance_path = index_root / "lance" / "chunks_vec.json"
    corpus_files = _count_corpus_files(corpus_root)
    files_indexed = _count_sqlite_rows(db_path, "files")
    chunks_indexed = _count_sqlite_rows(db_path, "chunks")
    vectors_indexed = _count_sqlite_rows(db_path, "chunk_vectors") or _count_vector_rows(
        lance_path,
        fallback_count=chunks_indexed,
    )
    index_missing_files = max(0, corpus_files - files_indexed)
    vector_drift = chunks_indexed - vectors_indexed
    index_present = db_path.exists() and files_indexed > 0
    index_stale = not index_present or index_missing_files > 0 or vector_drift != 0
    meta = _load_meta(db_path)

    return DoryStatus(
        api_version="v1",
        corpus_root=str(corpus_root),
        index_root=str(index_root),
        corpus_files=corpus_files,
        files_indexed=files_indexed,
        chunks_indexed=chunks_indexed,
        vectors_indexed=vectors_indexed,
        index_present=index_present,
        index_stale=index_stale,
        index_healthy=index_present and not index_stale,
        index_missing_files=index_missing_files,
        vector_drift=vector_drift,
        last_reindex_at=meta.get("last_reindex_at"),
        embedding_provider=resolved_settings.embedding_provider,
        embedding_model=_status_embedding_model(resolved_settings),
        embedding_dimensions=resolved_settings.embedding_dimensions,
        embedding_batch_size=resolved_settings.embedding_batch_size,
        query_reranker_enabled=resolved_settings.query_reranker_enabled,
        query_reranker_provider=(
            resolved_settings.query_reranker_provider if resolved_settings.query_reranker_enabled else None
        ),
        query_reranker_model=_status_reranker_model(resolved_settings),
        active_memory_llm_provider=resolved_settings.active_memory_llm_provider,
        active_memory_llm_stages=resolved_settings.active_memory_llm_stages,
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


def _status_embedding_model(settings: DorySettings) -> str:
    if settings.embedding_provider == "local":
        return settings.local_embedding_model
    return settings.embedding_model


def _status_reranker_model(settings: DorySettings) -> str | None:
    if not settings.query_reranker_enabled:
        return None
    if settings.query_reranker_provider == "local":
        return settings.local_reranker_model
    return settings.openrouter_query_model or settings.openrouter_model


def _count_corpus_files(corpus_root: Path) -> int:
    if not corpus_root.exists():
        return 0
    return len(MarkdownStore().walk(corpus_root))


def _count_sqlite_rows(db_path: Path, table: str) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path, timeout=0.25) as connection:
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0] if row is not None else 0)


def _load_meta(db_path: Path) -> dict[str, str]:
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path, timeout=0.25) as connection:
        try:
            rows = connection.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.OperationalError:
            return {}
    return {str(key): str(value) for key, value in rows}


def _count_vector_rows(records_path: Path, *, fallback_count: int) -> int:
    if not records_path.exists():
        return 0
    try:
        if records_path.stat().st_size > _MAX_STATUS_VECTOR_JSON_BYTES:
            return fallback_count
        payload = json.loads(records_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback_count
    return len(payload) if isinstance(payload, list) else fallback_count


def _load_openclaw_diagnostics(db_path: Path) -> OpenClawParityDiagnostics:
    return OpenClawParityStore(db_path.parent, readonly=True).diagnostics()
