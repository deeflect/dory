from __future__ import annotations

import json
import sqlite3

from dory_core import status as status_module
from dory_core.status import _count_sqlite_rows, _count_vector_rows


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
