from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dory_core.canonical_pages import canonical_title_from_subject
from dory_core.slug import canonical_target_for_subject, normalize_migration_slug
from dory_core.subject_resolver import SubjectMatch, SubjectResolver, SubjectResolverLike
from dory_core.types import MemoryWriteAction, MemoryWriteKind, MemoryWriteReq

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
    agent: str | None
    session_id: str | None
    origin_surface: str | None
    matched_by: str
    target_exists: bool


def build_semantic_write_plan(
    root: Path,
    req: MemoryWriteReq,
    *,
    resolver: SubjectResolverLike | None = None,
) -> SemanticWritePlan:
    resolver = resolver or SubjectResolver(root)
    match = resolver.resolve(req.subject, scope=req.scope)
    if match is None or _should_create_new_explicit_dream_subject(match, req):
        match = _new_subject_match_from_explicit_scope(req)
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
        agent=req.agent,
        session_id=req.session_id,
        origin_surface=req.origin_surface,
        matched_by=match.matched_by,
        target_exists=(root / target_path).exists(),
    )


def is_canonical_semantic_target(plan: SemanticWritePlan) -> bool:
    if plan.family in {"core", "person", "project", "concept", "decision"}:
        return True
    return plan.target_path.startswith(("core/", "people/", "projects/", "concepts/", "decisions/"))


def should_rewrite_canonical_from_claims(plan: SemanticWritePlan) -> bool:
    return plan.family != "core" and plan.resolved_mode != "forget"


def semantic_write_preview_message(plan: SemanticWritePlan, *, action: str, evidence_path: str) -> str:
    prefix = ""
    if is_canonical_semantic_target(plan):
        prefix = (
            f"CANONICAL TARGET {plan.target_path}; preview only; "
            "use force_inbox=true for tentative notes or allow_canonical=true after review. "
        )
    return f"{prefix}dry_run: {action}; semantic evidence would be {evidence_path}"


def semantic_write_preview_payload(
    plan: SemanticWritePlan,
    *,
    action: str,
    evidence_path: str,
) -> dict[str, object]:
    return {
        "action": action,
        "subject": plan.subject,
        "subject_ref": plan.subject_ref,
        "target_subject_ref": plan.target_subject_ref,
        "target_path": plan.target_path,
        "family": plan.family,
        "kind": plan.kind,
        "resolved_mode": plan.resolved_mode,
        "matched_by": plan.matched_by,
        "match_confidence": plan.match_confidence,
        "evidence_path": evidence_path,
        "canonical_target": is_canonical_semantic_target(plan),
    }


def primary_section_for_plan(plan: SemanticWritePlan) -> str:
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


def family_from_subject_ref(subject_ref: str) -> str:
    family, _slug = subject_ref.split(":", 1)
    return family


def _new_subject_match_from_explicit_scope(req: MemoryWriteReq) -> SubjectMatch | None:
    if req.scope not in {"project", "concept", "decision"}:
        return None
    slug = normalize_migration_slug(req.subject)
    if not slug:
        return None
    subject_ref = f"{req.scope}:{slug}"
    return SubjectMatch(
        subject_ref=subject_ref,
        family=req.scope,
        title=canonical_title_from_subject(subject_ref),
        target_path=canonical_target_for_subject(subject_ref),
        matched_by="explicit_scope",
        confidence="high",
    )


def _should_create_new_explicit_dream_subject(match: SubjectMatch, req: MemoryWriteReq) -> bool:
    if req.scope not in {"project", "concept", "decision"}:
        return False
    source = req.source or ""
    if "/digests/" not in source and "/inbox/distilled/" not in source:
        return False
    slug = normalize_migration_slug(req.subject)
    if not slug:
        return False
    requested_subject_ref = f"{req.scope}:{slug}"
    if match.subject_ref == requested_subject_ref:
        return False
    return match.matched_by in {"alias", "llm"}


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


def _resolve_mode(action: MemoryWriteAction) -> ResolvedMode:
    if action == "replace":
        return "replace"
    if action == "forget":
        return "forget"
    return "append"
