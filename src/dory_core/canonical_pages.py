from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from dory_core.claim_store import ClaimEvent, ClaimRecord
from dory_core.frontmatter import dump_markdown_document, load_markdown_document
from dory_core.schema import CANONICAL_SECTION_TEMPLATES, CORE_FILE_TEMPLATES, TIMELINE_MARKER
from dory_core.slug import slugify_path_segment


@dataclass(frozen=True, slots=True)
class CanonicalUpdate:
    title: str
    body: str
    aliases: tuple[str, ...]


def render_canonical_from_claims(
    *,
    family: str,
    title: str,
    entity_id: str,
    claims: Sequence[ClaimRecord],
    history: Sequence[ClaimRecord],
    events: Sequence[ClaimEvent] | None = None,
    aliases: Sequence[str] = (),
) -> CanonicalUpdate:
    slug = entity_id.split(":", 1)[1]
    active_claims = tuple(claim for claim in claims if claim.status == "active")
    timeline_events = tuple(events) if events is not None else _events_from_history(history)
    claims_by_id = {claim.claim_id: claim for claim in history}
    timeline_entries = tuple(
        _dedupe_strings(
            build_timeline_entry(
                time_ref=_event_time_ref(event),
                summary=summary,
                evidence_path=event.evidence_path,
            )
            for event in timeline_events
            if (summary := _event_summary(event, claims_by_id))
        )
    )
    evidence_paths = tuple(
        _dedupe_strings(event.evidence_path for event in timeline_events if event.evidence_path.strip())
    )
    section_updates = _section_updates_from_claims(family=family, claims=active_claims)
    return patch_canonical_markdown(
        None,
        family=family,
        title=title,
        slug=slug,
        domain="mixed",
        aliases=aliases,
        section_updates=section_updates,
        timeline_entries=timeline_entries,
        evidence_paths=evidence_paths,
    )


def render_retired_canonical_from_claims(
    *,
    family: str,
    title: str,
    entity_id: str,
    history: Sequence[ClaimRecord],
    events: Sequence[ClaimEvent] | None = None,
    aliases: Sequence[str] = (),
    retirement_reason: str | None = None,
) -> CanonicalUpdate:
    update = render_canonical_from_claims(
        family=family,
        title=title,
        entity_id=entity_id,
        claims=(),
        history=history,
        events=events,
        aliases=aliases,
    )
    document = load_markdown_document(update.body)
    frontmatter = dict(document.frontmatter)
    frontmatter["status"] = "superseded"
    frontmatter["canonical"] = False
    frontmatter["source_kind"] = "generated"
    frontmatter["temperature"] = "cold"

    retired_claims = tuple(claim for claim in history if claim.statement.strip())
    section_updates = _section_updates_from_claims(family=family, claims=retired_claims)
    existing_summary = section_updates.get("Summary", "").strip()
    summary_parts = []
    if retirement_reason:
        summary_parts.append(f"Retired: {retirement_reason.strip()}")
    if existing_summary:
        summary_parts.append(existing_summary)
    if summary_parts:
        section_updates["Summary"] = "\n\n".join(summary_parts)

    body = document.body
    for section, replacement in section_updates.items():
        body = replace_section(body, section, replacement)

    return CanonicalUpdate(
        title=title,
        body=dump_markdown_document(frontmatter, body),
        aliases=update.aliases,
    )


def render_canonical_markdown(
    *,
    family: str,
    title: str,
    slug: str,
    domain: str,
    aliases: Sequence[str] = (),
) -> str:
    section_names = CANONICAL_SECTION_TEMPLATES[family]
    frontmatter = _canonical_frontmatter(
        title=title,
        doc_type=family,
        slug=slug,
        domain=domain,
        aliases=aliases,
    )
    body = _render_canonical_body(section_names)
    return dump_markdown_document(frontmatter, body)


def render_core_markdown(
    *,
    file_name: str,
    title: str,
    domain: str = "mixed",
    aliases: Sequence[str] = (),
) -> str:
    stem = Path(file_name).stem
    section_names = CORE_FILE_TEMPLATES[stem]
    frontmatter = _canonical_frontmatter(
        title=title,
        doc_type="core",
        slug=stem,
        domain=domain,
        aliases=aliases,
    )
    body = _render_canonical_body(section_names)
    return dump_markdown_document(frontmatter, body)


