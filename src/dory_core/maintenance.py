from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
import re
from typing import Any

from dory_core.claim_store import ClaimStore
from dory_core.frontmatter import load_markdown_document
from dory_core.fs import atomic_write_text
from dory_core.llm.openrouter import OpenRouterClient

_BACKFILL_BODY_LIMIT = 12_000

_IGNORED_WIKI_META_FILES = {"index.md", "hot.md", "log.md"}


_MAINTENANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "suggested_type": {"type": ["string", "null"]},
        "suggested_status": {"type": ["string", "null"]},
        "suggested_area": {"type": ["string", "null"]},
        "suggested_canonical": {"type": ["boolean", "null"]},
        "suggested_source_kind": {"type": ["string", "null"]},
        "suggested_temperature": {"type": ["string", "null"]},
        "suggested_target": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "suggested_type",
        "suggested_status",
        "suggested_area",
        "suggested_canonical",
        "suggested_source_kind",
        "suggested_temperature",
        "suggested_target",
        "rationale",
        "confidence",
    ],
}


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    path: str
    suggested_type: str | None
    suggested_status: str | None
    suggested_area: str | None
    suggested_canonical: bool | None
    suggested_source_kind: str | None
    suggested_temperature: str | None
    suggested_target: str | None
    rationale: str
    confidence: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class OpenRouterMaintenanceInspector:
    client: OpenRouterClient

    def inspect(self, path: str, markdown_text: str) -> MaintenanceReport:
        document = load_markdown_document(markdown_text)
        payload = self.client.generate_json(
            system_prompt=(
                "You inspect Dory memory docs and suggest metadata or placement cleanup. "
                "Do not invent body facts. "
                "Only infer type, status, area, canonicality, source kind, temperature, and target path from the current file path, frontmatter, and body."
            ),
            user_prompt=(
                f"Path: {path}\n"
                f"Frontmatter:\n{json.dumps(document.frontmatter, indent=2, sort_keys=True)}\n\n"
                f"Body:\n{document.body}"
            ),
            schema_name="maintenance_report",
            schema=_MAINTENANCE_SCHEMA,
        )
        return MaintenanceReport(
            path=path,
            suggested_type=_optional_string(payload.get("suggested_type")),
            suggested_status=_optional_string(payload.get("suggested_status")),
            suggested_area=_optional_string(payload.get("suggested_area")),
            suggested_canonical=_optional_bool(payload.get("suggested_canonical")),
            suggested_source_kind=_optional_string(payload.get("suggested_source_kind")),
            suggested_temperature=_optional_string(payload.get("suggested_temperature")),
            suggested_target=_optional_string(payload.get("suggested_target")),
            rationale=_optional_string(payload.get("rationale")) or "No rationale provided.",
            confidence=_coerce_confidence(payload.get("confidence")),
        )


class MaintenanceReportWriter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(self, report: MaintenanceReport) -> Path:
        path_obj = Path(report.path)
        target_name = "--".join(path_obj.with_suffix("").parts) or path_obj.stem
        target = self.root / "inbox" / "maintenance" / f"{target_name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, report.to_json(), encoding="utf-8")
        return target


