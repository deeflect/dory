from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dory_core.migration_normalize import normalize_migration_slug


MatchConfidence = Literal["high", "medium", "low"]
MatchSource = Literal["entity_id", "title", "alias"]


@dataclass(frozen=True, slots=True)
class RegistryMatch:
    entity_id: str
    family: str
    title: str
    target_path: str
    matched_by: MatchSource
    confidence: MatchConfidence


@dataclass(frozen=True, slots=True)
class EntityRecord:
    entity_id: str
    family: str
    title: str
    target_path: str
    aliases: tuple[str, ...]


class EntityRegistry:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def upsert(
        self,
        *,
        entity_id: str,
        family: str,
        title: str,
        target_path: str,
        aliases: tuple[str, ...] = (),
    ) -> None:
        normalized_entity_id = _normalize_entity_id(entity_id)
        normalized_family = family.strip().lower()
        canonical_title = title.strip() or normalized_entity_id.split(":", 1)[1].replace("-", " ").title()
        normalized_target_path = target_path.strip()
        alias_rows = _build_alias_rows(normalized_entity_id, canonical_title, aliases)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO entities (entity_id, family, title, target_path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    family = excluded.family,
                    title = excluded.title,
                    target_path = excluded.target_path
                """,
                (normalized_entity_id, normalized_family, canonical_title, normalized_target_path),
            )
            connection.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (normalized_entity_id,))
            connection.executemany(
                """
                INSERT INTO entity_aliases (entity_id, normalized_value, source)
                VALUES (?, ?, ?)
                """,
                alias_rows,
            )
            connection.commit()

    def resolve(self, subject: str, *, family: str | None = None) -> RegistryMatch | None:
        normalized_subject = normalize_migration_slug(subject.strip())
        if not normalized_subject:
            return None
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    e.entity_id,
                    e.family,
                    e.title,
                    e.target_path,
                    a.source
                FROM entity_aliases AS a
                JOIN entities AS e ON e.entity_id = a.entity_id
                WHERE a.normalized_value = ?
                ORDER BY CASE a.source
                    WHEN 'entity_id' THEN 0
                    WHEN 'title' THEN 1
                    ELSE 2
                END
                """,
                (normalized_subject,),
            ).fetchall()
        for row in rows:
            row_family = str(row["family"])
            if family is not None and row_family != family:
                continue
            source = str(row["source"])
            matched_by: MatchSource = "alias"
            if source == "entity_id":
                matched_by = "entity_id"
            elif source == "title":
                matched_by = "title"
            return RegistryMatch(
                entity_id=str(row["entity_id"]),
                family=row_family,
                title=str(row["title"]),
                target_path=str(row["target_path"]),
                matched_by=matched_by,
                confidence="high",
            )
        return None

    def get(self, entity_id: str) -> EntityRecord | None:
        normalized_entity_id = _normalize_entity_id(entity_id)
        with sqlite3.connect(self.db_path) as connection:
            entity_row = connection.execute(
                """
                SELECT entity_id, family, title, target_path
                FROM entities
                WHERE entity_id = ?
                """,
                (normalized_entity_id,),
            ).fetchone()
            if entity_row is None:
                return None
            alias_rows = connection.execute(
                """
                SELECT normalized_value
                FROM entity_aliases
                WHERE entity_id = ? AND source = 'alias'
                ORDER BY normalized_value ASC
                """,
                (normalized_entity_id,),
            ).fetchall()
        return EntityRecord(
            entity_id=str(entity_row[0]),
            family=str(entity_row[1]),
            title=str(entity_row[2]),
            target_path=str(entity_row[3]),
            aliases=tuple(str(row[0]) for row in alias_rows),
        )

    def merge(self, winner_id: str, loser_id: str) -> None:
        normalized_winner = _normalize_entity_id(winner_id)
        normalized_loser = _normalize_entity_id(loser_id)
        if normalized_winner == normalized_loser:
            return
        with sqlite3.connect(self.db_path) as connection:
            loser_rows = connection.execute(
                "SELECT normalized_value, source FROM entity_aliases WHERE entity_id = ?",
                (normalized_loser,),
            ).fetchall()
            connection.execute(
                "UPDATE entity_aliases SET entity_id = ? WHERE entity_id = ?",
                (normalized_winner, normalized_loser),
            )
            connection.execute("DELETE FROM entities WHERE entity_id = ?", (normalized_loser,))
            for normalized_value, source in loser_rows:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO entity_aliases (entity_id, normalized_value, source)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_winner, normalized_value, source),
                )
            connection.commit()

    def list_family(self, family: str) -> tuple[EntityRecord, ...]:
        normalized_family = family.strip().lower()
        with sqlite3.connect(self.db_path) as connection:
            entity_rows = connection.execute(
                """
                SELECT entity_id, family, title, target_path
                FROM entities
                WHERE family = ?
                ORDER BY title COLLATE NOCASE ASC, entity_id ASC
                """,
                (normalized_family,),
            ).fetchall()
            records: list[EntityRecord] = []
            for entity_id, entity_family, title, target_path in entity_rows:
                alias_rows = connection.execute(
                    """
                    SELECT normalized_value
                    FROM entity_aliases
                    WHERE entity_id = ? AND source = 'alias'
                    ORDER BY normalized_value ASC
                    """,
                    (str(entity_id),),
                ).fetchall()
                records.append(
                    EntityRecord(
                        entity_id=str(entity_id),
                        family=str(entity_family),
                        title=str(title),
                        target_path=str(target_path),
                        aliases=tuple(str(row[0]) for row in alias_rows),
                    )
                )
        return tuple(records)

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    family TEXT NOT NULL,
                    title TEXT NOT NULL,
                    target_path TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_aliases (
                    entity_id TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    UNIQUE(entity_id, normalized_value, source)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_entity_aliases_lookup
                ON entity_aliases (normalized_value)
                """
            )
            connection.commit()


def _build_alias_rows(entity_id: str, title: str, aliases: tuple[str, ...]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source, value in (
        ("entity_id", entity_id),
        ("title", title),
        *(("alias", alias) for alias in aliases),
    ):
        normalized = normalize_migration_slug(value)
        if not normalized:
            continue
        key = (normalized, source)
        if key in seen:
            continue
        seen.add(key)
        rows.append((entity_id, normalized, source))
    return rows


def _normalize_entity_id(entity_id: str) -> str:
    rendered = entity_id.strip()
    if ":" not in rendered:
        return normalize_migration_slug(rendered)
    family, raw_slug = rendered.split(":", 1)
    return f"{family.strip().lower()}:{normalize_migration_slug(raw_slug)}"
