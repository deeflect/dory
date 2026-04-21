from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from dory_core.canonical_pages import (
    build_timeline_entry,
    canonical_title_from_subject,
    infer_aliases_from_subject,
    merge_section_content,
    patch_canonical_markdown,
    patch_core_markdown,
    render_canonical_from_claims,
    render_retired_canonical_from_claims,
)
from dory_core.claim_store import ClaimStore
from dory_core.entity_registry import EntityRegistry
from dory_core.embedding import ContentEmbedder
from dory_core.errors import DoryValidationError
from dory_core.frontmatter import dump_markdown_document, load_markdown_document
from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.llm.openrouter import OpenRouterClient, build_openrouter_client
from dory_core.migration_normalize import canonical_target_for_subject, normalize_migration_slug
from dory_core.metadata import normalize_frontmatter
from dory_core.config import DorySettings
from dory_core.subject_resolver import (
    RegistryBackedSubjectResolver,
    SubjectMatch,
    SubjectResolver,
    SubjectResolverLike,
)
from dory_core.types import MemoryWriteAction, MemoryWriteKind, MemoryWriteReq, MemoryWriteResp, WriteReq
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

ResolvedMode = Literal["append", "replace", "forget"]


@dataclass(frozen=True, slots=True)
class SemanticWritePlan:
    action: MemoryWriteAction
    kind: MemoryWriteKind
    subject: str
    subject_ref: str
    target_subject_ref: str
    family: str
    title: str
    target_path: str
    resolved_mode: ResolvedMode
    content: str
    scope: str | None
    confidence: Literal["high", "medium", "low"] | None
    soft: bool
    match_confidence: Literal["high", "medium", "low"]
    reason: str | None
    source: str | None
    matched_by: str
    target_exists: bool