def patch_canonical_markdown(
    current_markdown: str | None,
    *,
    family: str,
    title: str,
    slug: str,
    domain: str,
    aliases: Sequence[str] = (),
    section_updates: dict[str, str] | None = None,
    timeline_entries: Sequence[str] = (),
    evidence_paths: Sequence[str] = (),
) -> CanonicalUpdate:
    if current_markdown is None:
        markdown = render_canonical_markdown(
            family=family,
            title=title,
            slug=slug,
            domain=domain,
            aliases=aliases,
        )
    else:
        markdown = _ensure_canonical_scaffold(
            current_markdown,
            family=family,
            title=title,
            slug=slug,
            domain=domain,
            aliases=aliases,
        )

    document = load_markdown_document(markdown)
    frontmatter = dict(document.frontmatter)
    frontmatter["title"] = title
    frontmatter["type"] = family
    frontmatter["slug"] = slug
    frontmatter["domain"] = domain
    frontmatter["canonical"] = True
    frontmatter["source_kind"] = "canonical"
    frontmatter["has_timeline"] = True
    frontmatter["aliases"] = list(_merge_aliases(frontmatter.get("aliases"), aliases))

    body = document.body
    for section, replacement in (section_updates or {}).items():
        body = replace_section(body, section, replacement)

    if timeline_entries:
        merged_timeline = _merge_markdown_lines(section_text(body, "Timeline"), timeline_entries)
        body = replace_section(body, "Timeline", merged_timeline)

    if evidence_paths:
        evidence_lines = tuple(f"- `{path}`" for path in evidence_paths)
        merged_evidence = _merge_markdown_lines(section_text(body, "Evidence"), evidence_lines)
        body = replace_section(body, "Evidence", merged_evidence)

    return CanonicalUpdate(
        title=title,
        body=dump_markdown_document(frontmatter, body),
        aliases=tuple(frontmatter["aliases"]) if isinstance(frontmatter.get("aliases"), list) else (),
    )


def patch_core_markdown(
    current_markdown: str | None,
    *,
    file_name: str,
    title: str,
    domain: str = "mixed",
    aliases: Sequence[str] = (),
    section_updates: dict[str, str] | None = None,
    timeline_entries: Sequence[str] = (),
    evidence_paths: Sequence[str] = (),
) -> CanonicalUpdate:
    if current_markdown is None:
        markdown = render_core_markdown(
            file_name=file_name,
            title=title,
            domain=domain,
            aliases=aliases,
        )
    else:
        markdown = _ensure_core_scaffold(
            current_markdown,
            file_name=file_name,
            title=title,
            domain=domain,
            aliases=aliases,
        )

    document = load_markdown_document(markdown)
    frontmatter = dict(document.frontmatter)
    frontmatter["title"] = title
    frontmatter["type"] = "core"
    frontmatter["slug"] = Path(file_name).stem
    frontmatter["domain"] = domain
    frontmatter["canonical"] = True
    frontmatter["source_kind"] = "canonical"
    frontmatter["has_timeline"] = True
    frontmatter["aliases"] = list(_merge_aliases(frontmatter.get("aliases"), aliases))

    body = document.body
    for section, replacement in (section_updates or {}).items():
        body = replace_section(body, section, replacement)
    if timeline_entries:
        merged_timeline = _merge_markdown_lines(section_text(body, "Timeline"), timeline_entries)
        body = replace_section(body, "Timeline", merged_timeline)
    if evidence_paths:
        evidence_lines = tuple(f"- `{path}`" for path in evidence_paths)
        merged_evidence = _merge_markdown_lines(section_text(body, "Evidence"), evidence_lines)
        body = replace_section(body, "Evidence", merged_evidence)
    return CanonicalUpdate(
        title=title,
        body=dump_markdown_document(frontmatter, body),
        aliases=tuple(frontmatter["aliases"]) if isinstance(frontmatter.get("aliases"), list) else (),
    )


def replace_section(markdown_body: str, section: str, replacement: str) -> str:
    marker = f"## {section}\n"
    if marker not in markdown_body:
        return markdown_body
    before, after = markdown_body.split(marker, 1)
    next_header = after.find("\n## ")
    if next_header == -1:
        return f"{before}{marker}{replacement.rstrip()}\n"
    current = after[:next_header]
    rest = after[next_header + 1 :]
    _ = current
    return f"{before}{marker}{replacement.rstrip()}\n\n{rest}"


def section_text(markdown_body: str, section: str) -> str:
    marker = f"## {section}\n"
    if marker not in markdown_body:
        return ""
    _, after = markdown_body.split(marker, 1)
    next_header = after.find("\n## ")
    if next_header == -1:
        return after.strip()
    return after[:next_header].strip()


