from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.index.migrations import apply_migrations
from dory_core.types import (
    OpenClawParityDiagnostics,
    PublicArtifactResp,
    RecallEventReq,
    RecallEventResp,
)


@dataclass(frozen=True, slots=True)
class RecallEventRecord:
    id: int
    agent: str
    session_key: str | None
    query: str
    result_paths: tuple[str, ...]
    selected_path: str | None
    corpus: str
    source: str
    created_at: str


@dataclass(frozen=True, slots=True)
class RecallPromotionCandidate:
    selected_path: str
    last_event_id: int
    event_count: int
    query_count: int
    latest_at: str
    sample_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpenClawParityStore:
    index_root: Path
    readonly: bool = False

    def __post_init__(self) -> None:
        if self.readonly:
            return
        self.index_root.mkdir(parents=True, exist_ok=True)
        apply_migrations(self.db_path)

    @property
    def db_path(self) -> Path:
        return self.index_root / "dory.db"

    def record_recall_event(self, req: RecallEventReq) -> RecallEventResp:
        result_paths_json = json.dumps(list(req.result_paths), sort_keys=True)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO openclaw_recall_events(
                    agent, session_key, query, result_paths_json, selected_path, corpus, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.agent,
                    req.session_key,
                    req.query,
                    result_paths_json,
                    req.selected_path,
                    req.corpus,
                    req.source,
                    created_at,
                ),
            )
            connection.commit()
        return RecallEventResp(stored=True, selected_path=req.selected_path, created_at=created_at)

    def load_recent_recall_events(self, *, limit: int = 10) -> tuple[RecallEventRecord, ...]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, agent, session_key, query, result_paths_json, selected_path, corpus, source, created_at
                FROM openclaw_recall_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            RecallEventRecord(
                id=int(row[0]),
                agent=str(row[1]),
                session_key=str(row[2]) if row[2] is not None else None,
                query=str(row[3]),
                result_paths=tuple(_load_string_list(row[4])),
                selected_path=str(row[5]) if row[5] is not None else None,
                corpus=str(row[6]),
                source=str(row[7]),
                created_at=str(row[8]),
            )
            for row in rows
        )

    def recent_recall_summary(self, *, limit: int = 10) -> dict[str, object]:
        recent = self.load_recent_recall_events(limit=limit)
        last = recent[0] if recent else None
        return {
            "count": len(recent),
            "last_recall_event_at": last.created_at if last is not None else None,
            "last_recall_selected_path": last.selected_path if last is not None else None,
        }

    def list_recall_promotion_candidates(
        self,
        *,
        min_events: int = 2,
        limit: int = 10,
    ) -> tuple[RecallPromotionCandidate, ...]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    e.selected_path,
                    MAX(e.id) AS last_event_id,
                    COUNT(*) AS event_count,
                    COUNT(DISTINCT e.query) AS query_count,
                    MAX(e.created_at) AS latest_at,
                    COALESCE(p.last_event_id, 0) AS promoted_event_id
                FROM openclaw_recall_events e
                LEFT JOIN openclaw_recall_promotions p
                    ON p.selected_path = e.selected_path
                WHERE e.selected_path IS NOT NULL AND e.selected_path != ''
                GROUP BY e.selected_path
                HAVING COUNT(*) >= ?
                ORDER BY query_count DESC, event_count DESC, last_event_id DESC
                LIMIT ?
                """,
                (min_events, limit),
            ).fetchall()

            candidates: list[RecallPromotionCandidate] = []
            for row in rows:
                selected_path = str(row[0])
                last_event_id = int(row[1])
                promoted_event_id = int(row[5] or 0)
                if promoted_event_id >= last_event_id:
                    continue
                sample_queries = connection.execute(
                    """
                    SELECT DISTINCT query
                    FROM openclaw_recall_events
                    WHERE selected_path = ?
                    ORDER BY id DESC
                    LIMIT 5
                    """,
                    (selected_path,),
                ).fetchall()
                candidates.append(
                    RecallPromotionCandidate(
                        selected_path=selected_path,
                        last_event_id=last_event_id,
                        event_count=int(row[2]),
                        query_count=int(row[3]),
                        latest_at=str(row[4]),
                        sample_queries=tuple(
                            str(query_row[0]).strip()
                            for query_row in sample_queries
                            if query_row[0] is not None and str(query_row[0]).strip()
                        ),
                    )
                )
        return tuple(candidates)

    def mark_recall_promotion(
        self,
        *,
        candidate: RecallPromotionCandidate,
        distilled_path: str,
    ) -> None:
        promoted_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO openclaw_recall_promotions(
                    selected_path, last_event_id, event_count, query_count, distilled_path, promoted_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(selected_path) DO UPDATE SET
                    last_event_id = excluded.last_event_id,
                    event_count = excluded.event_count,
                    query_count = excluded.query_count,
                    distilled_path = excluded.distilled_path,
                    promoted_at = excluded.promoted_at
                """,
                (
                    candidate.selected_path,
                    candidate.last_event_id,
                    candidate.event_count,
                    candidate.query_count,
                    distilled_path,
                    promoted_at,
                ),
            )
            connection.commit()

    def diagnostics(self) -> OpenClawParityDiagnostics:
        if not self.db_path.exists():
            return OpenClawParityDiagnostics(
                flush_enabled=False,
                recall_tracking_enabled=True,
                artifact_listing_enabled=True,
            )
        summary = self.recent_recall_summary()
        candidates = self.list_recall_promotion_candidates()
        last_promotion_at = self._last_recall_promotion_at()
        return OpenClawParityDiagnostics(
            flush_enabled=False,
            recall_tracking_enabled=True,
            artifact_listing_enabled=True,
            recent_recall_count=int(summary["count"]),
            promotion_candidate_count=len(candidates),
            last_recall_event_at=summary["last_recall_event_at"],
            last_recall_selected_path=summary["last_recall_selected_path"],
            last_recall_promotion_at=last_promotion_at,
        )

    def _last_recall_promotion_at(self) -> str | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT promoted_at
                FROM openclaw_recall_promotions
                ORDER BY promoted_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])


