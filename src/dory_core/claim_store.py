from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    claim_id: str
    entity_id: str
    kind: str
    statement: str
    status: str
    valid_from: str | None
    valid_to: str | None
    confidence: str
    evidence_path: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ClaimEvent:
    event_id: str
    claim_id: str
    entity_id: str
    event_type: str
    reason: str | None
    evidence_path: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ClaimEventDetail:
    event_id: str
    claim_id: str
    entity_id: str
    event_type: str
    reason: str | None
    evidence_path: str
    created_at: str
    statement: str
    kind: str
    claim_status: str


class ClaimStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def add_claim(
        self,
        *,
        entity_id: str,
        kind: str,
        statement: str,
        evidence_path: str,
        confidence: str = "high",
        occurred_at: str | None = None,
    ) -> str:
        claim_id = uuid4().hex
        now = _normalize_occurred_at(occurred_at) or _now_iso()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO claims (
                    claim_id, entity_id, kind, statement, status, valid_from, valid_to,
                    confidence, evidence_path, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    entity_id,
                    kind,
                    statement,
                    "active",
                    now,
                    None,
                    confidence,
                    evidence_path,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                claim_id=claim_id,
                entity_id=entity_id,
                event_type="added",
                reason=None,
                evidence_path=evidence_path,
                created_at=now,
            )
            connection.commit()
        return claim_id

    def invalidate_claim(self, claim_id: str, *, reason: str, evidence_path: str | None = None) -> None:
        self._set_claim_status(
            claim_id,
            status="invalidated",
            reason=reason,
            evidence_path=evidence_path,
        )

    def replace_current_claim(
        self,
        *,
        entity_id: str,
        kind: str,
        statement: str,
        evidence_path: str,
        confidence: str = "high",
        reason: str | None = None,
        occurred_at: str | None = None,
    ) -> str:
        now = _normalize_occurred_at(occurred_at) or _now_iso()
        with sqlite3.connect(self.db_path) as connection:
            replaced_ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT claim_id FROM claims
                    WHERE entity_id = ? AND kind = ? AND status = 'active'
                    """,
                    (entity_id, kind),
                ).fetchall()
            ]
            connection.execute(
                """
                UPDATE claims
                SET status = ?, valid_to = ?, updated_at = ?
                WHERE entity_id = ? AND kind = ? AND status = 'active'
                """,
                ("replaced", now, now, entity_id, kind),
            )
            for replaced_id in replaced_ids:
                self._insert_event(
                    connection,
                    claim_id=replaced_id,
                    entity_id=entity_id,
                    event_type="replaced",
                    reason=reason,
                    evidence_path=evidence_path,
                    created_at=now,
                )

            claim_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO claims (
                    claim_id, entity_id, kind, statement, status, valid_from, valid_to,
                    confidence, evidence_path, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    entity_id,
                    kind,
                    statement,
                    "active",
                    now,
                    None,
                    confidence,
                    evidence_path,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                claim_id=claim_id,
                entity_id=entity_id,
                event_type="added",
                reason=reason,
                evidence_path=evidence_path,
                created_at=now,
            )
            connection.commit()
        return claim_id

    def retire_entity_claims(
        self,
        *,
        entity_id: str,
        reason: str,
        kind: str | None = None,
        evidence_path: str | None = None,
    ) -> None:
        now = _now_iso()
        query = """
            SELECT claim_id, evidence_path FROM claims
            WHERE entity_id = ? AND status = 'active'
        """
        params: tuple[str, ...] | tuple[str, str] = (entity_id,)
        if kind is not None:
            query += " AND kind = ?"
            params = (entity_id, kind)
        with sqlite3.connect(self.db_path) as connection:
            claim_rows = connection.execute(query, params).fetchall()
            if not claim_rows:
                return
            claim_ids = [row[0] for row in claim_rows]
            if kind is None:
                connection.execute(
                    """
                    UPDATE claims
                    SET status = ?, valid_to = ?, updated_at = ?
                    WHERE entity_id = ? AND status = 'active'
                    """,
                    ("retired", now, now, entity_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE claims
                    SET status = ?, valid_to = ?, updated_at = ?
                    WHERE entity_id = ? AND kind = ? AND status = 'active'
                    """,
                    ("retired", now, now, entity_id, kind),
                )
            for claim_id, current_evidence_path in claim_rows:
                self._insert_event(
                    connection,
                    claim_id=claim_id,
                    entity_id=entity_id,
                    event_type="retired",
                    reason=reason,
                    evidence_path=evidence_path or str(current_evidence_path),
                    created_at=now,
                )
            connection.commit()

    def current_claims(self, entity_id: str, *, kind: str | None = None) -> tuple[ClaimRecord, ...]:
        query = """
            SELECT claim_id, entity_id, kind, statement, status, valid_from, valid_to,
                   confidence, evidence_path, created_at, updated_at
            FROM claims
            WHERE entity_id = ? AND status = 'active'
        """
        params: tuple[str, ...] | tuple[str, str] = (entity_id,)
        if kind is not None:
            query += " AND kind = ?"
            params = (entity_id, kind)
        query += " ORDER BY created_at ASC"
        return self._fetch_claims(query, params)

    def claim_history(self, entity_id: str) -> tuple[ClaimRecord, ...]:
        return self._fetch_claims(
            """
            SELECT claim_id, entity_id, kind, statement, status, valid_from, valid_to,
                   confidence, evidence_path, created_at, updated_at
            FROM claims
            WHERE entity_id = ?
            ORDER BY created_at ASC
            """,
            (entity_id,),
        )

    def claim_events(self, entity_id: str) -> tuple[ClaimEvent, ...]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT event_id, claim_id, entity_id, event_type, reason, evidence_path, created_at
                FROM claim_events
                WHERE entity_id = ?
                ORDER BY created_at ASC
                """,
                (entity_id,),
            ).fetchall()
        return tuple(ClaimEvent(*row) for row in rows)

    def recent_active_claims(self, *, limit: int = 20) -> tuple[ClaimRecord, ...]:
        return self._fetch_claims(
            """
            SELECT claim_id, entity_id, kind, statement, status, valid_from, valid_to,
                   confidence, evidence_path, created_at, updated_at
            FROM claims
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (str(limit),),
        )

    def recent_event_details(self, *, limit: int = 20) -> tuple[ClaimEventDetail, ...]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    e.event_id,
                    e.claim_id,
                    e.entity_id,
                    e.event_type,
                    e.reason,
                    e.evidence_path,
                    e.created_at,
                    COALESCE(c.statement, ''),
                    COALESCE(c.kind, ''),
                    COALESCE(c.status, '')
                FROM claim_events e
                LEFT JOIN claims c ON c.claim_id = e.claim_id
                ORDER BY e.created_at DESC, e.event_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(ClaimEventDetail(*row) for row in rows)

    def _set_claim_status(
        self,
        claim_id: str,
        *,
        status: str,
        reason: str,
        evidence_path: str | None = None,
    ) -> None:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT entity_id, evidence_path FROM claims WHERE claim_id = ?",
                (claim_id,),
            ).fetchone()
            if row is None:
                return
            entity_id, current_evidence_path = row
            connection.execute(
                """
                UPDATE claims
                SET status = ?, valid_to = ?, updated_at = ?
                WHERE claim_id = ?
                """,
                (status, now, now, claim_id),
            )
            self._insert_event(
                connection,
                claim_id=claim_id,
                entity_id=str(entity_id),
                event_type=status,
                reason=reason,
                evidence_path=evidence_path or str(current_evidence_path),
                created_at=now,
            )
            connection.commit()

    def _fetch_claims(self, query: str, params: tuple[str, ...] | tuple[str, str]) -> tuple[ClaimRecord, ...]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(ClaimRecord(*row) for row in rows)

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    status TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    confidence TEXT NOT NULL,
                    evidence_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS claim_events (
                    event_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    reason TEXT,
                    evidence_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_claims_entity_status
                ON claims (entity_id, status, kind)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_claim_events_claim
                ON claim_events (claim_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_claim_events_entity
                ON claim_events (entity_id, created_at)
                """
            )
            connection.commit()

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        claim_id: str,
        entity_id: str,
        event_type: str,
        reason: str | None,
        evidence_path: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO claim_events (
                event_id, claim_id, entity_id, event_type, reason, evidence_path, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid4().hex, claim_id, entity_id, event_type, reason, evidence_path, created_at),
        )


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _normalize_occurred_at(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) == 10:
        try:
            datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return f"{normalized}T00:00:00Z"
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
