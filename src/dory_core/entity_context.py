"""Entity context packet for deterministic entity lookup before broad retrieval.

Provides the ``EntityContext`` data class and a pure resolver function that
uses only existing deterministic primitives (entity registry, subject resolver,
project helpers).  It never creates entities and returns ``None`` when
resolution is ambiguous or impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from dory_core.project_context import resolve_project_handle, resolve_project_path

if TYPE_CHECKING:
    from dory_core.entity_registry import EntityRegistry
    from dory_core.subject_resolver import SubjectResolverLike

_MatchedBy = Literal[
    "entity_id",
    "title",
    "alias",
    "subject_ref",
    "project_handle",
    "project_path",
]


@dataclass(frozen=True, slots=True)
class EntityContext:
    """Deterministic entity context packet.

    Produced before broad retrieval to scope searches to a known entity.
    Every field is computed from deterministic sources — no LLM, no graph,
    no side effects.

    Return ``None`` (unresolved) when the subject does not match exactly
    one known entity.
    """

    entity_id: str
    """Stable registry identifier, e.g. ``project:palace``."""

    canonical_name: str
    """Human-readable display name from the canonical source."""

    family: str
    """Entity family, e.g. ``project``, ``person``, ``concept``, ``decision``."""

    canonical_path: str | None
    """Relative path to the canonical markdown file, if one exists."""

    matched_by: str
    """How resolution succeeded: ``entity_id``, ``title``, ``alias``,
    ``subject_ref``, ``project_handle``, or ``project_path``."""

    source_refs: tuple[str, ...]
    """Exact markdown file paths that back this entity context.

    At minimum the canonical path.  May include alias sources or linked
    evidence paths in future iterations.
    """


def resolve_entity_context(
    subject: str,
    *,
    family: str | None = None,
    root: Path | None = None,
    registry: EntityRegistry | None = None,
    subject_resolver: SubjectResolverLike | None = None,
    project: str | None = None,
    cwd: str | None = None,
) -> EntityContext | None:
    """Resolve *subject* to an ``EntityContext`` using deterministic lookups.

    Resolution order:

    1. **Entity registry** — SQLite-backed deterministic lookup by alias,
       title, or entity_id (fastest, most reliable).
    2. **Subject resolver** — Filesystem-only deterministic resolver over
       canonical markdown files and frontmatter aliases.
    3. **Project helpers** — Path-based project detection when *family* is
       ``project`` (or *family* is ``None``).

    Returns ``None`` when the subject cannot be resolved deterministically
    to a single known entity.  Never creates entities.
    """
    cleaned = subject.strip()
    if not cleaned:
        return None

    # --- 1. Entity registry (SQLite-backed, deterministic) ---
    if registry is not None:
        registry_match = registry.resolve(cleaned, family=family)
        if registry_match is not None:
            source_refs: list[str] = []
            if registry_match.target_path:
                source_refs.append(registry_match.target_path)
            return EntityContext(
                entity_id=registry_match.entity_id,
                canonical_name=registry_match.title,
                family=registry_match.family,
                canonical_path=registry_match.target_path or None,
                matched_by=registry_match.matched_by,
                source_refs=tuple(source_refs),
            )

    # --- 2. Subject resolver (filesystem, deterministic) ---
    if subject_resolver is not None:
        subject_match = subject_resolver.resolve(cleaned, scope=family)
        if subject_match is not None:
            return EntityContext(
                entity_id=subject_match.subject_ref,
                canonical_name=subject_match.title,
                family=subject_match.family,
                canonical_path=subject_match.target_path or None,
                matched_by=subject_match.matched_by,
                source_refs=(subject_match.target_path,) if subject_match.target_path else (),
            )

    # --- 3. Project context helpers ---
    # Only attempt project resolution when the caller hints at a project,
    # or when no family filter is active (wide net).
    if family is None or family == "project":
        handle = resolve_project_handle(project=subject, cwd=cwd, root=root)
        if handle:
            project_path = resolve_project_path(root, handle)
            if project_path is not None:
                rel_path = project_path.relative_to(root).as_posix() if root else None
                return EntityContext(
                    entity_id=f"project:{handle}",
                    canonical_name=handle.replace("-", " ").title(),
                    family="project",
                    canonical_path=rel_path,
                    matched_by="project_handle",
                    source_refs=(rel_path,) if rel_path else (),
                )

    return None


def resolve_default_entity_context(
    *,
    project: str | None = None,
    cwd: str | None = None,
    root: Path | None = None,
    registry: EntityRegistry | None = None,
    subject_resolver: SubjectResolverLike | None = None,
) -> EntityContext | None:
    """Resolve a default entity context from the current *project* or *cwd*.

    This is a convenience wrapper that tries the explicit project first,
    then falls back to cwd-based inference.  Useful for the ``active-memory``
    entry point where the user may not name an explicit subject.
    """
    # Explicit project takes priority.
    explicit = (project or "").strip()
    if explicit:
        ctx = resolve_entity_context(
            explicit,
            family="project",
            root=root,
            registry=registry,
            subject_resolver=subject_resolver,
            project=explicit,
            cwd=cwd,
        )
        if ctx is not None:
            return ctx

    # Infer from cwd when no explicit project was given.
    if cwd:
        inferred = resolve_entity_context(
            cwd,
            family="project",
            root=root,
            registry=registry,
            subject_resolver=subject_resolver,
            project=None,
            cwd=cwd,
        )
        if inferred is not None:
            return inferred

    return None
