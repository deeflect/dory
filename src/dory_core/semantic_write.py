from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from dory_core.canonical_pages import (
    build_timeline_entry,
    infer_aliases_from_subject,
    merge_section_content,
    patch_canonical_markdown,
    patch_core_markdown,
)
from dory_core.claim_store import ClaimStore
from dory_core.entity_registry import EntityRegistry
from dory_core.embedding import ContentEmbedder
from dory_core.errors import DoryValidationError
from dory_core.frontmatter import load_markdown_document
from dory_core.llm.openrouter import OpenRouterClient, build_openrouter_client
from dory_core.slug import normalize_migration_slug
from dory_core.config import DorySettings
from dory_core.semantic_write_artifacts import SemanticEvidenceArtifact, SemanticEvidenceStore
from dory_core.semantic_write_canonical import SemanticCanonicalPublisher
from dory_core.semantic_write_claims import SemanticClaimRecorder
from dory_core.semantic_write_plan import (
    ResolvedMode,
    SemanticWritePlan,
    build_semantic_write_plan,
    is_canonical_semantic_target,
    primary_section_for_plan,
    semantic_write_preview_message,
    semantic_write_preview_payload,
    should_rewrite_canonical_from_claims,
)
from dory_core.subject_resolver import (
    RegistryBackedSubjectResolver,
    SubjectMatch,
    SubjectResolver,
    SubjectResolverLike,
)
from dory_core.types import MemoryWriteReq, MemoryWriteResp, WriteReq, WriteResp
from dory_core.write import WriteEngine

__all__ = [
    "ResolvedMode",
    "SemanticWritePlan",
    "SemanticWriteEngine",
    "SubjectMatch",
    "SubjectResolver",
    "SubjectResolverLike",
    "RegistryBackedSubjectResolver",
    "build_semantic_write_plan",
]

