from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from dory_core.embedding import ContentEmbedder
from dory_core.errors import DoryValidationError
from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.frontmatter import dump_markdown_document, load_markdown_document, merge_frontmatter
from dory_core.index.reindex import reindex_paths
from dory_core.link import load_known_entities, sync_document_edges
from dory_core.metadata import normalize_frontmatter, resolve_write_target
from dory_core.schema import TIMELINE_MARKER
from dory_core.types import WriteReq, WriteResp

_INJECTION_PATTERNS = [
    re.compile(r"ignore (?:all\s+)?previous instructions", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"<(?:system|assistant|developer|tool|function)", re.IGNORECASE),
]
_ZERO_WIDTH_PATTERN = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_VALID_TARGET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.\-/]*\.md$")
_TIMELINE_ENTRY_PATTERN = re.compile(r"(?m)^\s*-\s*\d{4}-\d{2}-\d{2}:")


@dataclass(frozen=True, slots=True)
class WriteEngine:
    root: Path
    max_write_bytes: int = 10_240
    index_root: Path | None = None
    embedder: ContentEmbedder | None = None

    def write(self, req: WriteReq) -> WriteResp:
        requested_target = self._validate_target(req.target)
        content_issue = self._find_content_issue(req.content)
        if content_issue is not None:
            if req.soft:
                if req.dry_run:
                    quarantine_rel = self._quarantine_target(requested_target, req.content)
                    rendered = self._render_quarantine_document(req, requested_target, content_issue, quarantine_rel)
                    return WriteResp(
                        path=str(quarantine_rel),
                        action="would_quarantine",
                        bytes_written=len(req.content.encode("utf-8")),
                        hash=f"sha256:{sha256(rendered.encode('utf-8')).hexdigest()}",
                        indexed=False,
                        edges_added=0,
                    )
                return self._quarantine_write(req, requested_target, content_issue)
            raise DoryValidationError(content_issue)

        target_rel = self._resolve_target(req, requested_target)
        target = resolve_corpus_target(self.root, target_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        changed_documents: dict[Path, str] = {}

        if req.kind == "create":
            if target.exists():
                raise DoryValidationError(f"target already exists: {req.target}")
            frontmatter = self._normalize_for_new_document(req.frontmatter, target_rel=target_rel)
            if TIMELINE_MARKER in req.content:
                frontmatter["has_timeline"] = True
            rendered = dump_markdown_document(frontmatter, req.content)
            action = "created"
            changed_documents[target_rel] = rendered
        elif req.kind == "append":
            rendered = self._append_document(target, target_rel, req.content, req.frontmatter)
            action = "appended"
            changed_documents[target_rel] = rendered
        elif req.kind == "replace":
            if not target.exists():
                raise DoryValidationError(f"target does not exist: {req.target}")
            current_text = target.read_text(encoding="utf-8")
            current_hash = f"sha256:{sha256(current_text.encode('utf-8')).hexdigest()}"
            if req.expected_hash is None or req.expected_hash != current_hash:
                raise DoryValidationError("replace requires a matching expected_hash")
            current = load_markdown_document(current_text)
            frontmatter = normalize_frontmatter(
                merge_frontmatter(current.frontmatter, req.frontmatter),
                target=target_rel,
            )
            frontmatter["updated"] = _today_iso()
            rendered = dump_markdown_document(frontmatter, req.content)
            action = "replaced"
            changed_documents[target_rel] = rendered
        elif req.kind == "forget":
            if not target.exists():
                raise DoryValidationError(f"target does not exist: {req.target}")
            if not req.reason:
                raise DoryValidationError("forget requires a reason")
            rendered, tombstone_rel, tombstone_rendered = self._forget_document(target, target_rel, req.reason)
            action = "forgotten"
            changed_documents[target_rel] = rendered
            changed_documents[tombstone_rel] = tombstone_rendered
        else:
            raise DoryValidationError(f"write kind not yet supported: {req.kind}")

        if req.dry_run:
            preview_action = {
                "created": "would_create",
                "appended": "would_append",
                "replaced": "would_replace",
                "forgotten": "would_forget",
            }.get(action, f"would_{action}")
            return WriteResp(
                path=str(target_rel),
                action=preview_action,
                bytes_written=len(req.content.encode("utf-8")),
                hash=f"sha256:{sha256(rendered.encode('utf-8')).hexdigest()}",
                indexed=False,
                edges_added=0,
            )

        for changed_path, markdown in changed_documents.items():
            changed_target = resolve_corpus_target(self.root, changed_path)
            changed_target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(changed_target, markdown, encoding="utf-8")

        indexed = False
        edges_added = 0
        if self.index_root is not None and self.embedder is not None:
            reindex_paths(
                self.root,
                self.index_root,
                self.embedder,
                [str(path) for path in changed_documents],
            )
            known_entities = load_known_entities(self.root)
            for changed_path, markdown in changed_documents.items():
                edges_added += sync_document_edges(
                    self.index_root / "dory.db",
                    from_path=str(changed_path),
                    markdown=markdown,
                    known_entities=known_entities,
                )
            indexed = True

        return WriteResp(
            path=str(target_rel),
            action=action,
            bytes_written=len(req.content.encode("utf-8")),
            hash=f"sha256:{sha256(rendered.encode('utf-8')).hexdigest()}",
            indexed=indexed,
            edges_added=edges_added,
        )

    def _quarantine_write(
        self,
        req: WriteReq,
        requested_target: Path,
        reason: str,
    ) -> WriteResp:
        quarantine_rel = self._quarantine_target(requested_target, req.content)
        quarantine_target = resolve_corpus_target(self.root, quarantine_rel)
        quarantine_target.parent.mkdir(parents=True, exist_ok=True)
        rendered = self._render_quarantine_document(req, requested_target, reason, quarantine_rel)
        atomic_write_text(quarantine_target, rendered, encoding="utf-8")
        return WriteResp(
            path=str(quarantine_rel),
            action="quarantined",
            bytes_written=len(req.content.encode("utf-8")),
            hash=f"sha256:{sha256(rendered.encode('utf-8')).hexdigest()}",
            indexed=False,
            edges_added=0,
        )

    def quarantine(
        self,
        *,
        requested_target: str,
        content: str,
        reason: str,
        frontmatter: dict[str, Any] | None = None,
        kind: str = "append",
    ) -> WriteResp:
        validated_target = self._validate_target(requested_target)
        return self._quarantine_write(
            WriteReq(
                kind=kind,
                target=requested_target,
                content=content,
                soft=True,
                frontmatter=frontmatter,
            ),
            validated_target,
            reason,
        )

    def quarantine_target(self, *, requested_target: str, content: str) -> Path:
        return self._quarantine_target(self._validate_target(requested_target), content)

    def _append_document(
        self,
        target: Path,
        target_rel: Path,
        content: str,
        frontmatter: dict[str, Any] | None,
    ) -> str:
        if target.exists():
            current = load_markdown_document(target.read_text(encoding="utf-8"))
            merged_frontmatter = normalize_frontmatter(
                merge_frontmatter(current.frontmatter, frontmatter),
                target=target_rel,
            )
            merged_body, timeline_changed = _merge_appended_body(current.body, content)
            if TIMELINE_MARKER in merged_body:
                merged_frontmatter["has_timeline"] = True
            if not timeline_changed:
                merged_frontmatter["updated"] = _today_iso()
            return dump_markdown_document(merged_frontmatter, merged_body)

        merged_frontmatter = self._normalize_for_new_document(frontmatter, target_rel=target_rel)
        if TIMELINE_MARKER in content:
            merged_frontmatter["has_timeline"] = True
        return dump_markdown_document(merged_frontmatter, content)

    def _forget_document(self, target: Path, target_rel: Path, reason: str) -> tuple[str, Path, str]:
        current = load_markdown_document(target.read_text(encoding="utf-8"))
        tombstone_path = f"{target.stem}.tombstone.md"
        updated_frontmatter = merge_frontmatter(
            current.frontmatter,
            {
                "superseded_by": str(target.with_name(tombstone_path).name),
                "status": "superseded",
                "canonical": False,
                "source_kind": "generated",
                "temperature": "cold",
            },
        )
        updated_rendered = dump_markdown_document(
            normalize_frontmatter(updated_frontmatter, target=target_rel),
            current.body,
        )
        tombstone_rel = target_rel.with_name(tombstone_path)
        tombstone_rendered = dump_markdown_document(
            normalize_frontmatter(
                {
                    "title": f"Tombstone for {target.stem}",
                    "type": current.frontmatter.get("type", "capture"),
                    "status": "superseded",
                    "canonical": False,
                    "source_kind": "generated",
                    "temperature": "cold",
                },
                target=tombstone_rel,
            ),
            reason,
        )
        return updated_rendered, tombstone_rel, tombstone_rendered

    def _resolve_target(self, req: WriteReq, requested_target: Path) -> Path:
        exact_target = resolve_corpus_target(self.root, requested_target)
        if exact_target.exists():
            return requested_target

        if req.frontmatter is None:
            return requested_target

        frontmatter = self._require_frontmatter(req.frontmatter)
        return resolve_write_target(requested_target.as_posix(), frontmatter=frontmatter)

    def _validate_target(self, target: str) -> Path:
        if target.startswith("/"):
            raise DoryValidationError("target must be relative to corpus root")
        if ".." in Path(target).parts:
            raise DoryValidationError("target cannot escape corpus root")
        if not _VALID_TARGET_PATTERN.match(target):
            raise DoryValidationError(f"invalid target path: {target}")
        return Path(target)

    def _find_content_issue(self, content: str) -> str | None:
        if len(content.encode("utf-8")) > self.max_write_bytes:
            return "content exceeds max write size"
        if _ZERO_WIDTH_PATTERN.search(content):
            return "content contains invisible unicode"
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(content):
                return "content failed injection scan"
        return None

    @staticmethod
    def _require_frontmatter(frontmatter: dict[str, Any] | None) -> dict[str, Any]:
        if frontmatter is None:
            raise DoryValidationError("frontmatter is required when creating a new file")
        missing = sorted({"title", "type"}.difference(frontmatter))
        if missing:
            raise DoryValidationError(f"frontmatter missing required fields: {', '.join(missing)}")
        return dict(frontmatter)

    def _normalize_for_new_document(
        self,
        frontmatter: dict[str, Any] | None,
        *,
        target_rel: Path,
    ) -> dict[str, Any]:
        return normalize_frontmatter(self._require_frontmatter(frontmatter), target=target_rel)

    def _quarantine_target(self, requested_target: Path, content: str) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", requested_target.as_posix().lower()).strip("-") or "write"
        digest = sha256(content.encode("utf-8")).hexdigest()[:12]
        return Path("inbox/quarantine") / f"{slug}-{digest}.md"

    def _render_quarantine_document(
        self,
        req: WriteReq,
        requested_target: Path,
        reason: str,
        quarantine_rel: Path,
    ) -> str:
        frontmatter: dict[str, Any] = {
            "title": f"Quarantine: {requested_target.as_posix()}",
            "type": "capture",
            "status": "raw",
            "canonical": False,
            "source_kind": "generated",
            "temperature": "cold",
            "quarantined_at": _today_iso(),
            "quarantine_reason": reason,
            "original_target": requested_target.as_posix(),
            "original_kind": req.kind,
        }
        if req.frontmatter is not None:
            frontmatter["original_frontmatter"] = req.frontmatter
        frontmatter = normalize_frontmatter(frontmatter, target=quarantine_rel)
        return dump_markdown_document(frontmatter, req.content)


def _merge_appended_body(existing_body: str, addition: str) -> tuple[str, bool]:
    existing = existing_body.rstrip()
    content = addition.rstrip()
    if not existing:
        return content, _looks_like_timeline_entry(content)
    if TIMELINE_MARKER not in existing:
        return f"{existing}\n\n{content}", False

    compiled, _, timeline = existing.partition(TIMELINE_MARKER)
    compiled_text = compiled.rstrip()
    timeline_text = timeline.lstrip("\n").rstrip()
    if _looks_like_timeline_entry(content):
        updated_timeline = f"{timeline_text}\n{content}".strip() if timeline_text else content
        return _render_timeline_body(compiled_text, updated_timeline), True

    updated_compiled = f"{compiled_text}\n\n{content}".strip() if compiled_text else content
    return _render_timeline_body(updated_compiled, timeline_text), False


def _render_timeline_body(compiled: str, timeline: str) -> str:
    sections: list[str] = []
    if compiled:
        sections.append(compiled.strip())
    sections.append(TIMELINE_MARKER)
    if timeline:
        sections.append(timeline.strip())
    return "\n\n".join(sections).rstrip()


def _looks_like_timeline_entry(content: str) -> bool:
    return bool(_TIMELINE_ENTRY_PATTERN.search(content.strip()))


def _today_iso() -> str:
    return datetime.now(tz=UTC).date().isoformat()
