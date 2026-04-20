from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3


def apply_migrations(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                hash TEXT NOT NULL,
                mtime TEXT,
                size INTEGER NOT NULL DEFAULT 0,
                frontmatter_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                hash TEXT NOT NULL,
                frontmatter_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content,
                path,
                chunk_id
            );

            CREATE TABLE IF NOT EXISTS edges (
                from_path TEXT NOT NULL,
                to_path TEXT NOT NULL,
                anchor TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (from_path, to_path, anchor)
            );

            CREATE TABLE IF NOT EXISTS embedding_cache_meta (
                content_hash TEXT PRIMARY KEY,
                vector_id TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunk_vectors (
                chunk_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                vector BLOB NOT NULL,
                dimension INTEGER NOT NULL,
                FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recall_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                chunk_ids TEXT NOT NULL DEFAULT '[]',
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS openclaw_recall_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                session_key TEXT,
                query TEXT NOT NULL,
                result_paths_json TEXT NOT NULL DEFAULT '[]',
                selected_path TEXT,
                corpus TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS openclaw_recall_promotions (
                selected_path TEXT PRIMARY KEY,
                last_event_id INTEGER NOT NULL,
                event_count INTEGER NOT NULL DEFAULT 0,
                query_count INTEGER NOT NULL DEFAULT 0,
                distilled_path TEXT NOT NULL DEFAULT '',
                promoted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        connection.commit()