class SemanticWriteEngine:
    def __init__(
        self,
        root: Path,
        *,
        index_root: Path | None = None,
        embedder: ContentEmbedder | None = None,
        resolver_client: OpenRouterClient | None = None,
    ) -> None:
        self.root = Path(root)
        settings = DorySettings()
        self.writer = WriteEngine(
            root=self.root,
            max_write_bytes=max(settings.max_write_bytes, 256_000),
            index_root=index_root,
            embedder=embedder,
        )
        self.evidence_store = SemanticEvidenceStore(
            self.root,
            index_root=self.writer.index_root,
            embedder=self.writer.embedder,
        )
        self.registry = EntityRegistry(self.root / ".dory" / "entity-registry.db")
        self.claim_store = ClaimStore(self.root / ".dory" / "claim-store.db")
        self.claim_recorder = SemanticClaimRecorder(
            registry=self.registry,
            claim_store=self.claim_store,
        )
        self.canonical_publisher = SemanticCanonicalPublisher(
            root=self.root,
            writer=self.writer,
            claim_store=self.claim_store,
        )
        resolved_client = (
            resolver_client
            if resolver_client is not None
            else build_openrouter_client(
                settings,
                purpose="maintenance",
            )
        )
        self.resolver = RegistryBackedSubjectResolver(
            self.root,
            registry=self.registry,
            fallback=SubjectResolver(self.root),
            llm_client=resolved_client,
        )

    def write(self, req: MemoryWriteReq) -> MemoryWriteResp:
        if req.force_inbox:
            return self._write_forced_inbox(req)

        try:
            plan = build_semantic_write_plan(self.root, req, resolver=self.resolver)
        except ValueError as err:
            if req.soft:
                if req.dry_run:
                    return self._preview_quarantine_unresolved_request(req, str(err))
                return self._quarantine_unresolved_request(req, str(err))
            return MemoryWriteResp(
                resolved=False,
                action=req.action,
                kind=req.kind,
                result="rejected",
                indexed=False,
                quarantined=False,
                message=str(err),
            )

        if plan.match_confidence == "low":
            message = f"ambiguous semantic subject: {req.subject}"
            if req.soft:
                if req.dry_run:
                    return self._preview_quarantine_unresolved_request(
                        req,
                        message,
                        subject_ref=plan.subject_ref,
                        confidence=req.confidence or plan.match_confidence,
                    )
                return self._quarantine_unresolved_request(
                    req,
                    message,
                    subject_ref=plan.subject_ref,
                    confidence=req.confidence or plan.match_confidence,
                )
            return MemoryWriteResp(
                resolved=False,
                action=req.action,
                kind=req.kind,
                subject_ref=plan.subject_ref,
                target_path=plan.target_path,
                result="rejected",
                confidence=req.confidence or plan.match_confidence,
                indexed=False,
                quarantined=False,
                matched_by=plan.matched_by,
                message=message,
            )

        if not req.dry_run and not req.allow_canonical and is_canonical_semantic_target(plan):
            return MemoryWriteResp(
                resolved=True,
                action=req.action,
                kind=req.kind,
                subject_ref=plan.subject_ref,
                target_path=plan.target_path,
                result="rejected",
                confidence=req.confidence or plan.match_confidence,
                indexed=False,
                quarantined=False,
                matched_by=plan.matched_by,
                message=(
                    "live semantic write resolves to canonical memory; rerun with dry_run=true "
                    "to preview, allow_canonical=true to commit, or force_inbox=true for a tentative capture"
                ),
            )

        existing_evidence_path = self._find_existing_semantic_evidence(plan)
        if existing_evidence_path is not None and self._canonical_already_reflects_plan(plan):
            return MemoryWriteResp(
                resolved=True,
                action=req.action,
                kind=req.kind,
                subject_ref=plan.subject_ref,
                target_path=plan.target_path,
                result="forgotten" if plan.resolved_mode == "forget" else "replaced" if plan.resolved_mode == "replace" else "written",
                confidence=req.confidence or plan.confidence,
                indexed=False,
                quarantined=False,
                evidence_path=existing_evidence_path,
                matched_by=plan.matched_by,
                message="idempotent semantic write replay; existing evidence reused",
            )

        try:
            semantic_evidence = self._semantic_evidence_for_plan(plan, existing_evidence_path=existing_evidence_path)
            if req.dry_run or not (plan.resolved_mode == "forget" and self._should_patch_forget_exact_content(plan)):
                low_level_req = self._build_low_level_write_req(plan, evidence_path=semantic_evidence.path)
                if low_level_req.kind == "replace":
                    self._ensure_replace_target_exists(Path(low_level_req.target))
            if req.dry_run:
                response = self.writer.write(low_level_req.model_copy(update={"dry_run": True}))
                return MemoryWriteResp(
                    resolved=True,
                    action=req.action,
                    kind=req.kind,
                    subject_ref=plan.subject_ref,
                    target_path=response.path,
                    result="preview",
                    confidence=req.confidence or plan.confidence,
                    indexed=False,
                    quarantined=False,
                    message=semantic_write_preview_message(
                        plan,
                        action=response.action,
                        evidence_path=semantic_evidence.path,
                    ),
                    evidence_path=semantic_evidence.path,
                    matched_by=plan.matched_by,
                    preview=semantic_write_preview_payload(
                        plan,
                        action=response.action,
                        evidence_path=semantic_evidence.path,
                    ),
                )
            response: WriteResp | None = None
            if should_rewrite_canonical_from_claims(plan):
                if req.soft and self.writer._find_content_issue(low_level_req.content) is not None:
                    response = self.writer.write(low_level_req)
                else:
                    response = None
            elif plan.resolved_mode == "forget" and self._should_patch_forget_exact_content(plan):
                response = self._write_semantic_forget_patch(plan)
            else:
                response = self.writer.write(low_level_req)
        except DoryValidationError as err:
            if req.dry_run and str(err) == "content exceeds max write size":
                return MemoryWriteResp(
                    resolved=True,
                    action=req.action,
                    kind=req.kind,
                    subject_ref=plan.subject_ref,
                    target_path=plan.target_path,
                    result="preview",
                    confidence=req.confidence or plan.confidence,
                    indexed=False,
                    quarantined=False,
                    message=semantic_write_preview_message(
                        plan,
                        action="would_update_large_target",
                        evidence_path=semantic_evidence.path,
                    )
                    + "; rendered target exceeds preview write-size limit",
                    evidence_path=semantic_evidence.path,
                    matched_by=plan.matched_by,
                    preview=semantic_write_preview_payload(
                        plan,
                        action="would_update_large_target",
                        evidence_path=semantic_evidence.path,
                    ),
                )
            return MemoryWriteResp(
                resolved=True,
                action=req.action,
                kind=req.kind,
                subject_ref=plan.subject_ref,
                target_path=plan.target_path,
                result="quarantined" if req.soft else "rejected",
                confidence=req.confidence,
                indexed=False,
                quarantined=req.soft,
                message=str(err),
            )

        if response is not None and response.action == "quarantined":
            return MemoryWriteResp(
                resolved=True,
                action=req.action,
                kind=req.kind,
                subject_ref=plan.subject_ref,
                target_path=response.path,
                result="quarantined",
                confidence=req.confidence,
                indexed=response.indexed,
                quarantined=True,
                evidence_path=semantic_evidence.path,
                matched_by=plan.matched_by,
                message="semantic write content was quarantined",
            )

        if existing_evidence_path is None:
            self.evidence_store.write(semantic_evidence)
        result = "written"
        if plan.resolved_mode == "replace":
            result = "replaced"
        elif plan.resolved_mode == "forget":
            result = "forgotten"

        self.claim_recorder.record(plan, evidence_path=semantic_evidence.path)
        self.claim_recorder.sync_registry(plan, requested_subject=req.subject)
        if should_rewrite_canonical_from_claims(plan):
            response = self.canonical_publisher.rewrite_from_claims(plan, requested_subject=req.subject)
        if plan.family != "core" and plan.resolved_mode == "forget":
            self.canonical_publisher.rewrite_tombstone_from_claims(plan, requested_subject=req.subject)

        if response is None:
            raise DoryValidationError("semantic write did not produce a write response")

        return MemoryWriteResp(
            resolved=True,
            action=req.action,
            kind=req.kind,
            subject_ref=plan.subject_ref,
            target_path=response.path,
            result=result,
            confidence=req.confidence or plan.confidence,
            indexed=response.indexed,
            quarantined=False,
            evidence_path=semantic_evidence.path,
            matched_by=plan.matched_by,
            message=None,
        )

    def _write_forced_inbox(self, req: MemoryWriteReq) -> MemoryWriteResp:
        target_path = self._forced_inbox_target(req)
        frontmatter: dict[str, object] = {
            "title": f"Inbox semantic capture for {req.subject}",
            "type": "capture",
            "status": "raw",
            "canonical": False,
            "source_kind": "semantic",
            "temperature": "cold",
            "original_action": req.action,
            "original_kind": req.kind,
            "original_subject": req.subject,
            "original_scope": req.scope,
            "original_confidence": req.confidence,
            "original_reason": req.reason,
            "original_source": req.source,
            "agent": req.agent,
            "session_id": req.session_id,
            "origin_surface": req.origin_surface,
            "forced_inbox": True,
        }
        response = self.writer.write(
            WriteReq(
                kind="create",
                target=target_path,
                content=req.content,
                soft=req.soft,
                dry_run=req.dry_run,
                frontmatter=frontmatter,
                reason=req.reason or "forced semantic inbox capture",
            )
        )
        if response.action == "quarantined":
            return MemoryWriteResp(
                resolved=False,
                action=req.action,
                kind=req.kind,
                target_path=response.path,
                result="quarantined",
                confidence=req.confidence,
                indexed=response.indexed,
                quarantined=True,
                message="force_inbox content was quarantined",
            )
        return MemoryWriteResp(
            resolved=False,
            action=req.action,
            kind=req.kind,
            subject_ref=None,
            target_path=response.path,
            result="preview" if req.dry_run else "written",
            confidence=req.confidence,
            indexed=response.indexed,
            quarantined=False,
            message=f"force_inbox: {response.action}",
        )

    def _build_low_level_write_req(self, plan: SemanticWritePlan, *, evidence_path: str) -> WriteReq:
        kind = "create"
        if plan.resolved_mode == "forget":
            kind = "forget"
        elif plan.target_exists:
            kind = "replace"

        reason = plan.reason or f"semantic {plan.action}"
        expected_hash = None
        if kind == "replace":
            expected_hash = self._current_hash_for_target(Path(plan.target_path))
        frontmatter, body = self._canonical_rendered_document(plan, evidence_path=evidence_path)
        return WriteReq(
            kind=kind,
            target=plan.target_path,
            content=body if kind != "forget" else plan.content,
            soft=plan.soft,
            frontmatter=frontmatter if kind != "forget" else None,
            reason=reason,
            expected_hash=expected_hash,
        )

    def _quarantine_unresolved_request(
        self,
        req: MemoryWriteReq,
        reason: str,
        *,
        subject_ref: str | None = None,
        confidence: Literal["high", "medium", "low"] | None = None,
    ) -> MemoryWriteResp:
        response = self.writer.quarantine(
            requested_target=self._semantic_quarantine_target(req),
            content=req.content,
            reason=reason,
            frontmatter={
                "title": f"Semantic quarantine for {req.subject}",
                "type": "capture",
                "original_action": req.action,
                "original_kind": req.kind,
                "original_subject": req.subject,
                "original_scope": req.scope,
                "original_reason": req.reason,
                "original_source": req.source,
                "agent": req.agent,
                "session_id": req.session_id,
                "origin_surface": req.origin_surface,
            },
        )
        return MemoryWriteResp(
            resolved=False,
            action=req.action,
            kind=req.kind,
            subject_ref=subject_ref,
            target_path=response.path,
            result="quarantined",
            confidence=confidence,
            indexed=response.indexed,
            quarantined=True,
            message=reason,
        )

    def _preview_quarantine_unresolved_request(
        self,
        req: MemoryWriteReq,
        reason: str,
        *,
        subject_ref: str | None = None,
        confidence: Literal["high", "medium", "low"] | None = None,
    ) -> MemoryWriteResp:
        target_path = self.writer.quarantine_target(
            requested_target=self._semantic_quarantine_target(req),
            content=req.content,
        )
        return MemoryWriteResp(
            resolved=False,
            action=req.action,
            kind=req.kind,
            subject_ref=subject_ref,
            target_path=target_path.as_posix(),
            result="preview",
            confidence=confidence,
            indexed=False,
            quarantined=True,
            message=f"dry_run: would quarantine unresolved semantic write: {reason}",
        )

    def _semantic_quarantine_target(self, req: MemoryWriteReq) -> str:
        scope = req.scope or "unknown"
        subject_slug = normalize_migration_slug(req.subject) or "unknown-subject"
        return f"semantic/{scope}-{subject_slug}.md"

    def _forced_inbox_target(self, req: MemoryWriteReq) -> str:
        subject_slug = normalize_migration_slug(req.subject) or "unknown-subject"
        stamp = datetime.now(tz=UTC).strftime("%Y-%m-%d-%H%M%S-%f")
        return f"inbox/semantic/{stamp}-{subject_slug}.md"

    def _find_existing_semantic_evidence(self, plan: SemanticWritePlan) -> str | None:
        semantic_root = self.root / "sources" / "semantic"
        if not semantic_root.exists():
            return None
        expected_content = _normalize_semantic_text(plan.content)
        for path in sorted(semantic_root.rglob("*.md"), reverse=True):
            try:
                document = load_markdown_document(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if document.frontmatter.get("source_kind") != "semantic":
                continue
            if document.frontmatter.get("entity_id") != plan.target_subject_ref:
                continue
            if document.frontmatter.get("action") != plan.action:
                continue
            if document.frontmatter.get("kind") != plan.kind:
                continue
            if document.frontmatter.get("canonical_target") != plan.target_path:
                continue
            if _normalize_semantic_text(document.body) == expected_content:
                return path.relative_to(self.root).as_posix()
        return None

    def _semantic_evidence_for_plan(
        self,
        plan: SemanticWritePlan,
        *,
        existing_evidence_path: str | None,
    ) -> SemanticEvidenceArtifact:
        if existing_evidence_path is None:
            return self.evidence_store.plan(plan)
        return SemanticEvidenceArtifact(
            path=existing_evidence_path,
            frontmatter={},
            content=plan.content,
        )

    def _canonical_already_reflects_plan(self, plan: SemanticWritePlan) -> bool:
        target = self.root / plan.target_path
        if not target.exists():
            return False
        try:
            body = load_markdown_document(target.read_text(encoding="utf-8")).body
        except ValueError:
            return False
        has_content = _canonical_contains_exact_content(body, plan.content)
        if plan.resolved_mode == "forget":
            return not has_content
        return has_content

    def _should_patch_forget_exact_content(self, plan: SemanticWritePlan) -> bool:
        target = self.root / plan.target_path
        if not target.exists():
            return False
        try:
            document = load_markdown_document(target.read_text(encoding="utf-8"))
        except ValueError:
            return False
        if _remove_exact_content_from_body(document.body, plan.content) == document.body:
            return False
        active_claims = self.claim_store.current_claims(
            plan.target_subject_ref,
            kind=None if plan.kind == "note" else plan.kind,
        )
        return not any(_normalize_semantic_text(claim.statement) == _normalize_semantic_text(plan.content) for claim in active_claims)

    def _write_semantic_forget_patch(self, plan: SemanticWritePlan) -> WriteResp:
        target = self.root / plan.target_path
        document = load_markdown_document(target.read_text(encoding="utf-8"))
        updated_body = _remove_exact_content_from_body(document.body, plan.content)
        expected_hash = self._current_hash_for_target(Path(plan.target_path))
        return self.writer.write(
            WriteReq(
                kind="replace",
                target=plan.target_path,
                content=updated_body,
                frontmatter=document.frontmatter,
                soft=False,
                reason=plan.reason or f"semantic {plan.action}",
                expected_hash=expected_hash,
            )
        )

    def _canonical_rendered_document(
        self, plan: SemanticWritePlan, *, evidence_path: str
    ) -> tuple[dict[str, object], str]:
        target = self.root / plan.target_path
        current_text = target.read_text(encoding="utf-8") if target.exists() else None
        aliases = infer_aliases_from_subject(plan.target_subject_ref, requested_subject=plan.subject)
        section_updates = self._section_updates(plan)
        timeline_entries = (
            build_timeline_entry(
                time_ref=None,
                summary=plan.content,
                evidence_path=evidence_path,
            ),
        )
        evidence_paths = (evidence_path,)
        if plan.family == "core":
            update = patch_core_markdown(
                current_text,
                file_name=Path(plan.target_path).name,
                title=plan.title,
                aliases=aliases,
                section_updates=section_updates,
                timeline_entries=timeline_entries,
                evidence_paths=evidence_paths,
            )
        else:
            update = patch_canonical_markdown(
                current_text,
                family=plan.family,
                title=plan.title,
                slug=Path(plan.target_path).parent.name
                if Path(plan.target_path).name == "state.md"
                else Path(plan.target_path).stem,
                domain="mixed",
                aliases=aliases,
                section_updates=section_updates,
                timeline_entries=timeline_entries,
                evidence_paths=evidence_paths,
            )
        document = load_markdown_document(update.body)
        return document.frontmatter, document.body

    def _section_updates(self, plan: SemanticWritePlan) -> dict[str, str]:
        existing_text: str = ""
        target = self.root / plan.target_path
        if target.exists():
            try:
                existing_document = load_markdown_document(target.read_text(encoding="utf-8"))
                existing_text = existing_document.body
            except ValueError:
                existing_text = ""

        primary_section = primary_section_for_plan(plan)
        replacement = plan.content.strip()
        if plan.resolved_mode == "replace":
            updates = {primary_section: replacement}
            if plan.family in {"project", "concept", "person"}:
                updates["Summary"] = replacement
            return updates
        if plan.family == "decision" and primary_section == "Decision":
            merged = replacement
        else:
            merged = merge_section_content(_section_text(existing_text, primary_section), replacement, bullet=True)
        updates = {primary_section: merged}
        if plan.family == "person" and plan.kind in {"fact", "note"}:
            summary = merge_section_content(_section_text(existing_text, "Summary"), replacement, bullet=True)
            updates.setdefault("Summary", summary)
        if plan.family == "project" and plan.kind == "state":
            summary = merge_section_content(_section_text(existing_text, "Summary"), replacement, bullet=True)
            updates.setdefault("Summary", summary)
        if plan.family == "concept":
            summary = merge_section_content(_section_text(existing_text, "Summary"), replacement, bullet=True)
            updates.setdefault("Summary", summary)
        return updates

    def _ensure_replace_target_exists(self, target: Path) -> None:
        current = self.root / target
        if not current.exists():
            raise DoryValidationError(f"target does not exist: {target.as_posix()}")

    def _current_hash_for_target(self, target: Path) -> str:
        current = self.root / target
        if not current.exists():
            raise DoryValidationError(f"target does not exist: {target.as_posix()}")
        current_text = current.read_text(encoding="utf-8")
        return f"sha256:{sha256(current_text.encode('utf-8')).hexdigest()}"


def _section_text(markdown_body: str, section: str) -> str:
    marker = f"## {section}\n"
    if marker not in markdown_body:
        return ""
    _, after = markdown_body.split(marker, 1)
    next_header = after.find("\n## ")
    if next_header == -1:
        return after.strip()
    return after[:next_header].strip()


def _normalize_semantic_text(text: str) -> str:
    return " ".join(line.strip().removeprefix("- ").strip() for line in text.splitlines() if line.strip()).strip()


def _canonical_contains_exact_content(markdown_body: str, content: str) -> bool:
    expected = _normalize_semantic_text(content)
    if not expected:
        return False
    return any(_normalize_semantic_text(line) == expected for line in markdown_body.splitlines())


def _remove_exact_content_from_body(markdown_body: str, content: str) -> str:
    expected = _normalize_semantic_text(content)
    if not expected:
        return markdown_body
    lines = markdown_body.splitlines()
    kept = [line for line in lines if _normalize_semantic_text(line) != expected]
    return "\n".join(kept).rstrip() + "\n"
