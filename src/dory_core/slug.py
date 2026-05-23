from __future__ import annotations

import re
import unicodedata

_INVALID_CHARS = re.compile(r"[^a-z0-9_/-]+")
_WHITESPACE = re.compile(r"\s+")
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


def slugify_path_segment(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()
    hyphenated = _WHITESPACE.sub("-", ascii_only)
    cleaned = _INVALID_CHARS.sub("", hyphenated)
    collapsed = _HYPHENS.sub("-", cleaned)
    return collapsed.strip("-")


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


__all__ = [
    "slugify_path_segment",
    "normalize_migration_slug",
    "canonical_target_for_subject",
]
