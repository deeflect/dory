from __future__ import annotations

import json
import sqlite3

from dory_core import status as status_module
from dory_core.status import DoryStatus, _count_sqlite_rows, _count_vector_rows, serialize_status
from dory_core.types import OpenClawParityDiagnostics


def test_count_vector_rows_uses_fallback_for_large_json(monkeypatch, tmp_path) -> None:
    records_path = tmp_path / "chunks_vec.json"
    records_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(status_module, "_MAX_STATUS_VECTOR_JSON_BYTES", 1)

    def fail_json_loads(_payload: str):
        raise AssertionError("large status vector JSON should not be parsed")

    monkeypatch.setattr(json, "loads", fail_json_loads)

    assert _count_vector_rows(records_path, fallback_count=42) == 42


def test_count_vector_rows_parses_small_json(tmp_path) -> None:
    records_path = tmp_path / "chunks_vec.json"
    records_path.write_text('[{"chunk_id":"a"}]', encoding="utf-8")

    assert _count_vector_rows(records_path, fallback_count=42) == 1


def test_count_sqlite_rows_returns_zero_for_missing_table(tmp_path) -> None:
    db_path = tmp_path / "dory.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE files(path TEXT)")

    assert _count_sqlite_rows(db_path, "chunk_vectors") == 0


def test_serialize_status_hides_debug_fields_by_default() -> None:
    status = DoryStatus(
        api_version="v1",
        corpus_root="/var/lib/dory",
        index_root="/var/lib/dory/.index",
        corpus_files=10,
        session_files=2,
        session_docs_indexed=2,
        session_missing_docs=0,
        session_stale_docs=0,
        files_indexed=10,
        chunks_indexed=20,
        vectors_indexed=20,
        index_present=True,
        index_stale=False,
        index_healthy=True,
        index_missing_files=0,
        vector_drift=0,
        last_reindex_at="2026-04-23T00:00:00Z",
        embedding_provider="local",
        embedding_model="qwen3-embed",
        embedding_dimensions=1024,
        embedding_batch_size=16,
        query_reranker_enabled=True,
        query_reranker_provider="local",
        query_reranker_model="qwen3-rerank",
        active_memory_llm_provider="local",
        active_memory_llm_stages="compose",
        openclaw=OpenClawParityDiagnostics(
            flush_enabled=False,
            recall_tracking_enabled=True,
            artifact_listing_enabled=True,
        ),
        compat_matrix={"wake": "ok"},
    )

    payload = serialize_status(status)

    assert "corpus_root" not in payload
    assert "index_root" not in payload
    assert "embedding_batch_size" not in payload
    assert "openclaw" not in payload
    assert "compat_matrix" not in payload
    assert payload["embedding_model"] == "qwen3-embed"
    assert payload["index_healthy"] is True


def test_serialize_status_keeps_debug_fields_when_requested() -> None:
    status = DoryStatus(
        api_version="v1",
        corpus_root="/var/lib/dory",
        index_root="/var/lib/dory/.index",
        corpus_files=10,
        session_files=2,
        session_docs_indexed=2,
        session_missing_docs=0,
        session_stale_docs=0,
        files_indexed=10,
        chunks_indexed=20,
        vectors_indexed=20,
        index_present=True,
        index_stale=False,
        index_healthy=True,
        index_missing_files=0,
        vector_drift=0,
        last_reindex_at="2026-04-23T00:00:00Z",
        embedding_provider="local",
        embedding_model="qwen3-embed",
        embedding_dimensions=1024,
        embedding_batch_size=16,
        query_reranker_enabled=True,
        query_reranker_provider="local",
        query_reranker_model="qwen3-rerank",
        active_memory_llm_provider="local",
        active_memory_llm_stages="compose",
        openclaw=OpenClawParityDiagnostics(
            flush_enabled=False,
            recall_tracking_enabled=True,
            artifact_listing_enabled=True,
        ),
        compat_matrix={"wake": "ok"},
    )

    payload = serialize_status(status, debug=True)

    assert payload["corpus_root"] == "/var/lib/dory"
    assert payload["index_root"] == "/var/lib/dory/.index"
    assert payload["embedding_batch_size"] == 16
    assert payload["openclaw"]["recall_tracking_enabled"] is True
    assert payload["compat_matrix"] == {"wake": "ok"}
