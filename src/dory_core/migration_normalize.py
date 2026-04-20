from __future__ import annotations

import re
from pathlib import Path

from dory_core.canonical_pages import render_canonical_markdown, render_core_markdown
from dory_core.migration_types import ClassifiedDocument
from dory_core.slug import slugify_path_segment

_HYPHENS = re.compile(r"-{2,}")

_SUBJECT_FAMILY_TARGETS: dict[str, str] = {
    "core": "core/{slug}.md",
    "decision": "decisions/{slug}.md",
    "decisions": "decisions/{slug}.md",
    "person": "people/{slug}.md",
    "people": "people/{slug}.md",
    "project": "projects/{slug}/state.md",
    "projects": "projects/{slug}/state.md",
    "concept": "concepts/{slug}.md",
    "concepts": "concepts/{slug}.md",
}


def normalize_migration_slug(value: str) -> str:
    slug = slugify_path_segment(value).replace("/", "-").replace("_", "-")
    return _HYPHENS.sub("-", slug).strip("-")


def canonical_target_for_subject(subject_ref: str) -> str:
    if ":" not in subject_ref:
        raise ValueError(f"invalid subject ref: {subject_ref}")

    family, raw_slug = subject_ref.split(":", 1)
    template = _SUBJECT_FAMILY_TARGETS.get(family)
    if template is None:
        raise ValueError(f"unsupported subject ref family: {family}")
    slug = normalize_migration_slug(raw_slug)
    if not slug:
        raise ValueError(f"empty subject slug: {subject_ref}")
    return template.format(slug=slug)


def concept_kind_for_legacy_path(path: str) -> str:
    if "/tools/" in path:
        return "tool"
    if "/health/" in path:
        return "health"
    return "general"


def normalize_classification_target(classified: ClassifiedDocument) -> ClassifiedDocument:
    normalized_target = _normalized_target_path(classified)
    if normalized_target == classified.target_path:
        return classified
    return ClassifiedDocument(
        doc_class=classified.doc_class,
        canonicality=classified.canonicality,
        target_path=normalized_target,
        domain=classified.domain,
        entity_refs=classified.entity_refs,
        decision_refs=classified.decision_refs,
        time_scope=classified.time_scope,
        confidence=classified.confidence,
        action=classified.action,
        reason=classified.reason,
    )


def render_canonical_template(
    *,
    family: str,
    title: str,
    slug: str,
    domain: str,
    aliases: tuple[str, ...] = (),
) -> str:
    return render_canonical_markdown(
        family=family,
        title=title,
        slug=slug,
        domain=domain,
        aliases=aliases,
    )


def render_core_template(
    *,
    file_name: str,
    title: str,
    domain: str = "mixed",
    aliases: tuple[str, ...] = (),
) -> str:
    return render_core_markdown(
        file_name=file_name,
        title=title,
        domain=domain,
        aliases=aliases,
    )


def _normalized_target_path(classified: ClassifiedDocument) -> str:
    doc_class = classified.doc_class
    if doc_class.startswith("core_"):
        return f"core/{doc_class.removeprefix('core_')}.md"

    if doc_class == "person_profile" and classified.canonicality == "canonical":
        return canonical_target_for_subject(_subject_ref_for_family("person", classified))
    if doc_class == "project_state" and classified.canonicality == "canonical":
        return canonical_target_for_subject(_subject_ref_for_family("project", classified))
    if doc_class == "concept_note" and classified.canonicality == "canonical":
        return canonical_target_for_subject(_subject_ref_for_family("concept", classified))
    if doc_class == "decision_record" and classified.canonicality == "canonical":
        return canonical_target_for_subject(_subject_ref_for_family("decision", classified))

    if doc_class == "session_log":
        return _normalized_bucket_path(classified.target_path, expected_root=("logs", "sessions"))
    if doc_class == "digest_daily":
        return _normalized_bucket_path(classified.target_path, expected_root=("digests", "daily"))
    if doc_class == "digest_weekly":
        return _normalized_bucket_path(classified.target_path, expected_root=("digests", "weekly"))
    if doc_class == "source_web":
        return _normalized_bucket_path(classified.target_path, expected_root=("sources", "web"))
    if doc_class == "source_research":
        return _normalized_bucket_path(classified.target_path, expected_root=("sources", "research"))
    if doc_class == "source_imported":
        return _normalized_bucket_path(classified.target_path, expected_root=("sources", "imported"))
    if doc_class == "source_legacy":
        return _normalized_bucket_path(classified.target_path, expected_root=("sources", "legacy"))
    if doc_class == "reference_report":
        return _normalized_bucket_path(classified.target_path, expected_root=("references", "reports"))
    if doc_class == "reference_briefing":
        return _normalized_bucket_path(classified.target_path, expected_root=("references", "briefings"))
    if doc_class == "reference_slide":
        return _normalized_bucket_path(classified.target_path, expected_root=("references", "slides"))
    if doc_class == "reference_note":
        return _normalized_bucket_path(classified.target_path, expected_root=("references", "notes"))
    if doc_class in {
        "idea_note",
        "draft_note",
        "inbox_capture",
        "quarantine_case",
        "migration_note",
        "misc_operational",
    }:
        return _normalized_bucket_path(classified.target_path, expected_root=("inbox",))
    return classified.target_path


def _subject_ref_for_family(family: str, classified: ClassifiedDocument) -> str:
    for ref in classified.entity_refs:
        if ref.split(":", 1)[0].strip().lower() == family:
            return ref
    slug = _slug_from_target_path(classified.target_path)
    return f"{family}:{slug or 'unknown'}"


def _slug_from_target_path(target_path: str) -> str:
    target = Path(target_path)
    if target.name == "state.md":
        return normalize_migration_slug(target.parent.name)
    return normalize_migration_slug(target.stem)


def _normalized_file_name(target_path: str) -> str:
    target = Path(target_path)
    stem = normalize_migration_slug(target.stem) or "untitled"
    suffix = target.suffix or ".md"
    return f"{stem}{suffix}"


def _normalized_bucket_path(target_path: str, *, expected_root: tuple[str, ...]) -> str:
    target = Path(target_path)
    parts = target.parts
    if parts[: len(expected_root)] == expected_root:
        tail = Path(*parts[len(expected_root) :]) if len(parts) > len(expected_root) else Path(target.name)
    else:
        tail = Path(target.name)
    normalized_tail = _normalized_tail_path(tail)
    return (Path(*expected_root) / normalized_tail).as_posix()


def _normalized_tail_path(path: Path) -> Path:
    parent = path.parent if path.parent != Path(".") else Path()
    return parent / _normalized_output_name(path)


def _normalized_output_name(path: Path) -> str:
    if path.name.lower().endswith(".md") and len(path.suffixes) > 1:
        base = path.name[:-3]
        segments = [normalize_migration_slug(segment) or "untitled" for segment in base.split(".") if segment.strip()]
        normalized_base = ".".join(segments) or "untitled"
        return f"{normalized_base}.md"
    return _normalized_file_name(path.as_posix())
