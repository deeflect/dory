from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class SessionSearchQuery:
    query: str
    limit: int = 5


@dataclass(frozen=True, slots=True)
class SessionSearchResult:
    path: str
    snippet: str
    updated: str
    agent: str
    device: str
    session_id: str
    status: str
    score: float


@dataclass(frozen=True, slots=True)
class SessionSearchResponse:
    count: int
    results: tuple[SessionSearchResult, ...]


@dataclass(frozen=True, slots=True)
class SessionEvidencePlane:
    db_path: Path

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_docs (
                    path TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    updated TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    device TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS session_docs_fts USING fts5(
                    path,
                    content,
                    content='session_docs',
                    content_rowid='rowid'
                );

                CREATE TRIGGER IF NOT EXISTS session_docs_ai AFTER INSERT ON session_docs BEGIN
                    INSERT INTO session_docs_fts(rowid, path, content)
                    VALUES (new.rowid, new.path, new.content);
                END;

                CREATE TRIGGER IF NOT EXISTS session_docs_ad AFTER DELETE ON session_docs BEGIN
                    INSERT INTO session_docs_fts(session_docs_fts, rowid, path, content)
                    VALUES ('delete', old.rowid, old.path, old.content);
                END;

                CREATE TRIGGER IF NOT EXISTS session_docs_au AFTER UPDATE ON session_docs BEGIN
                    INSERT INTO session_docs_fts(session_docs_fts, rowid, path, content)
                    VALUES ('delete', old.rowid, old.path, old.content);
                    INSERT INTO session_docs_fts(rowid, path, content)
                    VALUES (new.rowid, new.path, new.content);
                END;
                """
            )
            if _session_fts_needs_rebuild(connection):
                connection.execute("INSERT INTO session_docs_fts(session_docs_fts) VALUES('rebuild')")
            connection.commit()

    def upsert_session_chunk(
        self,
        *,
        path: str,
        content: str,
        updated: str,
        agent: str,
        device: str,
        session_id: str,
        status: str,
    ) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO session_docs(path, content, updated, agent, device, session_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    content = excluded.content,
                    updated = excluded.updated,
                    agent = excluded.agent,
                    device = excluded.device,
                    session_id = excluded.session_id,
                    status = excluded.status
                """,
                (path, content, updated, agent, device, session_id, status),
            )
            connection.commit()

    def search(self, query: SessionSearchQuery) -> SessionSearchResponse:
        fts_query = _build_fts_query(query.query)
        if not fts_query:
            return SessionSearchResponse(count=0, results=())

        query_terms = _query_terms(query.query)
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    d.path,
                    d.content,
                    d.updated,
                    d.agent,
                    d.device,
                    d.session_id,
                    d.status,
                    bm25(session_docs_fts) AS rank
                FROM session_docs_fts
                JOIN session_docs AS d ON d.rowid = session_docs_fts.rowid
                WHERE session_docs_fts MATCH ?
                ORDER BY rank ASC, d.updated DESC
                LIMIT ?
                """,
                (fts_query, max(query.limit * 4, query.limit)),
            ).fetchall()

        ranked = sorted(
            (
                SessionSearchResult(
                    path=row[0],
                    snippet=_build_snippet(row[1], query_terms),
                    updated=row[2],
                    agent=row[3],
                    device=row[4],
                    session_id=row[5],
                    status=row[6],
                    score=_session_score(
                        content=row[1], updated=row[2], bm25_rank=float(row[7]), query_terms=query_terms
                    ),
                )
                for row in rows
            ),
            key=lambda result: (-result.score, result.path),
        )
        results = tuple(ranked[: query.limit])
        return SessionSearchResponse(count=len(results), results=results)


def _build_fts_query(raw_query: str) -> str:
    tokens = [token.lower() for token in _TOKEN_RE.findall(raw_query) if len(token) >= 2]
    deduped = list(dict.fromkeys(tokens))
    if not deduped:
        return ""
    return " OR ".join(f'"{token}"' for token in deduped)


def _query_terms(raw_query: str) -> tuple[str, ...]:
    deduped = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(raw_query):
        lowered = token.lower()
        if len(lowered) < 2 or lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(lowered)
    return tuple(deduped)


def _session_score(*, content: str, updated: str, bm25_rank: float, query_terms: tuple[str, ...]) -> float:
    lowered = content.lower()
    exact_hits = sum(1 for term in query_terms if term in lowered)
    coverage = (exact_hits / len(query_terms)) if query_terms else 0.0
    recency_bonus = _updated_score(updated)
    phrase_bonus = 0.015 if len(query_terms) >= 2 and " ".join(query_terms) in lowered else 0.0
    # SQLite bm25() is lower-is-better, often negative. Convert to a small positive prior.
    lexical = max(0.0, min(0.04, -bm25_rank / 20.0))
    return (coverage * 0.8) + phrase_bonus + lexical + recency_bonus


def _build_snippet(content: str, query_terms: tuple[str, ...]) -> str:
    if not content:
        return ""
    lowered = content.lower()
    match_index = min((lowered.find(term) for term in query_terms if term in lowered), default=-1)
    if match_index < 0:
        return content[:280]
    start = max(0, match_index - 80)
    end = min(len(content), match_index + 200)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(content):
        snippet = f"{snippet}..."
    return snippet[:280]


def _updated_score(updated: str) -> float:
    try:
        parsed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return parsed.timestamp() / 10_000_000_000


def _session_fts_needs_rebuild(connection: sqlite3.Connection) -> bool:
    docs_count = int(connection.execute("SELECT COUNT(*) FROM session_docs").fetchone()[0])
    fts_count = int(connection.execute("SELECT COUNT(*) FROM session_docs_fts").fetchone()[0])
    return docs_count != fts_count