@dataclass(frozen=True, slots=True)
class _SemanticEvidenceArtifact:
    path: str
    frontmatter: dict[str, object]
    content: str



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
        self.writer = WriteEngine(root=self.root, index_root=index_root, embedder=embedder)
        self.registry = EntityRegistry(self.root / ".dory" / "entity-registry.db")
        self.claim_store = ClaimStore(self.root / ".dory" / "claim-store.db")
        resolved_client = (
            resolver_client
            if resolver_client is not None
            else build_openrouter_client(
                DorySettings(),
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
                message=message,
            )

        if not req.dry_run and not req.allow_canonical and _is_canonical_semantic_target(plan):
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
                message=(
                    "live semantic write resolves to canonical memory; rerun with dry_run=true "
                    "to preview, allow_canonical=true to commit, or force_inbox=true for a tentative capture"
                ),
            )

        try:
            semantic_evidence = self._plan_semantic_evidence_artifact(plan)
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
                    message=_semantic_write_preview_message(
                        plan,
                        action=response.action,
                        evidence_path=semantic_evidence.path,
                    ),
                )
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
                    message=_semantic_write_preview_message(
                        plan,
                        action="would_update_large_target",
                        evidence_path=semantic_evidence.path,
                    )
                    + "; rendered target exceeds preview write-size limit",
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

        if response.action == "quarantined":
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
                message="semantic write content was quarantined",
            )

        self._write_semantic_evidence_artifact(semantic_evidence)
        result = "written"
        if plan.resolved_mode == "replace":
            result = "replaced"
        elif plan.resolved_mode == "forget":
            result = "forgotten"

        self._record_claims(plan, evidence_path=semantic_evidence.path)
        self._sync_registry(plan, requested_subject=req.subject)
        if plan.family != "core" and plan.resolved_mode != "forget":
            self._rewrite_canonical_from_claims(plan, requested_subject=req.subject)
        if plan.family != "core" and plan.resolved_mode == "forget":
            self._rewrite_tombstone_from_claims(plan, requested_subject=req.subject)

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

    def _plan_semantic_evidence_artifact(self, plan: SemanticWritePlan) -> _SemanticEvidenceArtifact:
        subject_slug = self._semantic_evidence_subject_slug(plan)
        artifact_path = self._semantic_evidence_path(plan.action, subject_slug=subject_slug)
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
            "origin_surface": plan.source or "semantic-write",
            "canonical_target": plan.target_path,
        }
        return _SemanticEvidenceArtifact(
            path=artifact_path,
            frontmatter=frontmatter,
            content=plan.content,
        )

    def _write_semantic_evidence_artifact(self, artifact: _SemanticEvidenceArtifact) -> None:
        target = resolve_corpus_target(self.root, Path(artifact.path))
        if target.exists():
            raise DoryValidationError(f"semantic evidence artifact already exists: {artifact.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = normalize_frontmatter(artifact.frontmatter, target=Path(artifact.path))
        rendered = dump_markdown_document(frontmatter, artifact.content)
        atomic_write_text(target, rendered, encoding="utf-8")

    def _semantic_evidence_path(self, action: MemoryWriteAction, *, subject_slug: str) -> str:
        while True:
            stamp = datetime.now(tz=UTC)
            day_path = stamp.strftime("%Y/%m/%d")
            candidate = f"sources/semantic/{day_path}/{subject_slug}-{stamp.strftime('%Y%m%d-%H%M%S-%f')}-{action}.md"
            if not resolve_corpus_target(self.root, Path(candidate)).exists():
                return candidate

    def _sync_registry(self, plan: SemanticWritePlan, *, requested_subject: str) -> None:
        self.registry.upsert(
            entity_id=plan.target_subject_ref,
            family=plan.family,
            title=plan.title,
            target_path=plan.target_path,
            aliases=infer_aliases_from_subject(plan.target_subject_ref, requested_subject=requested_subject),
        )
        if plan.subject_ref != plan.target_subject_ref:
            subject_family = _family_from_subject_ref(plan.subject_ref)
            self.registry.upsert(
                entity_id=plan.subject_ref,
                family=subject_family,
                title=canonical_title_from_subject(plan.subject_ref),
                target_path=canonical_target_for_subject(plan.subject_ref),
                aliases=infer_aliases_from_subject(plan.subject_ref, requested_subject=requested_subject),
            )

    def _record_claims(self, plan: SemanticWritePlan, *, evidence_path: str) -> None:
        statement = plan.content.strip()
        if not statement:
            return

        confidence = plan.confidence or plan.match_confidence
        if plan.resolved_mode == "replace":
            self.claim_store.replace_current_claim(
                entity_id=plan.target_subject_ref,
                kind=plan.kind,
                statement=statement,
                evidence_path=evidence_path,
                confidence=confidence,
                reason=plan.reason,
            )
            return

        if plan.resolved_mode == "forget":
            self.claim_store.retire_entity_claims(
                entity_id=plan.target_subject_ref,
                reason=plan.reason or f"semantic {plan.action}",
                kind=None if plan.kind == "note" else plan.kind,
                evidence_path=evidence_path,
            )
            return

        self.claim_store.add_claim(
            entity_id=plan.target_subject_ref,
            kind=plan.kind,
            statement=statement,
            evidence_path=evidence_path,
            confidence=confidence,
        )

    def _rewrite_canonical_from_claims(self, plan: SemanticWritePlan, *, requested_subject: str) -> None:
        claims = self.claim_store.current_claims(plan.target_subject_ref)
        history = self.claim_store.claim_history(plan.target_subject_ref)
        events = self.claim_store.claim_events(plan.target_subject_ref)
        update = render_canonical_from_claims(
            family=plan.family,
            title=plan.title,
            entity_id=plan.target_subject_ref,
            claims=claims,
            history=history,
            events=events,
            aliases=infer_aliases_from_subject(plan.target_subject_ref, requested_subject=requested_subject),
        )
        document = load_markdown_document(update.body)
        target = Path(plan.target_path)
        write_kind = "replace" if (self.root / target).exists() else "create"
        expected_hash = self._current_hash_for_target(target) if write_kind == "replace" else None
        self.writer.write(
            WriteReq(
                kind=write_kind,
                target=plan.target_path,
                content=document.body,
                frontmatter=document.frontmatter,
                soft=False,
                reason="claim-derived canonical rewrite",
                expected_hash=expected_hash,
            )
        )

    def _rewrite_tombstone_from_claims(self, plan: SemanticWritePlan, *, requested_subject: str) -> None:
        history = self.claim_store.claim_history(plan.target_subject_ref)
        events = self.claim_store.claim_events(plan.target_subject_ref)
        if not history:
            return

        tombstone_target = Path(plan.target_path).with_name(f"{Path(plan.target_path).stem}.tombstone.md")
        update = render_retired_canonical_from_claims(
            family=plan.family,
            title=plan.title,
            entity_id=plan.target_subject_ref,
            history=history,
            events=events,
            aliases=infer_aliases_from_subject(plan.target_subject_ref, requested_subject=requested_subject),
            retirement_reason=plan.reason,
        )
        document = load_markdown_document(update.body)
        write_kind = "replace" if (self.root / tombstone_target).exists() else "create"
        expected_hash = self._current_hash_for_target(tombstone_target) if write_kind == "replace" else None
        self.writer.write(
            WriteReq(
                kind=write_kind,
                target=tombstone_target.as_posix(),
                content=document.body,
                frontmatter=document.frontmatter,
                soft=False,
                reason="claim-derived tombstone rewrite",
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

    def _semantic_evidence_subject_slug(self, plan: SemanticWritePlan) -> str:
        _family, slug = plan.target_subject_ref.split(":", 1)
        return normalize_migration_slug(slug) or normalize_migration_slug(plan.subject) or "unknown-subject"

    def _section_updates(self, plan: SemanticWritePlan) -> dict[str, str]:
        existing_text: str = ""
        target = self.root / plan.target_path
        if target.exists():
            try:
                existing_document = load_markdown_document(target.read_text(encoding="utf-8"))
                existing_text = existing_document.body
            except ValueError:
                existing_text = ""

        primary_section = _primary_section_for_plan(plan)
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


def build_semantic_write_plan(
    root: Path,
    req: MemoryWriteReq,
    *,
    resolver: SubjectResolverLike | None = None,
) -> SemanticWritePlan:
    resolver = resolver or SubjectResolver(root)
    match = resolver.resolve(req.subject, scope=req.scope)
    if match is None:
        raise ValueError(f"could not resolve semantic subject: {req.subject}")

    target_subject_ref, target_family, target_path = _route_target(match, req)
    resolved_mode = _resolve_mode(req.action)
    return SemanticWritePlan(
        action=req.action,
        kind=req.kind,
        subject=req.subject,
        subject_ref=match.subject_ref,
        target_subject_ref=target_subject_ref,
        family=target_family,
        title=canonical_title_from_subject(target_subject_ref)
        if target_family == "decision" and match.family != "decision"
        else match.title,
        target_path=target_path,
        resolved_mode=resolved_mode,
        content=req.content,
        scope=req.scope,
        confidence=req.confidence,
        soft=req.soft,
        match_confidence=match.confidence,
        reason=req.reason,
        source=req.source,
        matched_by=match.matched_by,
        target_exists=(root / target_path).exists(),
    )


def _route_target(match: SubjectMatch, req: MemoryWriteReq) -> tuple[str, str, str]:
    if match.family == "core":
        return match.subject_ref, "core", match.target_path
    if req.kind == "decision":
        if match.family == "decision":
            target_subject_ref = match.subject_ref
        else:
            decision_slug = normalize_migration_slug(req.subject) or normalize_migration_slug(match.title)
            target_subject_ref = f"decision:{decision_slug}"
        return target_subject_ref, "decision", canonical_target_for_subject(target_subject_ref)
    if match.family in {"person", "project", "concept", "decision"}:
        return match.subject_ref, match.family, canonical_target_for_subject(match.subject_ref)
    raise ValueError(f"unsupported semantic family: {match.family}")


def _frontmatter_type_for_family(family: str) -> str:
    if family == "person":
        return "person"
    if family == "project":
        return "project"
    if family == "concept":
        return "concept"
    if family == "decision":
        return "decision"
    if family == "core":
        return "core"
    return "note"


def _resolve_mode(action: MemoryWriteAction) -> ResolvedMode:
    if action == "replace":
        return "replace"
    if action == "forget":
        return "forget"
    return "append"


def _is_canonical_semantic_target(plan: SemanticWritePlan) -> bool:
    if plan.family in {"core", "person", "project", "concept", "decision"}:
        return True
    return plan.target_path.startswith(("core/", "people/", "projects/", "concepts/", "decisions/"))


def _semantic_write_preview_message(plan: SemanticWritePlan, *, action: str, evidence_path: str) -> str:
    prefix = ""
    if _is_canonical_semantic_target(plan):
        prefix = (
            f"CANONICAL TARGET {plan.target_path}; preview only; "
            "use force_inbox=true for tentative notes or allow_canonical=true after review. "
        )
    return f"{prefix}dry_run: {action}; semantic evidence would be {evidence_path}"


def _primary_section_for_plan(plan: SemanticWritePlan) -> str:
    if plan.family == "person":
        if plan.kind == "preference":
            return "Preferences And Working Style"
        return "Current Facts"
    if plan.family == "project":
        if plan.kind == "note":
            return "Open Work"
        return "Current State"
    if plan.family == "concept":
        if plan.kind == "note":
            return "Open Questions"
        return "Current Understanding"
    if plan.family == "decision":
        return "Decision"
    if plan.family == "core":
        stem = Path(plan.target_path).stem
        if stem == "user":
            return "Current Facts" if plan.kind != "preference" else "Preferences And Working Style"
        if stem == "active":
            return "Current Focus"
        if stem == "defaults":
            return "Default Operating Assumptions"
        if stem == "env":
            return "Environment"
        if stem == "identity":
            return "Role"
        return "Behavior Rules"
    return "Summary"


def _section_text(markdown_body: str, section: str) -> str:
    marker = f"## {section}\n"
    if marker not in markdown_body:
        return ""
    _, after = markdown_body.split(marker, 1)
    next_header = after.find("\n## ")
    if next_header == -1:
        return after.strip()
    return after[:next_header].strip()



def _family_from_subject_ref(subject_ref: str) -> str:
    family, _slug = subject_ref.split(":", 1)
    return family