class MemoryHealthDashboard:
    def __init__(self, root: Path, *, stale_after_days: int = 30) -> None:
        self.root = Path(root)
        self.stale_after_days = stale_after_days

    def inspect(self) -> dict[str, list[str]]:
        report: dict[str, list[str]] = {
            "stale_pages": [],
            "contradictions": [],
            "low_confidence": [],
            "open_questions": [],
            "missing_evidence": [],
            "missing_timeline": [],
            "event_mismatch": [],
            "state_conflict": [],
            "claim_mismatch": [],
            "claim_event_mismatch": [],
            "claim_evidence_mismatch": [],
            "missing_privacy_metadata": [],
        }

        wiki_roots = [root for root in self._wiki_roots() if root.exists()]
        claim_store = _load_claim_store(self.root)

        seen: set[str] = set()
        for wiki_root in wiki_roots:
            for path in sorted(wiki_root.rglob("*.md")):
                if path.name in _IGNORED_WIKI_META_FILES:
                    continue
                rel_path = path.relative_to(self.root).as_posix()
                if rel_path in seen:
                    continue
                seen.add(rel_path)

                text = path.read_text(encoding="utf-8")
                try:
                    document = load_markdown_document(text)
                except ValueError:
                    report["missing_evidence"].append(rel_path)
                    continue

                frontmatter = document.frontmatter
                if _is_stale(frontmatter, self.stale_after_days):
                    report["stale_pages"].append(rel_path)
                if _has_missing_evidence(document.body):
                    report["missing_evidence"].append(rel_path)
                if _has_missing_timeline(document.body):
                    report["missing_timeline"].append(rel_path)
                if _has_event_mismatch(document.body):
                    report["event_mismatch"].append(rel_path)
                if _has_state_conflict(document.body):
                    report["state_conflict"].append(rel_path)
                if claim_store is not None and _has_claim_mismatch(rel_path, document.body, claim_store):
                    report["claim_mismatch"].append(rel_path)
                if claim_store is not None and _has_claim_event_mismatch(rel_path, document.body, claim_store):
                    report["claim_event_mismatch"].append(rel_path)
                if claim_store is not None and _has_claim_evidence_mismatch(rel_path, document.body, claim_store):
                    report["claim_evidence_mismatch"].append(rel_path)
                if _needs_privacy_metadata(rel_path, frontmatter):
                    report["missing_privacy_metadata"].append(rel_path)
                if _has_meaningful_section_items(document.body, "Contradictions"):
                    report["contradictions"].append(rel_path)
                if _has_low_confidence_signal(document.body):
                    report["low_confidence"].append(rel_path)
                if _has_meaningful_section_items(document.body, "Open questions") or _has_meaningful_section_items(
                    document.body,
                    "Open Questions",
                ):
                    report["open_questions"].append(rel_path)

        report["missing_privacy_metadata"].extend(_privacy_metadata_paths(self.root, seen))
        return report

    def write_report(self) -> Path:
        report = self.inspect()
        target = self.root / "inbox" / "maintenance" / "wiki-health.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def _wiki_roots(self) -> tuple[Path, ...]:
        return (
            self.root / "wiki",
            self.root / "knowledge" / "wiki",
        )


@dataclass(frozen=True, slots=True)
class PrivacyMetadataBackfillChange:
    path: str
    visibility: str
    sensitivity: str
    reason: str


@dataclass(frozen=True, slots=True)
class PrivacyMetadataBackfillResult:
    dry_run: bool
    changed: list[PrivacyMetadataBackfillChange]
    skipped: list[str]
    errors: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        sensitivity_counts: dict[str, int] = {}
        for change in self.changed:
            sensitivity_counts[change.sensitivity] = sensitivity_counts.get(change.sensitivity, 0) + 1
        return {
            "dry_run": self.dry_run,
            "changed_count": len(self.changed),
            "skipped_count": len(self.skipped),
            "error_count": len(self.errors),
            "sensitivity_counts": dict(sorted(sensitivity_counts.items())),
            "changed": [asdict(change) for change in self.changed],
            "skipped": self.skipped,
            "errors": self.errors,
        }