def merge_section_content(existing: str, new_text: str, *, bullet: bool = True) -> str:
    rendered = _normalize_text_block(new_text)
    if not rendered:
        return existing.strip()
    if bullet:
        existing_lines = _line_items(existing)
        candidate = f"- {rendered}"
        if candidate not in existing_lines:
            existing_lines.append(candidate)
        return "\n".join(existing_lines).strip()
    if existing.strip() == rendered:
        return existing.strip()
    if not existing.strip():
        return rendered
    return f"{existing.strip()}\n\n{rendered}"


def build_timeline_entry(*, time_ref: str | None, summary: str, evidence_path: str) -> str:
    stamp = time_ref or "undated"
    return f"- {stamp}: {summary.strip()} (`{evidence_path}`)"


def canonical_title_from_subject(subject_ref: str) -> str:
    family, raw_slug = subject_ref.split(":", 1)
    slug = _normalize_slug(raw_slug)
    base = slug.split("-") if slug else [raw_slug]
    if family == "decision" and len(base) > 3 and all(part.isdigit() for part in base[:1]):
        base = [part for part in base if not (len(part) == 4 and part.isdigit())]
    return " ".join(part.upper() if part in {"api", "db", "cli", "ui", "ux"} else part.title() for part in base if part)


def infer_aliases_from_subject(subject_ref: str, *, requested_subject: str | None = None) -> tuple[str, ...]:
    aliases: list[str] = []
    family, raw_slug = subject_ref.split(":", 1)
    _ = family
    human_slug = raw_slug.replace("-", " ").strip()
    if human_slug:
        aliases.append(human_slug)
    if requested_subject:
        rendered = requested_subject.strip()
        if rendered:
            aliases.append(rendered)
    return tuple(_merge_aliases((), aliases))


def _canonical_frontmatter(
    *,
    title: str,
    doc_type: str,
    slug: str,
    domain: str,
    aliases: Sequence[str],
) -> dict[str, object]:
    frontmatter: dict[str, object] = {
        "title": title,
        "type": doc_type,
        "slug": slug,
        "domain": domain,
        "created": date.today().isoformat(),
        "updated": date.today().isoformat(),
        "aliases": list(_merge_aliases((), aliases)),
        "status": "active",
        "canonical": True,
        "source_kind": "canonical",
        "confidence": "high",
        "has_timeline": True,
    }
    return frontmatter


def _render_canonical_body(section_names: Sequence[str]) -> str:
    top_sections = [name for name in section_names if name not in {"Timeline", "Evidence"}]
    bottom_sections = [name for name in section_names if name in {"Timeline", "Evidence"}]
    rendered_top = "\n\n".join(f"## {section}\n" for section in top_sections).rstrip()
    rendered_bottom = "\n\n".join(f"## {section}\n" for section in bottom_sections).rstrip()
    return f"{rendered_top}\n\n---\n\n{TIMELINE_MARKER}\n\n{rendered_bottom}\n"


