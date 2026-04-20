from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from dory_core.frontmatter import dump_markdown_document, load_markdown_document
from dory_core.schema import TIMELINE_MARKER

_TIMELINE_ENTRY_PREFIX = "- "
_TIMELINE_DATE_RE = r"\d{4}-\d{2}-\d{2}:"


@dataclass(frozen=True, slots=True)
class TimelineMigrationResult:
    target_path: str
    changed: bool
    report_reason: str | None = None
    rendered: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineMigrationRun:
    changed_paths: tuple[str, ...]
    unchanged_paths: tuple[str, ...]
    reports: tuple[dict[str, str], ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def migrate_document(path: Path, text: str, *, sidecar_text: str | None = None) -> TimelineMigrationResult:
    doc = load_markdown_document(text)
    if TIMELINE_MARKER in doc.body:
        return TimelineMigrationResult(target_path=str(path), changed=False)

    if not _is_supported_target(path):
        return TimelineMigrationResult(target_path=str(path), changed=False, report_reason="unsupported target")

    compiled_lines, timeline_lines = _split_existing_timeline_lines(doc.body)
    if sidecar_text:
        timeline_lines.extend(_build_sidecar_references(path, sidecar_text))

    updated_frontmatter = dict(doc.frontmatter)
    updated_frontmatter["has_timeline"] = True
    rendered = dump_markdown_document(
        updated_frontmatter,
        _render_timeline_body("\n".join(compiled_lines).strip(), timeline_lines),
    )
    return TimelineMigrationResult(target_path=str(path), changed=rendered != text, rendered=rendered)


def migrate_corpus(root: Path, *, write: bool = False) -> TimelineMigrationRun:
    root = Path(root)
    changed_paths: list[str] = []
    unchanged_paths: list[str] = []
    reports: list[dict[str, str]] = []
    for target in _candidate_paths(root):
        sidecar_text = None
        sidecar_path = _sidecar_for_target(target)
        if sidecar_path is not None and sidecar_path.exists():
            sidecar_text = sidecar_path.read_text(encoding="utf-8")
        result = migrate_document(
            target.relative_to(root),
            target.read_text(encoding="utf-8"),
            sidecar_text=sidecar_text,
        )
        if result.report_reason is not None:
            unchanged_paths.append(result.target_path)
            reports.append({"path": result.target_path, "reason": result.report_reason})
            continue
        if result.changed and result.rendered is not None:
            changed_paths.append(result.target_path)
            if write:
                target.write_text(result.rendered, encoding="utf-8")
        else:
            unchanged_paths.append(result.target_path)
    return TimelineMigrationRun(
        changed_paths=tuple(changed_paths),
        unchanged_paths=tuple(unchanged_paths),
        reports=tuple(reports),
    )


def _candidate_paths(root: Path) -> list[Path]:
    candidates = list((root / "projects").glob("*/state.md"))
    candidates.extend((root / "people").glob("*.md"))
    candidates.extend((root / "decisions" / "canonical").glob("*.md"))
    return sorted(path for path in candidates if path.exists())


def _is_supported_target(path: Path) -> bool:
    parts = path.parts
    if parts[:1] == ("people",) and len(parts) == 2:
        return True
    if parts[:1] == ("projects",) and len(parts) == 3 and parts[2] == "state.md":
        return True
    if parts[:2] == ("decisions", "canonical") and path.suffix == ".md":
        return True
    return False


def _split_existing_timeline_lines(body: str) -> tuple[list[str], list[str]]:
    compiled_lines: list[str] = []
    timeline_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if line.startswith("- ") and _looks_like_timeline_line(line):
            timeline_lines.append(line)
        else:
            compiled_lines.append(line)
    return _trim_blank_edges(compiled_lines), timeline_lines


def _build_sidecar_references(target: Path, sidecar_text: str) -> list[str]:
    sidecar_doc = load_markdown_document(sidecar_text)
    date_hint = str(sidecar_doc.frontmatter.get("date") or sidecar_doc.frontmatter.get("created") or "undated")[:10]
    if len(date_hint) != 10 or date_hint.count("-") != 2:
        date_hint = "undated"
    reference_path = target.parent / "notes-from-daily-digests.md"
    return [f"- {date_hint}: See {reference_path.as_posix()} for imported supporting evidence."]


def _render_timeline_body(compiled_truth: str, timeline_lines: list[str]) -> str:
    sections: list[str] = []
    if compiled_truth.strip():
        sections.append(compiled_truth.strip())
    sections.append(TIMELINE_MARKER)
    if timeline_lines:
        sections.append("\n".join(_dedupe_preserve_order(timeline_lines)))
    return "\n\n".join(sections).rstrip() + "\n"


def _looks_like_timeline_line(line: str) -> bool:
    if not line.startswith(_TIMELINE_ENTRY_PREFIX):
        return False
    payload = line[2:]
    return len(payload) >= 11 and payload[4] == "-" and payload[7] == "-" and payload[10] == ":"


def _trim_blank_edges(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _sidecar_for_target(path: Path) -> Path | None:
    if path.parts[:1] == ("projects",):
        return path.parent / "notes-from-daily-digests.md"
    return None
