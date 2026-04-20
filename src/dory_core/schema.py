from __future__ import annotations

from typing import Final

CANONICAL_FAMILIES: Final[tuple[str, ...]] = (
    "core",
    "people",
    "projects",
    "concepts",
    "decisions",
)

SESSION_LOGS_ROOT: Final[str] = "logs/sessions"

SOURCE_FAMILIES: Final[tuple[str, ...]] = (
    "imported",
    "web",
    "research",
    "legacy",
)

DIGEST_FAMILIES: Final[tuple[str, ...]] = (
    "daily",
    "weekly",
)

REFERENCE_FAMILIES: Final[tuple[str, ...]] = (
    "reports",
    "briefings",
    "slides",
    "notes",
)

WIKI_FAMILIES: Final[tuple[str, ...]] = (
    "people",
    "projects",
    "concepts",
    "decisions",
    "indexes",
)

TYPE_VOCABULARY: Final[tuple[str, ...]] = (
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

DOC_CLASSES: Final[tuple[str, ...]] = (
    "core_user",
    "core_identity",
    "core_soul",
    "core_env",
    "core_active",
    "core_defaults",
    "person_profile",
    "project_state",
    "project_spec",
    "concept_note",
    "decision_record",
    "session_log",
    "source_web",
    "source_research",
    "source_imported",
    "source_legacy",
    "digest_daily",
    "digest_weekly",
    "reference_report",
    "reference_briefing",
    "reference_slide",
    "reference_note",
    "idea_note",
    "draft_note",
    "inbox_capture",
    "quarantine_case",
    "index_note",
    "migration_note",
    "misc_operational",
)

CANONICAL_FRONTMATTER_CORE_FIELDS: Final[tuple[str, ...]] = (
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

CANONICAL_SECTION_TEMPLATES: Final[dict[str, tuple[str, ...]]] = {
    "person": (
        "Summary",
        "Current Facts",
        "Preferences And Working Style",
        "Goals And Priorities",
        "Related Decisions And Projects",
        "Timeline",
        "Evidence",
    ),
    "project": (
        "Summary",
        "Current State",
        "Goals",
        "Open Work",
        "Key Decisions",
        "Dependencies And Related Concepts",
        "Timeline",
        "Evidence",
    ),
    "concept": (
        "Summary",
        "Definition",
        "Key Claims",
        "Current Understanding",
        "Related People Projects And Decisions",
        "Open Questions",
        "Timeline",
        "Evidence",
    ),
    "decision": (
        "Decision",
        "Status",
        "Context",
        "Rationale",
        "Alternatives Considered",
        "Consequences",
        "Related Projects And Concepts",
        "Timeline",
        "Evidence",
    ),
}

CORE_FILE_TEMPLATES: Final[dict[str, tuple[str, ...]]] = {
    "user": (
        "Summary",
        "Current Facts",
        "Preferences And Working Style",
        "Goals And Priorities",
        "Constraints",
        "Timeline",
        "Evidence",
    ),
    "identity": (
        "Agent Identity",
        "Role",
        "Channels And Surfaces",
        "Boundaries",
        "Timeline",
        "Evidence",
    ),
    "soul": (
        "Voice",
        "Behavior Rules",
        "Banned Patterns",
        "Interaction Principles",
        "Timeline",
        "Evidence",
    ),
    "env": (
        "Environment",
        "Machines And Paths",
        "Secrets And Auth Notes",
        "Services",
        "Timeline",
        "Evidence",
    ),
    "active": (
        "Current Focus",
        "Top Priorities",
        "Current Risks",
        "Open Loops",
        "Recent Changes",
        "Timeline",
        "Evidence",
    ),
    "defaults": (
        "Default Models",
        "Default Tools",
        "Default Operating Assumptions",
        "Fallback Rules",
        "Timeline",
        "Evidence",
    ),
}

TIMELINE_MARKER: Final[str] = "<!-- TIMELINE: append-only below this line -->"

DOC_CLASS_TO_TYPE: Final[dict[str, str]] = {
    "core_user": "core",
    "core_identity": "core",
    "core_soul": "core",
    "core_env": "core",
    "core_active": "core",
    "core_defaults": "core",
    "person_profile": "person",
    "project_state": "project",
    "project_spec": "project",
    "concept_note": "concept",
    "decision_record": "decision",
    "session_log": "session",
    "source_web": "source",
    "source_research": "source",
    "source_imported": "source",
    "source_legacy": "source",
    "digest_daily": "digest-daily",
    "digest_weekly": "digest-weekly",
    "reference_report": "report",
    "reference_briefing": "briefing",
    "reference_slide": "slide",
    "reference_note": "note",
    "idea_note": "idea",
    "draft_note": "note",
    "inbox_capture": "note",
    "quarantine_case": "note",
    "index_note": "wiki",
    "migration_note": "note",
    "misc_operational": "note",
}
