from __future__ import annotations

import sqlite3
from pathlib import Path

from dory_core.index.migrations import apply_migrations


def test_apply_migrations_creates_expected_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "dory.db"

    apply_migrations(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }

    assert {
        "files",
        "chunks",
        "chunks_fts",
        "edges",
        "embedding_cache_meta",
        "recall_log",
        "openclaw_recall_events",
        "openclaw_recall_promotions",
        "meta",
    } <= tables
