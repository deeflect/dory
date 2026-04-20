from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from dory_core.errors import DoryValidationError
from dory_core.slug import slugify_path_segment

VALID_DOC_TYPES = {
    "capture",
    "briefing",
    "concept",
    "core",
    "daily",
    "decision",
    "digest-daily",
    "digest-weekly",
    "idea",
    "knowledge",
    "note",
    "person",
    "project",
    "reference",
    "report",
    "session",
    "slide",
    "source",
    "weekly",
    "wiki",
}
VALID_STATUSES = {
    "active",
    "done",
    "paused",
    "pending",
    "raw",
    "superseded",
}
VALID_AREAS = {
    "coding",
    "writing",
    "marketing",
    "product",
    "design",
    "ops",
    "personal",
    "health",
    "finance",
    "relationships",
    "sales",
}
VALID_SOURCE_KINDS = {
    "canonical",
    "distilled",
    "extracted",
    "generated",
    "human",
    "imported",
    "legacy",
    "recall-promotion",
    "research",
    "semantic",
    "web",
}
VALID_TEMPERATURES = {"hot", "warm", "cold"}
VALID_VISIBILITIES = {"private", "internal", "public"}
VALID_SENSITIVITIES = {"personal", "financial", "legal", "contact", "credentials", "health", "none"}
DOC_TYPE_ALIASES = {
    "action-item": "capture",
    "analysis": "report",
    "briefing": "briefing",
    "concept": "concept",
    "content-idea": "idea",
    "daily-log": "daily",
    "digest": "digest-daily",
    "digest-daily": "digest-daily",
    "digest-weekly": "digest-weekly",
    "draft": "capture",
    "engagement-pattern": "knowledge",
    "idea": "idea",
    "inbox": "capture",
    "link": "reference",
    "note": "note",
    "overnight": "capture",
    "overnight-summary": "capture",
    "person": "person",
    "preference": "knowledge",
    "product": "project",
    "resource": "reference",
    "report": "report",
    "saved": "knowledge",
    "session-archive": "session",
    "session-log": "session",
    "slide": "slide",
    "source": "source",
    "strategic-idea": "idea",
    "strategy": "report",
    "task": "capture",
    "todo": "capture",
    "tool": "knowledge",
    "tweet-digest": "reference",
    "tweet-idea": "idea",
    "tweets": "reference",
    "wiki": "wiki",
}
GENERIC_DOC_TYPES = {"archive"}
STATUS_ALIASES = {
    "abandoned": "superseded",
    "actionable": "pending",
    "archive": "superseded",
    "archived": "superseded",
    "backlog": "pending",
    "complete": "done",
    "deployed": "done",
    "draft": "pending",
    "idea": "pending",
    "ideation": "pending",
    "in-progress": "active",
    "inbox": "raw",
    "parked": "paused",
    "planning": "pending",
    "ready-to-draft": "pending",
    "ready-to-execute": "pending",
    "ready-to-post": "pending",
    "ready-to-ship": "pending",
    "ready-to-test": "pending",
    "reference": "done",
    "research": "pending",
    "someday": "paused",
    "urgent": "active",
}
LEGACY_BUCKET_REDIRECTS = {
    "daily": Path("digests/daily"),
    "resources": Path("references/notes"),
    "sessions": Path("logs/sessions"),
    "weekly": Path("digests/weekly"),
}
TYPE_BUCKETS = {
    "capture": Path("inbox"),
    "briefing": Path("references/briefings"),
    "concept": Path("concepts"),
    "core": Path("core"),
    "daily": Path("logs/daily"),
    "decision": Path("decisions"),
    "digest-daily": Path("digests/daily"),
    "digest-weekly": Path("digests/weekly"),
    "idea": Path("ideas"),
    "knowledge": Path("knowledge"),
    "note": Path("references/notes"),
    "person": Path("people"),
    "project": Path("projects"),
    "reference": Path("references"),
    "report": Path("references/reports"),
    "session": Path("logs/sessions"),
    "slide": Path("references/slides"),
    "source": Path("sources/imported"),
    "weekly": Path("logs/weekly"),
    "wiki": Path("wiki"),
}


@dataclass(frozen=True, slots=True)
class MigrationResult:
    path: Path | None
    unresolved_reason: str | None = None


