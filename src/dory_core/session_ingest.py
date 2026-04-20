from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dory_core.errors import DoryValidationError
from dory_core.fs import atomic_write_text
from dory_core.frontmatter import dump_markdown_document
from dory_core.metadata import normalize_frontmatter
from dory_core.session_plane import SessionEvidencePlane
from dory_core.types import SessionIngestReq, SessionIngestResp


@dataclass(frozen=True, slots=True)
class SessionIngestService:
    corpus_root: Path
    session_db_path: Path

    def ingest(self, req: SessionIngestReq) -> SessionIngestResp:
        root = self.corpus_root.resolve()
        target = self._resolve_target(req.path)
        target.parent.mkdir(parents=True, exist_ok=True)

        frontmatter = normalize_frontmatter(
            {
                "title": f"{req.agent} session {req.session_id}",
                "type": "session",
                "status": req.status,
                "source_kind": "extracted",
                "temperature": "warm",
                "agent": req.agent,
                "device": req.device,
                "session_id": req.session_id,
                "captured_from": req.captured_from,
                "created": req.updated,
                "updated": req.updated,
            },
            target=target.relative_to(root),
        )
        rendered = dump_markdown_document(frontmatter, req.content)
        atomic_write_text(target, rendered, encoding="utf-8")

        SessionEvidencePlane(self.session_db_path).upsert_session_chunk(
            path=req.path,
            content=req.content,
            updated=req.updated,
            agent=req.agent,
            device=req.device,
            session_id=req.session_id,
            status=req.status,
        )
        return SessionIngestResp(stored=True, path=req.path, reindexed=False)

    def _resolve_target(self, relative_path: str) -> Path:
        root = self.corpus_root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as err:
            raise DoryValidationError(f"path escapes corpus root: {relative_path}") from err
        if relative.parts[:2] != ("logs", "sessions") or candidate.suffix.lower() != ".md":
            raise DoryValidationError("session ingest path must live under logs/sessions/*.md")
        return candidate
