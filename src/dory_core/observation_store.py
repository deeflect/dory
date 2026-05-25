from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4


ObservationStatus = Literal["active", "stale", "retired", "rejected"]
ObservationFreshness = Literal["new", "stable", "stale"]
ObservationConfidence = Literal["low", "medium", "high"]
ObservationRelevance = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    title: str
    content: str
    entity_ids: tuple[str, ...]
    status: ObservationStatus
    freshness: ObservationFreshness
    confidence: ObservationConfidence
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ObservationEvidence:
    observation_id: str
    claim_id: str | None
    evidence_path: str
    quote: str
    relevance: ObservationRelevance
    observed_at: str | None


class ObservationStore:
    """SQLite sidecar/index for observations over claims and evidence.

    Observations are derived/indexed views over claims and evidence. They
    are rebuildable. Claims remain the durable active-truth layer. Markdown
    remains canonical.

    No canonical truth is stored in observations — this is a rebuildable
    index.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_observation(
        self,
        *,
        title: str,
        content: str,
        entity_ids: tuple[str, ...],
        status: ObservationStatus = "active",
        freshness: ObservationFreshness = "new",
        confidence: ObservationConfidence = "high",
        evidence_rows: tuple[ObservationEvidence, ...] = (),
    ) -> str:
        """Add an observation with at least one evidence reference.

        Returns the generated observation_id.
        Raises ValueError if no evidence_rows are provided.
        """
        if not evidence_rows:
            raise ValueError("Every observation requires at least one source reference / evidence row.")
        for ev in evidence_rows:
            if not ev.evidence_path.strip():
                raise ValueError("Every observation evidence row requires a source reference.")

        observation_id = uuid4().hex
        now = _now_iso()

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """\
                INSERT INTO observations (
                    observation_id, title, content, entity_ids, status,
                    freshness, confidence, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    title,
                    content,
                    _join_entity_ids(entity_ids),
                    status,
                    freshness,
                    confidence,
                    now,
                    now,
                ),
            )
            for ev in evidence_rows:
                connection.execute(
                    """\
                    INSERT INTO observation_evidence (
                        observation_id, claim_id, evidence_path, quote,
                        relevance, observed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.observation_id or observation_id,
                        ev.claim_id,
                        ev.evidence_path,
                        ev.quote,
                        ev.relevance,
                        ev.observed_at,
                    ),
                )
            connection.commit()

        return observation_id

    def update_observation(
        self,
        observation_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        status: ObservationStatus | None = None,
        freshness: ObservationFreshness | None = None,
        confidence: ObservationConfidence | None = None,
    ) -> bool:
        """Update mutable fields on an observation. Returns True if found."""
        now = _now_iso()
        sets: list[str] = []
        params: list[str | None] = []

        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if freshness is not None:
            sets.append("freshness = ?")
            params.append(freshness)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(confidence)

        if not sets:
            return False

        sets.append("updated_at = ?")
        params.append(now)
        params.append(observation_id)

        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                f"""\
                UPDATE observations
                SET {', '.join(sets)}
                WHERE observation_id = ?
                """,
                params,
            )
            connection.commit()
            return cursor.rowcount > 0

    def retire_observation(self, observation_id: str, *, reason: str | None = None) -> bool:
        """Mark an observation as retired."""
        return self.update_observation(observation_id, status="retired")

    def get_observation(self, observation_id: str) -> ObservationRecord | None:
        """Get a single observation by ID."""
        row = self._fetch_one(
            """\
            SELECT observation_id, title, content, entity_ids, status,
                   freshness, confidence, created_at, updated_at
            FROM observations
            WHERE observation_id = ?
            """,
            (observation_id,),
        )
        if row is None:
            return None
        return _row_to_observation(row)

    def get_evidence(self, observation_id: str) -> tuple[ObservationEvidence, ...]:
        """Get all evidence rows for an observation."""
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """\
                SELECT observation_id, claim_id, evidence_path, quote,
                       relevance, observed_at
                FROM observation_evidence
                WHERE observation_id = ?
                ORDER BY rowid ASC
                """,
                (observation_id,),
            ).fetchall()
        return tuple(_row_to_evidence(r) for r in rows)

    def list_observations(
        self,
        *,
        entity_id: str | None = None,
        status: ObservationStatus | None = None,
        freshness: ObservationFreshness | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ObservationRecord, ...]:
        """List observations with optional filters."""
        conditions: list[str] = []
        params: list[str] = []

        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if freshness is not None:
            conditions.append("freshness = ?")
            params.append(freshness)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with sqlite3.connect(self.db_path) as connection:
            if entity_id is not None:
                # Filter by entity_id using LIKE on the comma-joined string
                rows = connection.execute(
                    f"""\
                    SELECT observation_id, title, content, entity_ids, status,
                           freshness, confidence, created_at, updated_at
                    FROM observations
                    {where_clause}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    params + [str(limit), str(offset)],
                ).fetchall()
                # Filter in Python for entity_id containment
                result: list[ObservationRecord] = []
                for row in rows:
                    obs = _row_to_observation(row)
                    if entity_id in obs.entity_ids:
                        result.append(obs)
                return tuple(result)
            else:
                rows = connection.execute(
                    f"""\
                    SELECT observation_id, title, content, entity_ids, status,
                           freshness, confidence, created_at, updated_at
                    FROM observations
                    {where_clause}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    params + [str(limit), str(offset)],
                ).fetchall()
        return tuple(_row_to_observation(r) for r in rows)

    def count_observations(
        self,
        *,
        status: ObservationStatus | None = None,
    ) -> int:
        """Count observations, optionally filtered by status."""
        if status is not None:
            row = self._fetch_one(
                "SELECT COUNT(*) FROM observations WHERE status = ?",
                (status,),
            )
        else:
            row = self._fetch_one("SELECT COUNT(*) FROM observations", ())
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Rebuild support
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Remove all rows from the observation tables.

        Used before rebuilding from canonical sources.
        """
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("DELETE FROM observation_evidence")
            connection.execute("DELETE FROM observations")
            connection.commit()

    def drop_db(self) -> None:
        """Drop the database file entirely. Used for clean rebuilds."""
        self.db_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """\
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    entity_ids TEXT NOT NULL,
                    status TEXT NOT NULL,
                    freshness TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                """\
                CREATE TABLE IF NOT EXISTS observation_evidence (
                    observation_id TEXT NOT NULL,
                    claim_id TEXT,
                    evidence_path TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    relevance TEXT NOT NULL,
                    observed_at TEXT,
                    FOREIGN KEY (observation_id) REFERENCES observations(observation_id)
                )
                """,
            )
            connection.execute(
                """\
                CREATE INDEX IF NOT EXISTS idx_obs_status
                ON observations (status, updated_at)
                """,
            )
            connection.execute(
                """\
                CREATE INDEX IF NOT EXISTS idx_obs_freshness
                ON observations (freshness, updated_at)
                """,
            )
            connection.execute(
                """\
                CREATE INDEX IF NOT EXISTS idx_obs_evidence_obs
                ON observation_evidence (observation_id)
                """,
            )
            connection.commit()

    def _fetch_one(
        self,
        query: str,
        params: tuple[str, ...],
    ) -> tuple | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(query, params).fetchone()
        return row


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _join_entity_ids(entity_ids: tuple[str, ...]) -> str:
    return ",".join(entity_ids)


def _split_entity_ids(raw: str) -> tuple[str, ...]:
    return tuple(raw.split(",")) if raw else ()


def _row_to_observation(row: tuple) -> ObservationRecord:
    return ObservationRecord(
        observation_id=row[0],
        title=row[1],
        content=row[2],
        entity_ids=_split_entity_ids(row[3]),
        status=row[4],  # type: ignore[arg-type]
        freshness=row[5],  # type: ignore[arg-type]
        confidence=row[6],  # type: ignore[arg-type]
        created_at=row[7],
        updated_at=row[8],
    )


def _row_to_evidence(row: tuple) -> ObservationEvidence:
    return ObservationEvidence(
        observation_id=row[0],
        claim_id=row[1],
        evidence_path=row[2],
        quote=row[3],
        relevance=row[4],  # type: ignore[arg-type]
        observed_at=row[5],
    )
