from __future__ import annotations

import re
from typing import Any
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol

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
from dory_core.entity_registry import EntityRegistry, RegistryMatch
from dory_core.embedding import ContentEmbedder
from dory_core.errors import DoryValidationError
from dory_core.frontmatter import dump_markdown_document, load_markdown_document
from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.llm.openrouter import OpenRouterClient, build_openrouter_client
from dory_core.migration_normalize import canonical_target_for_subject, normalize_migration_slug
from dory_core.metadata import normalize_family_name, normalize_frontmatter
from dory_core.config import DorySettings
from dory_core.types import MemoryWriteAction, MemoryWriteKind, MemoryWriteReq, MemoryWriteResp, WriteReq
from dory_core.write import WriteEngine

ResolvedMode = Literal["append", "replace", "forget"]

_CORE_PATH_PART = "core"
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_SUBJECT_RESOLUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chosen_subject_ref": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "ambiguous": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["chosen_subject_ref", "confidence", "ambiguous", "reason"],
}


@dataclass(frozen=True, slots=True)
class SubjectMatch:
    subject_ref: str
    family: str
    title: str
    target_path: str
    matched_by: str
    confidence: Literal["high", "medium", "low"]


@dataclass(frozen=True, slots=True)
class SubjectResolutionDecision:
    chosen_subject_ref: str | None
    confidence: Literal["high", "medium", "low"]
    ambiguous: bool
    reason: str


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
class _SubjectEntry:
    subject_ref: str
    family: str
    title: str
    aliases: tuple[str, ...]
    target_path: str


@dataclass(frozen=True, slots=True)
class _SemanticEvidenceArtifact:
    path: str
    frontmatter: dict[str, object]
    content: str


class SubjectResolverLike(Protocol):
    def resolve(self, subject: str, *, scope: str | None = None) -> SubjectMatch | None: ...