def list_public_artifacts(corpus_root: Path) -> tuple[PublicArtifactResp, ...]:
    corpus_root = Path(corpus_root)
    if not corpus_root.exists():
        return ()

    artifacts: list[PublicArtifactResp] = []
    for path in sorted(corpus_root.rglob("*.md")):
        relative_path = path.relative_to(corpus_root).as_posix()
        kind = _artifact_kind_for_path(relative_path)
        if kind is None:
            continue
        metadata = _load_metadata(path)
        artifacts.append(
            PublicArtifactResp(
                kind=kind,
                relative_path=relative_path,
                content_type="markdown",
                title=_artifact_title(metadata, path),
                agent_ids=_extract_agent_ids(metadata),
            )
        )

    return tuple(artifacts)


def _artifact_kind_for_path(relative_path: str) -> str | None:
    if relative_path.startswith("core/") and relative_path.endswith(".md"):
        return "core"
    if relative_path.startswith("references/reports/") and relative_path.endswith(".md"):
        return "report"
    if relative_path.startswith("references/briefings/") and relative_path.endswith(".md"):
        return "briefing"
    if relative_path.startswith("references/slides/") and relative_path.endswith(".md"):
        return "slide"
    if relative_path.startswith("references/notes/") and relative_path.endswith(".md"):
        return "note"
    if relative_path.startswith("wiki/") and relative_path.endswith(".md"):
        return "wiki"
    return None


def _load_metadata(path: Path) -> dict[str, object]:
    try:
        document = load_markdown_document(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return dict(document.frontmatter)


def _extract_agent_ids(metadata: dict[str, object]) -> list[str]:
    raw_agent_ids = metadata.get("agent_ids")
    values: list[str] = []
    if isinstance(raw_agent_ids, list):
        for value in raw_agent_ids:
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    elif isinstance(raw_agent_ids, str) and raw_agent_ids.strip():
        values.append(raw_agent_ids.strip())

    raw_agent = metadata.get("agent")
    if isinstance(raw_agent, str) and raw_agent.strip() and raw_agent.strip() not in values:
        values.append(raw_agent.strip())
    return values


def _artifact_title(metadata: dict[str, object], path: Path) -> str:
    raw_title = metadata.get("title")
    if isinstance(raw_title, str) and raw_title.strip():
        return raw_title.strip()
    return path.stem.replace("-", " ").replace("_", " ").strip().title()


def _load_string_list(value: object) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    result: list[str] = []
    for item in payload:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result
