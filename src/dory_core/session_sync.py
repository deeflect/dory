from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.session_plane import SessionEvidencePlane

SESSION_LOG_PREFIX = "logs/sessions/"


@dataclass(frozen=True, slots=True)
class SessionSyncPlan:
    session_files: int
    session_docs_indexed: int
    missing_docs: int
    stale_docs: int


@dataclass(frozen=True, slots=True)
class SessionSyncResult:
    session_files: int
    docs_indexed: int
    docs_deleted: int
    skipped_paths: list[str] = field(default_factory=list)


def is_session_path(path: str | Path) -> bool:
    normalized = Path(path).as_posix()
    return normalized.startswith(SESSION_LOG_PREFIX) and normalized.endswith(".md")


def plan_session_sync(corpus_root: Path, session_db_path: Path) -> SessionSyncPlan:
    disk_paths = set(_iter_session_paths(corpus_root))
    indexed_paths = SessionEvidencePlane(session_db_path).load_paths()
    return SessionSyncPlan(
        session_files=len(disk_paths),
        session_docs_indexed=len(indexed_paths),
        missing_docs=len(disk_paths - indexed_paths),
        stale_docs=len(indexed_paths - disk_paths),
    )


def sync_session_files(
    corpus_root: Path,
    session_db_path: Path,
    relative_paths: list[str] | tuple[str, ...] | None = None,
) -> SessionSyncResult:
    corpus_root = Path(corpus_root)
    plane = SessionEvidencePlane(session_db_path)
    paths = sorted(set(relative_paths) if relative_paths is not None else _iter_session_paths(corpus_root))
    skipped_paths: list[str] = []
    indexed = 0
    delete_paths: list[str] = []
    existing_paths: set[str] = set()

    for relative_path in paths:
        if not is_session_path(relative_path):
            skipped_paths.append(relative_path)
            continue
        target = corpus_root / relative_path
        if not target.exists():
            delete_paths.append(relative_path)
            continue
        try:
            document = load_markdown_document(target.read_text(encoding="utf-8"))
        except ValueError:
            skipped_paths.append(relative_path)
            continue
        frontmatter = document.frontmatter
        plane.upsert_session_chunk(
            path=relative_path,
            content=document.body,
            updated=_session_updated(target, frontmatter),
            agent=_frontmatter_or_path(frontmatter, "agent", relative_path, index=2),
            device=_frontmatter_or_path(frontmatter, "device", relative_path, index=3),
            session_id=str(frontmatter.get("session_id") or Path(relative_path).stem),
            status=str(frontmatter.get("status") or "closed"),
        )
        existing_paths.add(relative_path)
        indexed += 1

    if relative_paths is None:
        delete_paths.extend(sorted(plane.load_paths() - existing_paths))

    deleted = plane.delete_paths(tuple(delete_paths))
    return SessionSyncResult(
        session_files=len(existing_paths),
        docs_indexed=indexed,
        docs_deleted=deleted,
        skipped_paths=skipped_paths,
    )


def _iter_session_paths(corpus_root: Path) -> list[str]:
    session_root = Path(corpus_root) / "logs" / "sessions"
    if not session_root.exists():
        return []
    return [str(path.relative_to(corpus_root).as_posix()) for path in sorted(session_root.rglob("*.md"))]


def _session_updated(path: Path, frontmatter: dict[str, object]) -> str:
    value = frontmatter.get("updated") or frontmatter.get("created")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _frontmatter_or_path(frontmatter: dict[str, object], key: str, path: str, *, index: int) -> str:
    value = frontmatter.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    parts = Path(path).parts
    if len(parts) > index:
        return parts[index]
    return "unknown"