class SubjectResolver:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._entries = self._load_entries()

    def resolve(self, subject: str, *, scope: str | None = None) -> SubjectMatch | None:
        normalized = self._normalize_text(subject)
        for entry in self.entries(scope=scope):
            matched_by = self._exact_match_kind(normalized, entry)
            if matched_by is None:
                continue
            return SubjectMatch(
                subject_ref=entry.subject_ref,
                family=entry.family,
                title=entry.title,
                target_path=entry.target_path,
                matched_by=matched_by,
                confidence="high",
            )
        return None

    def entries(self, *, scope: str | None = None) -> tuple[_SubjectEntry, ...]:
        if scope is None:
            return self._entries
        return tuple(entry for entry in self._entries if entry.family == scope)

    def _load_entries(self) -> tuple[_SubjectEntry, ...]:
        entries: list[_SubjectEntry] = []
        entries.extend(self._load_core_entries())
        for family in ("people", "projects", "concepts", "decisions"):
            family_root = self.root / family
            if not family_root.exists():
                continue
            if family == "projects":
                entries.extend(self._load_project_entries(family_root))
                continue
            entries.extend(self._load_family_entries(family_root, family=normalize_family_name(family)))
        return tuple(entries)

    def _load_core_entries(self) -> list[_SubjectEntry]:
        entries: list[_SubjectEntry] = []
        core_root = self.root / _CORE_PATH_PART
        if not core_root.exists():
            return entries
        for path in sorted(core_root.glob("*.md")):
            title, aliases = self._load_title_and_aliases(path, fallback=path.stem.replace("-", " ").title())
            entries.append(
                _SubjectEntry(
                    subject_ref=f"core:{path.stem}",
                    family="core",
                    title=title,
                    aliases=aliases,
                    target_path=path.relative_to(self.root).as_posix(),
                )
            )
        return entries

    def _load_family_entries(self, root: Path, *, family: str) -> list[_SubjectEntry]:
        entries: list[_SubjectEntry] = []
        for path in sorted(root.glob("*.md")):
            slug = normalize_migration_slug(path.stem)
            title, aliases = self._load_title_and_aliases(path, fallback=path.stem.replace("-", " ").title())
            entries.append(
                _SubjectEntry(
                    subject_ref=f"{family}:{slug}",
                    family=family,
                    title=title,
                    aliases=aliases,
                    target_path=path.relative_to(self.root).as_posix(),
                )
            )
        return entries

    def _load_project_entries(self, root: Path) -> list[_SubjectEntry]:
        entries: list[_SubjectEntry] = []
        for state_path in sorted(root.glob("*/state.md")):
            slug = normalize_migration_slug(state_path.parent.name)
            title, aliases = self._load_title_and_aliases(
                state_path,
                fallback=state_path.parent.name.replace("-", " ").title(),
            )
            entries.append(
                _SubjectEntry(
                    subject_ref=f"project:{slug}",
                    family="project",
                    title=title,
                    aliases=aliases,
                    target_path=state_path.relative_to(self.root).as_posix(),
                )
            )
        return entries

    def _load_title_and_aliases(self, path: Path, *, fallback: str) -> tuple[str, tuple[str, ...]]:
        try:
            document = load_markdown_document(path.read_text(encoding="utf-8"))
        except ValueError:
            return fallback, ()

        title = fallback
        raw_title = document.frontmatter.get("title")
        if isinstance(raw_title, str) and raw_title.strip():
            title = raw_title.strip()

        aliases = document.frontmatter.get("aliases")
        normalized_aliases: list[str] = []
        if isinstance(aliases, str):
            normalized_aliases.append(aliases.strip())
        elif isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    normalized_aliases.append(alias.strip())

        return title, tuple(normalized_aliases)

    def _exact_match_kind(self, normalized_query: str, entry: _SubjectEntry) -> str | None:
        query_slug = normalize_migration_slug(normalized_query)
        title_slug = normalize_migration_slug(entry.title)
        alias_slugs = tuple(normalize_migration_slug(alias) for alias in entry.aliases)
        subject_slug = entry.subject_ref.split(":", 1)[1]
        # Decision files often have a YYYY-MM-DD- prefix. Allow a slug-only
        # query (for example "homeserver") to match "2026-04-07-homeserver".
        stripped_subject = _DATE_PREFIX_RE.sub("", subject_slug)

        if normalized_query == subject_slug or query_slug == subject_slug:
            return "subject_ref"
        if stripped_subject != subject_slug and (
            normalized_query == stripped_subject or query_slug == stripped_subject
        ):
            return "subject_ref"
        if normalized_query == title_slug or query_slug == title_slug:
            return "title"
        if query_slug in alias_slugs:
            return "alias"
        return None

    def _normalize_text(self, value: str) -> str:
        return normalize_migration_slug(value.strip())


class RegistryBackedSubjectResolver:
    def __init__(
        self,
        root: Path,
        *,
        registry: EntityRegistry,
        fallback: SubjectResolver | None = None,
        llm_client: OpenRouterClient | None = None,
    ) -> None:
        self.root = Path(root)
        self.registry = registry
        self.fallback = fallback
        self.llm_client = llm_client

    def resolve(self, subject: str, *, scope: str | None = None) -> SubjectMatch | None:
        registry_match = self.registry.resolve(subject, family=scope)
        if registry_match is not None:
            return _subject_match_from_registry(registry_match)
        fallback_resolver = self._fresh_fallback()
        fallback_match = fallback_resolver.resolve(subject, scope=scope)
        if fallback_match is not None:
            self.registry.upsert(
                entity_id=fallback_match.subject_ref,
                family=fallback_match.family,
                title=fallback_match.title,
                target_path=fallback_match.target_path,
                aliases=infer_aliases_from_subject(fallback_match.subject_ref, requested_subject=subject),
            )
            return fallback_match
        llm_match = self._resolve_with_llm(subject, scope=scope, resolver=fallback_resolver)
        if llm_match is not None:
            self.registry.upsert(
                entity_id=llm_match.subject_ref,
                family=llm_match.family,
                title=llm_match.title,
                target_path=llm_match.target_path,
                aliases=infer_aliases_from_subject(llm_match.subject_ref, requested_subject=subject),
            )
            return llm_match
        return None

    def _fresh_fallback(self) -> SubjectResolver:
        return SubjectResolver(self.root)

    def _resolve_with_llm(
        self,
        subject: str,
        *,
        scope: str | None,
        resolver: SubjectResolver,
    ) -> SubjectMatch | None:
        if self.llm_client is None:
            return None
        candidates = resolver.entries(scope=scope)
        if not candidates:
            return None
        payload = self.llm_client.generate_json(
            system_prompt=_subject_resolution_system_prompt(),
            user_prompt=_subject_resolution_user_prompt(subject=subject, scope=scope, candidates=candidates),
            schema_name="dory_subject_resolution",
            schema=_SUBJECT_RESOLUTION_SCHEMA,
        )
        decision = _parse_subject_resolution_decision(payload)
        if decision.chosen_subject_ref is None or decision.ambiguous:
            return None
        chosen_ref = _normalize_subject_ref_for_resolution(decision.chosen_subject_ref)
        for entry in candidates:
            if entry.subject_ref != chosen_ref:
                continue
            return SubjectMatch(
                subject_ref=entry.subject_ref,
                family=entry.family,
                title=entry.title,
                target_path=entry.target_path,
                matched_by="llm",
                confidence=decision.confidence,
            )
        return None


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