class PrivacyMetadataBackfiller:
    """Add missing privacy metadata without changing document bodies."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def run(
        self,
        *,
        paths: list[str] | None = None,
        dry_run: bool = True,
        refresh: bool = False,
    ) -> PrivacyMetadataBackfillResult:
        requested_paths = paths or self._missing_privacy_paths(refresh=refresh)
        changed: list[PrivacyMetadataBackfillChange] = []
        skipped: list[str] = []
        errors: dict[str, str] = {}

        for rel_path in sorted(dict.fromkeys(requested_paths)):
            target = self.root / rel_path
            try:
                raw = target.read_text(encoding="utf-8")
                document = load_markdown_document(raw)
                patch = _privacy_metadata_patch(rel_path, document.frontmatter, document.body)
                if not patch:
                    skipped.append(rel_path)
                    continue
                rendered = _render_with_frontmatter_patch(raw, patch)
                load_markdown_document(rendered)
                changed.append(
                    PrivacyMetadataBackfillChange(
                        path=rel_path,
                        visibility=str(patch["visibility"]),
                        sensitivity=str(patch["sensitivity"]),
                        reason=str(patch["reason"]),
                    )
                )
                if not dry_run:
                    atomic_write_text(target, rendered, encoding="utf-8")
            except (OSError, ValueError) as err:
                errors[rel_path] = str(err)

        return PrivacyMetadataBackfillResult(
            dry_run=dry_run,
            changed=changed,
            skipped=skipped,
            errors=errors,
        )

    def _missing_privacy_paths(self, *, refresh: bool) -> list[str]:
        report_path = self.root / "inbox" / "maintenance" / "wiki-health.json"
        if not refresh and report_path.exists():
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            paths = payload.get("missing_privacy_metadata")
            if isinstance(paths, list):
                return [str(path) for path in paths if isinstance(path, str) and path.strip()]
        return MemoryHealthDashboard(self.root).inspect()["missing_privacy_metadata"]


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _coerce_confidence(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _is_stale(frontmatter: dict[str, object], stale_after_days: int) -> bool:
    status = str(frontmatter.get("status", "")).strip().lower()
    if status in {"stale", "superseded"}:
        return True

    updated = frontmatter.get("updated")
    if not isinstance(updated, str):
        return False

    try:
        updated_date = date.fromisoformat(updated[:10])
    except ValueError:
        return False

    age = (datetime.now(tz=UTC).date() - updated_date).days
    return age > stale_after_days


def _has_missing_evidence(body: str) -> bool:
    if not _has_claim_or_state_section(body):
        return True
    evidence_refs = [item for item in _extract_evidence_items(body) if _looks_like_evidence_ref(item)]
    return not evidence_refs


def _has_section_items(body: str, heading: str) -> bool:
    return bool(_extract_list_items(body, heading))


def _has_meaningful_section_items(body: str, heading: str) -> bool:
    return bool(_extract_meaningful_list_items(body, heading))


def _has_claim_or_state_section(body: str) -> bool:
    return any(
        _extract_list_items(body, heading) for heading in ("Key Claims", "Key claims", "Current Facts", "Current State")
    )


def _has_missing_timeline(body: str) -> bool:
    if not _has_claim_or_state_section(body):
        return False
    timeline_items = _extract_meaningful_list_items(body, "Timeline")
    return not timeline_items


def _has_event_mismatch(body: str) -> bool:
    timeline_events = set(_timeline_event_types(body))
    evidence_events = set(_evidence_event_groups(body))
    if not timeline_events and not evidence_events:
        return False
    if not evidence_events:
        return False
    return timeline_events != evidence_events


def _has_state_conflict(body: str) -> bool:
    current_items = _current_state_items(body)
    if not current_items:
        return False
    event_types = set(_timeline_event_types(body)) | set(_evidence_event_groups(body))
    if not event_types:
        return False
    return event_types.issubset({"retired", "invalidated", "replaced"})


def _has_claim_mismatch(rel_path: str, body: str, claim_store: ClaimStore) -> bool:
    entity_id = _entity_id_from_wiki_path(rel_path)
    if entity_id is None:
        return False
    current_items = _current_state_items(body)
    active_claims = claim_store.current_claims(entity_id)
    history = claim_store.claim_history(entity_id)
    if not current_items and not active_claims:
        return False
    if not active_claims and not history:
        return False
    if not active_claims:
        return True
    normalized_page = {_normalize_semantic_text(item) for item in current_items if _normalize_semantic_text(item)}
    normalized_claims = {
        _normalize_semantic_text(claim.statement)
        for claim in active_claims
        if _normalize_semantic_text(claim.statement)
    }
    return normalized_page != normalized_claims


def _has_claim_event_mismatch(rel_path: str, body: str, claim_store: ClaimStore) -> bool:
    entity_id = _entity_id_from_wiki_path(rel_path)
    if entity_id is None:
        return False
    claim_events = claim_store.claim_events(entity_id)
    if not claim_events:
        return False
    expected_events = {event.event_type for event in claim_events}
    page_events = set(_timeline_event_types(body)) | set(_evidence_event_groups(body))
    if not page_events:
        return True
    return page_events != expected_events


def _has_claim_evidence_mismatch(rel_path: str, body: str, claim_store: ClaimStore) -> bool:
    entity_id = _entity_id_from_wiki_path(rel_path)
    if entity_id is None:
        return False
    claim_events = claim_store.claim_events(entity_id)
    if not claim_events:
        return False
    expected_paths = {
        _normalize_evidence_path(event.evidence_path) for event in claim_events if event.evidence_path.strip()
    }
    expected_paths.discard("")
    if not expected_paths:
        return False
    page_paths = {_normalize_evidence_path(item) for item in _extract_evidence_items(body)}
    page_paths.discard("")
    if not page_paths:
        return True
    return not expected_paths.issubset(page_paths)


def _needs_privacy_metadata(rel_path: str, frontmatter: dict[str, object]) -> bool:
    if "visibility" in frontmatter and "sensitivity" in frontmatter:
        return False
    lowered_path = rel_path.lower()
    if lowered_path.startswith(("knowledge/personal", "people/")):
        return True
    status = str(frontmatter.get("status", "")).strip().lower()
    source_kind = str(frontmatter.get("source_kind", "")).strip().lower()
    doc_type = str(frontmatter.get("type", "")).strip().lower()
    return status == "raw" or (source_kind in {"imported", "legacy"} and doc_type in {"knowledge", "source", "capture"})


def _privacy_metadata_patch(rel_path: str, frontmatter: dict[str, object], body: str) -> dict[str, str]:
    if "visibility" in frontmatter and "sensitivity" in frontmatter:
        return {}
    sensitivity, visibility, reason = _infer_privacy_metadata(rel_path, frontmatter, body)
    return {
        "visibility": str(frontmatter.get("visibility") or visibility),
        "sensitivity": str(frontmatter.get("sensitivity") or sensitivity),
        "reason": reason,
    }


def _infer_privacy_metadata(
    rel_path: str,
    frontmatter: dict[str, object],
    body: str,
) -> tuple[str, str, str]:
    haystack = _privacy_haystack(rel_path, frontmatter, body)
    path = rel_path.lower()

    rules: tuple[tuple[str, tuple[str, ...], str], ...] = (
        (
            "credentials",
            (
                "api key",
                "auth token",
                "bearer token",
                "credential",
                "oauth",
                "password",
                "private key",
                "secret",
                "ssh key",
            ),
            "contains credentials/config/access-control signals",
        ),
        (
            "financial",
            ("burnrate", "crypto", "finance", "financial", "income", "money", "revenue", "runway", "wallet"),
            "contains financial/business-money signals",
        ),
        (
            "health",
            ("adhd", "fitness", "gym", "health", "medical", "routine", "sleep"),
            "contains health or wellbeing signals",
        ),
        (
            "legal",
            ("legal", "paperwork", "residency", "visa"),
            "contains legal/residency-status signals",
        ),
        (
            "contact",
            ("contact", "email", "phone", "telegram"),
            "contains contact or messaging signals",
        ),
    )
    for sensitivity, needles, reason in rules:
        if any(needle in haystack for needle in needles):
            return sensitivity, "private", reason

    if path.startswith(("knowledge/personal", "knowledge/personal-db", "people/", "logs/", "archive/sessions/")):
        return "personal", "private", "path is personal, people, log, or session memory"
    if path.startswith(("archive/daily/", "inbox/", "ideas/")):
        return "personal", "private", "path is raw/distilled personal working memory"
    return "none", "internal", "no specific sensitivity signal; internal visibility is conservative for imported/raw material"


def _privacy_haystack(rel_path: str, frontmatter: dict[str, object], body: str) -> str:
    fragments = [
        rel_path,
        json.dumps(frontmatter, sort_keys=True, ensure_ascii=True),
        body[:_BACKFILL_BODY_LIMIT],
    ]
    return "\n".join(fragments).lower()


def _render_with_frontmatter_patch(raw: str, patch: dict[str, str]) -> str:
    lines = raw.splitlines(keepends=True)
    closing_index = _frontmatter_closing_index(lines)
    if closing_index is None:
        raise ValueError("markdown document has an unterminated frontmatter block")

    existing_keys = _frontmatter_keys(lines[1:closing_index])
    insertions: list[str] = []
    for key in ("visibility", "sensitivity"):
        if key not in existing_keys and key in patch:
            insertions.append(f"{key}: {patch[key]}\n")
    if not insertions:
        return raw
    return "".join(lines[:closing_index] + insertions + lines[closing_index:])


def _frontmatter_closing_index(lines: list[str]) -> int | None:
    if not lines or lines[0].strip() != "---":
        raise ValueError("markdown document is missing frontmatter")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index
    return None


def _frontmatter_keys(lines: list[str]) -> set[str]:
    keys: set[str] = set()
    for line in lines:
        if line[:1].isspace() or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def _privacy_metadata_paths(root: Path, already_seen: set[str]) -> list[str]:
    paths: list[str] = []
    for path in sorted(root.rglob("*.md")):
        rel_path = path.relative_to(root).as_posix()
        if rel_path in already_seen or rel_path.startswith((".git/", ".dory/")):
            continue
        try:
            document = load_markdown_document(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _needs_privacy_metadata(rel_path, document.frontmatter):
            paths.append(rel_path)
    return paths


def _extract_section(body: str, heading: str) -> str:
    candidates = (
        f"## {heading}",
        f"## {heading.lower()}",
        f"## {heading.title()}",
    )
    for marker in candidates:
        match = re.search(rf"(?m)^{re.escape(marker)}\s*$", body)
        if match is None:
            continue
        section = body[match.end() :]
        next_heading = re.search(r"(?m)^##\s+", section)
        if next_heading is not None:
            section = section[: next_heading.start()]
        return section
    return ""


def _extract_list_items(body: str, heading: str) -> tuple[str, ...]:
    section = _extract_section(body, heading)
    if not section:
        return ()
    items: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or line == "- None":
            continue
        if line.startswith("- "):
            items.append(line[2:].strip())
    return tuple(item for item in items if item)


def _extract_evidence_items(body: str) -> tuple[str, ...]:
    section = _extract_section(body, "Evidence")
    if not section:
        return ()
    items: list[str] = []
    for raw_line in section.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped == "- None":
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return tuple(item for item in items if item)


def _extract_meaningful_list_items(body: str, heading: str) -> tuple[str, ...]:
    return tuple(item for item in _extract_list_items(body, heading) if not _looks_like_placeholder_item(item))


def _current_state_items(body: str) -> tuple[str, ...]:
    for heading in ("Current State", "Current Facts", "Key Claims", "Key claims"):
        items = _extract_meaningful_list_items(body, heading)
        if items:
            return items
    return ()


def _load_claim_store(root: Path) -> ClaimStore | None:
    claim_store_path = root / ".dory" / "claim-store.db"
    if not claim_store_path.exists():
        return None
    return ClaimStore(claim_store_path)


def _entity_id_from_wiki_path(rel_path: str) -> str | None:
    path = Path(rel_path)
    parts = path.parts
    if len(parts) < 3 or parts[0] not in {"wiki", "knowledge"}:
        return None
    if parts[0] == "knowledge" and len(parts) >= 4 and parts[1] == "wiki":
        family = parts[2]
        slug = path.stem
    else:
        family = parts[1]
        slug = path.stem
    mapping = {
        "people": "person",
        "projects": "project",
        "concepts": "concept",
        "decisions": "decision",
    }
    entity_family = mapping.get(family)
    if entity_family is None or slug in _IGNORED_WIKI_META_FILES or slug == "index":
        return None
    return f"{entity_family}:{slug}"


def _normalize_semantic_text(text: str) -> str:
    stripped = re.sub(r"\s+\[(active|retired|superseded|invalidated),[^\]]+\]\s*$", "", text.strip(), flags=re.I)
    return " ".join(stripped.lower().split())


def _timeline_event_types(body: str) -> tuple[str, ...]:
    items = _extract_meaningful_list_items(body, "Timeline")
    events: list[str] = []
    for item in items:
        lowered = item.lower()
        if "retired:" in lowered:
            events.append("retired")
        elif "replaced:" in lowered:
            events.append("replaced")
        elif "invalidated:" in lowered:
            events.append("invalidated")
        else:
            events.append("added")
    return tuple(events)


def _evidence_event_groups(body: str) -> tuple[str, ...]:
    section = _extract_section(body, "Evidence")
    if not section:
        return ()
    events: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("### "):
            continue
        label = line[4:].strip().lower()
        if label in {"added", "replaced", "retired", "invalidated"}:
            events.append(label)
    return tuple(events)


def _looks_like_evidence_ref(item: str) -> bool:
    normalized = item.strip()
    lowered = normalized.lower()
    if not normalized:
        return False
    if "derived from claim store" in lowered:
        return False
    if normalized.startswith("[[") and "|" in normalized:
        return True
    if ".md" in lowered:
        return True
    if "/" in normalized and " " not in normalized:
        return True
    return False


def _normalize_evidence_path(item: str) -> str:
    normalized = item.strip()
    if not normalized:
        return ""
    if normalized.startswith("[[") and "]]" in normalized:
        inner = normalized[2:].split("]]", 1)[0]
        normalized = inner.split("|", 1)[0].strip()
    if normalized.startswith("- "):
        normalized = normalized[2:].strip()
    if " - " in normalized:
        maybe_path = normalized.split(" - ", 1)[0].strip()
        if ".md" in maybe_path.lower() or "/" in maybe_path:
            normalized = maybe_path
    if ":" in normalized and normalized.count("/") >= 1 and not normalized.startswith("sources/"):
        prefix, suffix = normalized.split(":", 1)
        if "/" in prefix and ".md" in prefix.lower():
            normalized = prefix.strip()
        else:
            normalized = suffix.strip()
    if " (" in normalized and normalized.lower().endswith(")"):
        normalized = normalized.rsplit(" (", 1)[0].strip()
    return normalized


def _looks_like_placeholder_item(item: str) -> bool:
    lowered = item.strip().lower().rstrip(".")
    if not lowered:
        return True
    if lowered in {"none", "n/a", "unknown"}:
        return True
    if lowered.startswith("no contradiction"):
        return True
    if lowered.startswith("no open question"):
        return True
    if lowered.startswith("no contradictions"):
        return True
    return False


def _has_low_confidence_signal(body: str) -> bool:
    lowered = body.lower()
    return "[low" in lowered or " low," in lowered or " low]" in lowered or "low confidence" in lowered
