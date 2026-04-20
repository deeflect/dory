from __future__ import annotations

from dory_core.schema import (
    CANONICAL_FAMILIES,
    CANONICAL_FRONTMATTER_CORE_FIELDS,
    CANONICAL_SECTION_TEMPLATES,
    CORE_FILE_TEMPLATES,
    DOC_CLASS_TO_TYPE,
    DOC_CLASSES,
    DIGEST_FAMILIES,
    REFERENCE_FAMILIES,
    SESSION_LOGS_ROOT,
    SOURCE_FAMILIES,
    TYPE_VOCABULARY,
    WIKI_FAMILIES,
)


def test_schema_constants_match_target_schema() -> None:
    assert CANONICAL_FAMILIES == ("core", "people", "projects", "concepts", "decisions")
    assert SESSION_LOGS_ROOT == "logs/sessions"
    assert SOURCE_FAMILIES == ("imported", "web", "research", "legacy")
    assert DIGEST_FAMILIES == ("daily", "weekly")
    assert REFERENCE_FAMILIES == ("reports", "briefings", "slides", "notes")
    assert WIKI_FAMILIES == ("people", "projects", "concepts", "decisions", "indexes")


def test_schema_type_vocabulary_is_strict_and_complete() -> None:
    assert TYPE_VOCABULARY == (
        "core",
        "person",
        "project",
        "concept",
        "decision",
        "idea",
        "session",
        "source",
        "digest-daily",
        "digest-weekly",
        "report",
        "briefing",
        "slide",
        "note",
        "wiki",
    )


def test_schema_doc_class_mapping_covers_core_families() -> None:
    assert DOC_CLASSES[0] == "core_user"
    assert DOC_CLASS_TO_TYPE["person_profile"] == "person"
    assert DOC_CLASS_TO_TYPE["project_state"] == "project"
    assert DOC_CLASS_TO_TYPE["digest_weekly"] == "digest-weekly"


def test_schema_frontmatter_and_templates_cover_required_sections() -> None:
    assert CANONICAL_FRONTMATTER_CORE_FIELDS == (
        "title",
        "type",
        "slug",
        "domain",
        "aliases",
        "status",
        "created",
        "updated",
        "canonical",
        "source_kind",
        "confidence",
    )
    assert CANONICAL_SECTION_TEMPLATES["project"] == (
        "Summary",
        "Current State",
        "Goals",
        "Open Work",
        "Key Decisions",
        "Dependencies And Related Concepts",
        "Timeline",
        "Evidence",
    )
    assert CORE_FILE_TEMPLATES["user"] == (
        "Summary",
        "Current Facts",
        "Preferences And Working Style",
        "Goals And Priorities",
        "Constraints",
        "Timeline",
        "Evidence",
    )