def _merge_aliases(existing: object, incoming: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(existing, str):
        values.append(existing)
    elif isinstance(existing, list):
        values.extend(str(item) for item in existing)
    values.extend(incoming)
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        stripped = " ".join(str(value).split()).strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(stripped)
    return tuple(merged)


def _normalize_text_block(text: str) -> str:
    return " ".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def _line_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            items.append(stripped)
    return items


def _section_updates_from_claims(*, family: str, claims: Sequence[ClaimRecord]) -> dict[str, str]:
    active = tuple(claim for claim in claims if claim.statement.strip())
    if family == "person":
        return _compact_updates(
            {
                "Summary": _render_claim_statements(active),
                "Current Facts": _render_claim_statements(
                    claim for claim in active if claim.kind in {"fact", "state", "note"}
                ),
                "Preferences And Working Style": _render_claim_statements(
                    claim for claim in active if claim.kind == "preference"
                ),
            }
        )
    if family == "project":
        return _compact_updates(
            {
                "Summary": _render_claim_statements(active),
                "Current State": _render_claim_statements(
                    claim for claim in active if claim.kind in {"state", "fact", "note"}
                ),
                "Open Work": _render_claim_statements(claim for claim in active if claim.kind == "note"),
                "Key Decisions": _render_claim_statements(claim for claim in active if claim.kind == "decision"),
            }
        )
    if family == "concept":
        return _compact_updates(
            {
                "Summary": _render_claim_statements(active),
                "Current Understanding": _render_claim_statements(claim for claim in active if claim.kind != "note"),
                "Open Questions": _render_claim_statements(claim for claim in active if claim.kind == "note"),
            }
        )
    if family == "decision":
        return _compact_updates(
            {
                "Decision": _render_claim_statements(active),
                "Context": _render_claim_statements(active),
            }
        )
    return {"Summary": _render_claim_statements(active)}


def _render_claim_statements(claims: Iterable[ClaimRecord]) -> str:
    statements = [claim.statement.strip() for claim in claims if claim.statement.strip()]
    if not statements:
        return ""
    statements = list(_dedupe_strings(statements))
    if len(statements) == 1:
        return statements[0]
    return "\n".join(f"- {statement}" for statement in statements)


def _compact_updates(updates: dict[str, str]) -> dict[str, str]:
    return {section: text for section, text in updates.items() if text.strip()}


def _event_time_ref(event: ClaimEvent) -> str:
    return event.created_at[:10] if event.created_at else "undated"


def _event_summary(event: ClaimEvent, claims_by_id: dict[str, ClaimRecord]) -> str:
    claim = claims_by_id.get(event.claim_id)
    statement = claim.statement.strip() if claim is not None else ""
    if event.event_type == "added":
        return statement
    if statement:
        return f"{event.event_type.title()}: {statement}"
    if event.reason:
        return f"{event.event_type.title()}: {event.reason.strip()}"
    return event.event_type.title()


def _events_from_history(history: Sequence[ClaimRecord]) -> tuple[ClaimEvent, ...]:
    return tuple(
        ClaimEvent(
            event_id=f"history:{claim.claim_id}",
            claim_id=claim.claim_id,
            entity_id=claim.entity_id,
            event_type="added" if claim.status == "active" else claim.status,
            reason=None,
            evidence_path=claim.evidence_path,
            created_at=claim.updated_at or claim.created_at,
        )
        for claim in history
        if claim.statement.strip()
    )


def _dedupe_strings(items: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        stripped = item.strip()
        if not stripped:
            continue
        key = stripped.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(stripped)
    return tuple(ordered)


def _merge_markdown_lines(existing: str, incoming: Sequence[str]) -> str:
    merged = _line_items(existing)
    seen = {line.strip().lower() for line in merged}
    for item in incoming:
        stripped = item.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(stripped)
    return "\n".join(merged).strip()


def _normalize_slug(value: str) -> str:
    return slugify_path_segment(value).replace("/", "-").replace("_", "-").strip("-")


def _ensure_canonical_scaffold(
    markdown: str,
    *,
    family: str,
    title: str,
    slug: str,
    domain: str,
    aliases: Sequence[str],
) -> str:
    document = load_markdown_document(markdown)
    body = document.body
    required_sections = CANONICAL_SECTION_TEMPLATES[family]
    if TIMELINE_MARKER in body and all(f"## {section}\n" in body for section in required_sections):
        return markdown
    scaffold = render_canonical_markdown(
        family=family,
        title=title,
        slug=slug,
        domain=domain,
        aliases=aliases,
    )
    summary = _fallback_existing_summary(body)
    for section in required_sections:
        section_value = section_text(body, section)
        if section_value:
            scaffold = dump_markdown_document(
                load_markdown_document(scaffold).frontmatter,
                replace_section(load_markdown_document(scaffold).body, section, section_value),
            )
    if summary and not section_text(load_markdown_document(scaffold).body, "Summary"):
        scaffold = dump_markdown_document(
            load_markdown_document(scaffold).frontmatter,
            replace_section(load_markdown_document(scaffold).body, "Summary", summary),
        )
    return scaffold


def _ensure_core_scaffold(
    markdown: str,
    *,
    file_name: str,
    title: str,
    domain: str,
    aliases: Sequence[str],
) -> str:
    document = load_markdown_document(markdown)
    body = document.body
    required_sections = CORE_FILE_TEMPLATES[Path(file_name).stem]
    if TIMELINE_MARKER in body and all(f"## {section}\n" in body for section in required_sections):
        return markdown
    scaffold = render_core_markdown(
        file_name=file_name,
        title=title,
        domain=domain,
        aliases=aliases,
    )
    summary = _fallback_existing_summary(body)
    for section in required_sections:
        section_value = section_text(body, section)
        if section_value:
            scaffold = dump_markdown_document(
                load_markdown_document(scaffold).frontmatter,
                replace_section(load_markdown_document(scaffold).body, section, section_value),
            )
    if summary and not section_text(load_markdown_document(scaffold).body, required_sections[0]):
        scaffold = dump_markdown_document(
            load_markdown_document(scaffold).frontmatter,
            replace_section(load_markdown_document(scaffold).body, required_sections[0], summary),
        )
    return scaffold


def _fallback_existing_summary(body: str) -> str:
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---" or stripped == TIMELINE_MARKER:
            continue
        lines.append(stripped)
    return "\n".join(lines[:3]).strip()
