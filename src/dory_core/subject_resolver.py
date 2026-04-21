"""Semantic subject → canonical entity resolution.

Lifted out of `dory_core.semantic_write` so the resolver stack can be reused
without pulling in the full write engine. The public surface:

- `SubjectMatch` — structured resolution result
- `SubjectResolverLike` — protocol both resolvers satisfy
- `SubjectResolver` — filesystem-only deterministic resolver
- `RegistryBackedSubjectResolver` — registry + fallback + optional LLM resolver
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from dory_core.canonical_pages import infer_aliases_from_subject
from dory_core.entity_registry import EntityRegistry, RegistryMatch
from dory_core.frontmatter import load_markdown_document
from dory_core.llm.openrouter import OpenRouterClient
from dory_core.metadata import normalize_family_name
from dory_core.migration_normalize import normalize_migration_slug


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
class _SubjectEntry:
    subject_ref: str
    family: str
    title: str
    aliases: tuple[str, ...]
    target_path: str


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