def normalize_frontmatter(
    frontmatter: dict[str, Any],
    *,
    target: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if "title" not in frontmatter or not str(frontmatter["title"]).strip():
        raise DoryValidationError("frontmatter missing required fields: title")
    if "type" not in frontmatter:
        raise DoryValidationError("frontmatter missing required fields: type")

    normalized = dict(frontmatter)
    doc_type = _resolve_doc_type(normalized, target=target)
    normalized["type"] = doc_type

    if "status" not in normalized:
        normalized["status"] = _default_status_for_type(doc_type)
    normalized["status"] = _normalize_status(normalized["status"])

    if "area" in normalized and normalized["area"] is not None:
        normalized["area"] = _normalize_enum_value("area", normalized["area"], VALID_AREAS)

    if "canonical" not in normalized:
        normalized["canonical"] = _default_canonical_for_type(doc_type, target=target)
    normalized["canonical"] = _normalize_bool("canonical", normalized["canonical"])

    if "source_kind" not in normalized:
        normalized["source_kind"] = _default_source_kind_for_type(doc_type, normalized)
    normalized["source_kind"] = _normalize_enum_value(
        "source_kind",
        normalized["source_kind"],
        VALID_SOURCE_KINDS,
    )

    if "temperature" not in normalized:
        normalized["temperature"] = _default_temperature_for_type(doc_type)
    normalized["temperature"] = _normalize_enum_value(
        "temperature",
        normalized["temperature"],
        VALID_TEMPERATURES,
    )

    if "visibility" in normalized and normalized["visibility"] is not None:
        normalized["visibility"] = _normalize_enum_value("visibility", normalized["visibility"], VALID_VISIBILITIES)
    if "sensitivity" in normalized and normalized["sensitivity"] is not None:
        normalized["sensitivity"] = _normalize_enum_value("sensitivity", normalized["sensitivity"], VALID_SENSITIVITIES)

    if "created" not in normalized:
        timestamp = now or datetime.now(tz=UTC)
        normalized["created"] = timestamp.date().isoformat()
    normalized["created"] = _normalize_date_field("created", normalized["created"])

    if "updated" in normalized and normalized["updated"] is not None:
        normalized["updated"] = _normalize_date_field("updated", normalized["updated"])
    if "date" in normalized and normalized["date"] is not None:
        normalized["date"] = _normalize_date_field("date", normalized["date"])

    return normalized


def resolve_write_target(
    raw_target: str,
    *,
    frontmatter: dict[str, Any],
) -> Path:
    requested = Path(raw_target)
    doc_type = normalize_doc_type(str(frontmatter["type"]))

    if raw_target.startswith("archive/"):
        raise DoryValidationError("new writes cannot target archive/; migrate into canonical buckets first")

    if raw_target.startswith("auto/") or len(requested.parts) == 1:
        return infer_target_from_frontmatter(frontmatter, requested.name if requested.name else None)

    first_part = requested.parts[0]
    if first_part in LEGACY_BUCKET_REDIRECTS:
        requested = LEGACY_BUCKET_REDIRECTS[first_part] / Path(*requested.parts[1:])

    _validate_target_matches_type(requested, doc_type)
    return requested


def infer_target_from_frontmatter(
    frontmatter: dict[str, Any],
    filename_hint: str | None = None,
) -> Path:
    if "title" not in frontmatter or not str(frontmatter["title"]).strip():
        raise DoryValidationError("frontmatter missing required fields: title")
    doc_type = normalize_doc_type(str(frontmatter["type"]))
    title = str(frontmatter["title"])
    slug = slugify_path_segment(Path(filename_hint).stem if filename_hint else title) or "note"
    bucket = TYPE_BUCKETS[doc_type]

    if doc_type == "core":
        raise DoryValidationError("type=core requires an explicit target under core/")
    if doc_type == "person":
        return bucket / f"{slug}.md"
    if doc_type == "project":
        return bucket / slug / "state.md"
    if doc_type == "concept":
        return bucket / f"{slug}.md"
    if doc_type == "decision":
        if _normalize_bool("canonical", frontmatter.get("canonical", True)):
            return bucket / f"{slug}.md"
        created = _extract_date_string(frontmatter, ("date", "created")) or date.today().isoformat()
        return bucket / f"{created}-{slug}.md"
    if doc_type == "daily":
        stamp = _extract_date_string(frontmatter, ("date", "created")) or slug
        return bucket / f"{stamp}.md"
    if doc_type == "digest-daily":
        stamp = _extract_date_string(frontmatter, ("date", "created")) or slug
        return bucket / f"{stamp}.md"
    if doc_type == "session":
        stamp = _extract_date_string(frontmatter, ("date", "created")) or slug
        return bucket / f"{stamp}.md"
    if doc_type == "weekly":
        stamp = _extract_date_string(frontmatter, ("date", "created")) or slug
        return bucket / f"{stamp}.md"
    if doc_type == "digest-weekly":
        stamp = _extract_date_string(frontmatter, ("date", "created")) or slug
        return bucket / f"{stamp}.md"
    if doc_type == "idea":
        return bucket / f"{slug}.md"
    return bucket / f"{slug}.md"


def plan_migration_path(current_path: Path, frontmatter: dict[str, Any]) -> MigrationResult:
    parts = current_path.parts
    if not parts:
        return MigrationResult(path=current_path)

    doc_type = _resolve_doc_type(frontmatter, target=current_path)
    first_part = parts[0]
    if first_part in {
        "core",
        "people",
        "projects",
        "concepts",
        "decisions",
        "knowledge",
        "inbox",
        "references",
        "logs",
        "digests",
        "sources",
        "wiki",
        "ideas",
    }:
        return MigrationResult(path=current_path)
    if first_part in LEGACY_BUCKET_REDIRECTS:
        return MigrationResult(path=LEGACY_BUCKET_REDIRECTS[first_part] / Path(*parts[1:]))
    if first_part != "archive":
        return MigrationResult(path=current_path)
    if len(parts) < 2:
        return MigrationResult(path=None, unresolved_reason="archive root cannot be migrated without a subtype")

    archive_kind = parts[1]
    tail = Path(*parts[2:]) if len(parts) > 2 else Path()
    if archive_kind == "daily":
        return MigrationResult(path=Path("logs/daily") / tail)
    if archive_kind == "sessions":
        return MigrationResult(path=Path("logs/sessions") / tail)
    if archive_kind == "weekly":
        return MigrationResult(path=Path("logs/weekly") / tail)
    if archive_kind == "resources":
        return MigrationResult(path=Path("references/notes") / tail)
    if archive_kind == "health-daily":
        return MigrationResult(path=Path("logs/daily") / "health-daily" / tail)
    if archive_kind == "knowledge":
        return MigrationResult(path=Path("concepts") / tail)
    if archive_kind == "projects":
        return MigrationResult(path=Path("projects") / tail)
    if doc_type in {
        "person",
        "project",
        "decision",
        "daily",
        "digest-daily",
        "session",
        "weekly",
        "digest-weekly",
        "concept",
        "report",
        "briefing",
        "slide",
        "note",
        "source",
    }:
        return MigrationResult(path=infer_target_from_frontmatter(frontmatter, current_path.name))

    inferred_bucket = TYPE_BUCKETS.get(doc_type)
    if inferred_bucket is None:
        return MigrationResult(
            path=None,
            unresolved_reason=f"unsupported legacy archive bucket: archive/{archive_kind}",
        )

    if tail.parts:
        return MigrationResult(path=inferred_bucket / archive_kind / tail)
    return MigrationResult(path=inferred_bucket / archive_kind / current_path.name)


def _sanitize_type_value(value: str) -> str:
    """Clean template-polluted frontmatter type values.

    Handles patterns like ``"product  # product, content, infra"``,
    ``"idea | project | knowledge"``, and ``"enum   — preference | memory"``
    that leak into the frontmatter when a template block is left unfilled.
    Takes the first meaningful segment before any comment, pipe, or em-dash
    marker, then lowercases and trims.
    """
    raw = value.strip()
    if not raw:
        return raw
    for marker in ("#", "|", "—"):
        position = raw.find(marker)
        if position > 0:
            raw = raw[:position].strip()
    return raw.strip().lower()


def normalize_doc_type(value: str) -> str:
    sanitized = _sanitize_type_value(value)
    normalized = DOC_TYPE_ALIASES.get(sanitized, sanitized)
    if normalized not in VALID_DOC_TYPES:
        raise DoryValidationError(f"invalid frontmatter type: {value}")
    return normalized


def normalize_family_name(name: str) -> str:
    """Canonicalize a family-like name to its singular doc type form.

    Recognizes the plural directory form (e.g. ``"people"`` -> ``"person"``)
    by inverting ``TYPE_BUCKETS``. Unknown values are returned lowercased.
    """
    lowered = name.strip().lower()
    if not lowered or lowered in TYPE_BUCKETS:
        return lowered
    for doc_type, bucket in TYPE_BUCKETS.items():
        if bucket.parts[:1] == (lowered,):
            return doc_type
    return lowered


def _resolve_doc_type(frontmatter: dict[str, Any], *, target: Path | None) -> str:
    if "type" not in frontmatter:
        raise DoryValidationError("frontmatter missing required fields: type")
    raw_value = str(frontmatter["type"]).strip().lower()
    if raw_value in GENERIC_DOC_TYPES and target is not None:
        inferred = _infer_doc_type_from_target(target)
        if inferred is not None:
            return inferred
    return normalize_doc_type(raw_value)


def _validate_target_matches_type(target: Path, doc_type: str) -> None:
    bucket = TYPE_BUCKETS[doc_type]
    if target.parts[: len(bucket.parts)] != bucket.parts:
        raise DoryValidationError(f"type={doc_type} must write under {bucket.as_posix()}/, got {target.as_posix()}")


def _default_status_for_type(doc_type: str) -> str:
    if doc_type in {"capture", "session"}:
        return "raw"
    if doc_type == "idea":
        return "pending"
    if doc_type in {"daily", "weekly", "digest-daily", "digest-weekly", "reference", "report", "briefing", "slide"}:
        return "done"
    return "active"


def _default_canonical_for_type(doc_type: str, *, target: Path | None) -> bool:
    if doc_type in {
        "reference",
        "report",
        "briefing",
        "slide",
        "note",
        "daily",
        "digest-daily",
        "session",
        "weekly",
        "digest-weekly",
        "capture",
        "source",
        "wiki",
        "idea",
    }:
        return False
    if doc_type == "project" and target is not None:
        return target.name == "state.md"
    return True


def _default_source_kind_for_type(doc_type: str, frontmatter: dict[str, Any]) -> str:
    if doc_type in {"core", "person", "project", "concept", "decision"}:
        return "canonical"
    sources = frontmatter.get("sources")
    has_sources = isinstance(sources, list) and bool(sources) or isinstance(sources, str) and bool(sources.strip())
    if has_sources:
        return "extracted"
    if doc_type in {"reference", "source"}:
        return "imported"
    if doc_type in {"report", "briefing", "slide", "note", "wiki"}:
        return "generated"
    return "human"


def _default_temperature_for_type(doc_type: str) -> str:
    if doc_type == "core":
        return "hot"
    if doc_type in {"decision", "knowledge", "concept", "person", "project", "idea"}:
        return "warm"
    return "cold"


def _normalize_enum_value(field: str, value: Any, allowed: set[str]) -> str:
    normalized = str(value).strip().lower()
    if normalized not in allowed:
        raise DoryValidationError(f"invalid {field}: {value}")
    return normalized


def _normalize_status(value: Any) -> str:
    normalized = str(value).strip().lower()
    normalized = STATUS_ALIASES.get(normalized, normalized)
    if normalized not in VALID_STATUSES:
        raise DoryValidationError(f"invalid status: {value}")
    return normalized


def _normalize_bool(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise DoryValidationError(f"invalid {field}: {value}")


def _normalize_date_field(field: str, value: Any) -> str:
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError as err:
        raise DoryValidationError(f"invalid {field}: {value}") from err


def _extract_date_string(frontmatter: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = frontmatter.get(key)
        if value is None:
            continue
        try:
            return _normalize_date_field(key, value)
        except DoryValidationError:
            continue
    return None


def _infer_doc_type_from_target(target: Path) -> str | None:
    parts = target.parts
    if not parts:
        return None
    if parts[:1] == ("digests",) and len(parts) > 1:
        if parts[1] == "daily":
            return "digest-daily"
        if parts[1] == "weekly":
            return "digest-weekly"
    if parts[:1] == ("sources",):
        return "source"
    if parts[:1] == ("concepts",):
        return "concept"
    if parts[:1] == ("ideas",):
        return "idea"
    if parts[:2] == ("references", "reports"):
        return "report"
    if parts[:2] == ("references", "briefings"):
        return "briefing"
    if parts[:2] == ("references", "slides"):
        return "slide"
    if parts[:2] == ("references", "notes"):
        return "note"
    if parts[:1] == ("wiki",):
        return "wiki"
    if parts[0] == "archive" and len(parts) > 1:
        archive_kind = parts[1]
        if archive_kind == "daily":
            return "daily"
        if archive_kind == "sessions":
            return "session"
        if archive_kind == "weekly":
            return "weekly"
        if archive_kind == "resources":
            return "reference"
        if archive_kind == "knowledge":
            return "knowledge"
        if archive_kind == "projects":
            return "project"
        return None
    if parts[0] in LEGACY_BUCKET_REDIRECTS:
        redirected = LEGACY_BUCKET_REDIRECTS[parts[0]]
        return _infer_doc_type_from_target(redirected)
    for doc_type, bucket in TYPE_BUCKETS.items():
        if parts[: len(bucket.parts)] == bucket.parts:
            return doc_type
    return None