def _subject_resolution_system_prompt() -> str:
    return (
        "Resolve one semantic memory subject against the provided candidate entities. "
        "Return JSON only. Choose a candidate only when the query clearly refers to that entity. "
        "If the subject is ambiguous or unsupported by the candidate list, set chosen_subject_ref to null. "
        "Prefer precision over recall. Do not invent candidates."
    )


def _subject_resolution_user_prompt(
    *,
    subject: str,
    scope: str | None,
    candidates: tuple[_SubjectEntry, ...],
) -> str:
    scope_text = scope or "any"
    candidate_lines = []
    for entry in candidates:
        aliases = ", ".join(entry.aliases) if entry.aliases else "none"
        candidate_lines.append(
            f"- {entry.subject_ref} | family={entry.family} | title={entry.title} | aliases={aliases} | target={entry.target_path}"
        )
    return f"Subject query: {subject}\nRequested scope: {scope_text}\nCandidates:\n" + "\n".join(candidate_lines)


def _parse_subject_resolution_decision(payload: Any) -> SubjectResolutionDecision:
    if not isinstance(payload, dict):
        raise ValueError("subject resolution payload must be an object")
    chosen_subject_ref = payload.get("chosen_subject_ref")
    if chosen_subject_ref is not None and (not isinstance(chosen_subject_ref, str) or not chosen_subject_ref.strip()):
        raise ValueError("chosen_subject_ref must be a non-empty string or null")
    confidence = payload.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("subject resolution confidence must be high, medium, or low")
    ambiguous = payload.get("ambiguous")
    if not isinstance(ambiguous, bool):
        raise ValueError("subject resolution ambiguous must be a boolean")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("subject resolution reason must be a non-empty string")
    return SubjectResolutionDecision(
        chosen_subject_ref=chosen_subject_ref.strip() if isinstance(chosen_subject_ref, str) else None,
        confidence=confidence,
        ambiguous=ambiguous,
        reason=reason.strip(),
    )


def _normalize_subject_ref_for_resolution(subject_ref: str) -> str:
    if ":" not in subject_ref:
        raise ValueError(f"semantic subject ref missing family: {subject_ref}")
    family, slug = subject_ref.split(":", 1)
    normalized_family = normalize_family_name(family)
    return f"{normalized_family}:{normalize_migration_slug(slug)}"


def _subject_match_from_registry(match: RegistryMatch) -> SubjectMatch:
    return SubjectMatch(
        subject_ref=match.entity_id,
        family=match.family,
        title=match.title,
        target_path=match.target_path,
        matched_by=match.matched_by,
        confidence=match.confidence,
    )


def _family_from_subject_ref(subject_ref: str) -> str:
    family, _slug = subject_ref.split(":", 1)
    return family
