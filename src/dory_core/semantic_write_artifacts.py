from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dory_core.embedding import ContentEmbedder
from dory_core.errors import DoryValidationError
from dory_core.frontmatter import dump_markdown_document
from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.index.reindex import reindex_paths
from dory_core.link import load_known_entities, sync_document_edges
from dory_core.metadata import normalize_frontmatter
from dory_core.semantic_write_plan import SemanticWritePlan
from dory_core.slug import normalize_migration_slug
from dory_core.types import MemoryWriteAction


@dataclass(frozen=True, slots=True)
class SemanticEvidenceArtifact:
    path: str
    frontmatter: dict[str, object]
    content: str


@dataclass(frozen=True, slots=True)
class SemanticEvidenceStore:
    root: Path
    index_root: Path | None = None
    embedder: ContentEmbedder | None = None

    def plan(self, plan: SemanticWritePlan) -> SemanticEvidenceArtifact:
        subject_slug = self._subject_slug(plan)
        artifact_path = self._artifact_path(plan.action, subject_slug=subject_slug)
        frontmatter: dict[str, object] = {
            "title": f"Semantic {plan.action} for {plan.title}",
            "type": "source",
            "status": "done",
            "canonical": False,
            "source_kind": "semantic",
            "entity_id": plan.target_subject_ref,
            "subject": plan.subject,
            "action": plan.action,
            "kind": plan.kind,
            "reason": plan.reason,
            "origin_surface": plan.origin_surface or plan.source or "semantic-write",
            "agent": plan.agent,
            "session_id": plan.session_id,
            "canonical_target": plan.target_path,
        }
        return SemanticEvidenceArtifact(
            path=artifact_path,
            frontmatter=frontmatter,
            content=plan.content,
        )

    def write(self, artifact: SemanticEvidenceArtifact) -> None:
        target_path = Path(artifact.path)
        target = resolve_corpus_target(self.root, target_path)
        if target.exists():
            raise DoryValidationError(f"semantic evidence artifact already exists: {artifact.path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = normalize_frontmatter(artifact.frontmatter, target=target_path)
        rendered = dump_markdown_document(frontmatter, artifact.content)
        atomic_write_text(target, rendered, encoding="utf-8")

        if self.index_root is None or self.embedder is None:
            return

        reindex_paths(
            self.root,
            self.index_root,
            self.embedder,
            [artifact.path],
        )
        known_entities = load_known_entities(self.root)
        sync_document_edges(
            self.index_root / "dory.db",
            from_path=artifact.path,
            markdown=rendered,
            known_entities=known_entities,
        )

    def _artifact_path(self, action: MemoryWriteAction, *, subject_slug: str) -> str:
        while True:
            stamp = datetime.now(tz=UTC)
            day_path = stamp.strftime("%Y/%m/%d")
            candidate = f"sources/semantic/{day_path}/{subject_slug}-{stamp.strftime('%Y%m%d-%H%M%S-%f')}-{action}.md"
            if not resolve_corpus_target(self.root, Path(candidate)).exists():
                return candidate

    def _subject_slug(self, plan: SemanticWritePlan) -> str:
        _family, slug = plan.target_subject_ref.split(":", 1)
        return normalize_migration_slug(slug) or normalize_migration_slug(plan.subject) or "unknown-subject"
