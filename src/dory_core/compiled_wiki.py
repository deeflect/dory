from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from dory_core.claim_store import ClaimEvent, ClaimRecord
from dory_core.claims import Claim
from dory_core.claims import EvidenceRef
from dory_core.frontmatter import MarkdownDocument, load_markdown_document
from dory_core.profiles import RetrievalProfileConfig


def render_compiled_page(
    *,
    title: str,
    summary: str,
    claims: tuple[Claim, ...],
    claim_events: tuple[ClaimEvent, ...] | None = None,
    contradictions: tuple[str, ...],
    open_questions: tuple[str, ...],
    last_refreshed: str | None = None,
) -> str:
    claim_lookup = {claim.id: claim.statement for claim in claims}
    return _render_compiled_page_sections(
        title=title,
        summary=summary,
        claims=claims,
        claim_lookup=claim_lookup,
        claim_events=claim_events,
        contradictions=contradictions,
        open_questions=open_questions,
        last_refreshed=last_refreshed,
    )


def _render_compiled_page_sections(
    *,
    title: str,
    summary: str,
    claims: tuple[Claim, ...],
    claim_lookup: dict[str, str],
    claim_events: tuple[ClaimEvent, ...] | None,
    contradictions: tuple[str, ...],
    open_questions: tuple[str, ...],
    last_refreshed: str | None,
) -> str:
    lines = [
        "---",
        f"title: {title}",
        "type: wiki",
        "status: active",
        "canonical: true",
        "source_kind: generated",
        "temperature: warm",
        f"updated: {last_refreshed or date.today().isoformat()}",
        "---",
        "",
        f"# {title}",
        "",
        "## Summary",
        summary.strip(),
        "",
        "## Key claims",
    ]
    if claims:
        for claim in claims:
            lines.append(f"- {claim.statement} [{claim.status}, {claim.confidence}, {claim.freshness}]")
    else:
        lines.append("- None")

    lines.extend(["", "## Evidence"])
    if claim_events is not None:
        event_evidence = _render_event_evidence(claim_events, claim_lookup)
        if event_evidence:
            lines.extend(event_evidence)
        else:
            lines.append("- None")
    elif claims:
        for claim in claims:
            lines.append(f"- {claim.id}")
            for source in claim.sources:
                lines.append(f"  - {source.path} ({source.line}) [{source.surface}] {source.note}")
    else:
        lines.append("- None")

    lines.extend(["", "## Timeline"])
    timeline_lines = _timeline_lines_from_lookup(claim_events, claim_lookup)
    if not timeline_lines and claims:
        timeline_lines = _timeline_lines_from_claims(claims)
    lines.extend(list(timeline_lines) or ["- None"])

    lines.extend(["", "## Contradictions"])
    lines.extend([f"- {item}" for item in contradictions] or ["- None"])
    lines.extend(["", "## Open questions"])
    lines.extend([f"- {item}" for item in open_questions] or ["- None"])
    return "\n".join(lines).strip() + "\n"


def render_compiled_page_from_claim_records(
    *,
    title: str,
    summary: str,
    claim_records: tuple[ClaimRecord, ...],
    claim_events: tuple[ClaimEvent, ...] | None = None,
    contradictions: tuple[str, ...],
    open_questions: tuple[str, ...],
    last_refreshed: str | None = None,
) -> str:
    claims = tuple(_claim_from_record(record) for record in claim_records if record.status == "active")
    claim_lookup = {record.claim_id: record.statement for record in claim_records}
    return _render_compiled_page_sections(
        title=title,
        summary=summary,
        claims=claims,
        claim_lookup=claim_lookup,
        claim_events=claim_events,
        contradictions=contradictions,
        open_questions=open_questions,
        last_refreshed=last_refreshed,
    )


def _claim_from_record(record: ClaimRecord) -> Claim:
    freshness = "fresh" if record.status == "active" else "stale"
    return Claim(
        id=record.claim_id,
        statement=record.statement,
        status=record.status,
        confidence=record.confidence,
        freshness=freshness,
        sources=(
            EvidenceRef(
                path=record.evidence_path,
                line="1:1",
                surface="durable",
                note="Derived from claim store",
            ),
        ),
        last_reviewed=record.updated_at,
    )


def _dedupe_strings(items: tuple[str, ...] | list[str] | object) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:  # type: ignore[assignment]
        stripped = str(item).strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(stripped)
    return tuple(ordered)


def _timeline_lines(claim_events: tuple[ClaimEvent, ...] | None) -> tuple[str, ...]:
    return _timeline_lines_from_lookup(claim_events, {})


def _timeline_lines_from_lookup(
    claim_events: tuple[ClaimEvent, ...] | None,
    claim_lookup: dict[str, str],
) -> tuple[str, ...]:
    if not claim_events:
        return ()
    lines: list[str] = []
    for event in sorted(claim_events, key=lambda item: (item.created_at, item.event_id)):
        detail = _event_detail(event, claim_lookup)
        lines.append(f"- {event.created_at}: {detail}")
    return tuple(lines)


def _timeline_lines_from_claims(claims: tuple[Claim, ...]) -> tuple[str, ...]:
    lines: list[str] = []
    for claim in claims:
        reviewed = claim.last_reviewed.strip() if isinstance(claim.last_reviewed, str) else ""
        timestamp = reviewed or date.today().isoformat()
        source = claim.sources[0].path if claim.sources else ""
        suffix = f" ({source})" if source else ""
        lines.append(f"- {timestamp}: {claim.statement}{suffix}")
    return tuple(lines)


def _render_event_evidence(
    claim_events: tuple[ClaimEvent, ...],
    claim_lookup: dict[str, str],
) -> tuple[str, ...]:
    grouped: dict[str, list[str]] = {}
    for event in sorted(claim_events, key=lambda item: (item.created_at, item.event_id)):
        evidence_path = event.evidence_path.strip()
        if not evidence_path:
            continue
        label = event.event_type.title()
        statement = claim_lookup.get(event.claim_id, "").strip()
        reason = event.reason.strip() if isinstance(event.reason, str) and event.reason.strip() else ""
        detail_parts = [evidence_path]
        if statement:
            detail_parts.append(statement)
        if reason:
            detail_parts.append(reason)
        grouped.setdefault(label, []).append(" - ".join(detail_parts))

    if not grouped:
        return ()

    lines: list[str] = []
    for label in sorted(grouped):
        lines.append(f"### {label}")
        for item in _dedupe_strings(grouped[label]):
            lines.append(f"- {item}")
    return tuple(lines)


_WARM_CARD_MAX_AGE_DAYS = 30
_WAKE_CARD_DROPPED_SECTIONS = frozenset({"evidence", "timeline"})
_WAKE_CARD_OPTIONAL_SECTIONS = frozenset({"contradictions", "open questions"})


def collect_project_card(
    root: Path,
    *,
    project: str | None,
    retrieval_profile: RetrievalProfileConfig | None = None,
) -> list[tuple[Path, str]]:
    """Collect the compiled project card at ``wiki/projects/<project>.md`` when allowed."""
    if not project:
        return []
    resolved_root = root.resolve()
    wiki_root = (resolved_root / "wiki").resolve()
    if not wiki_root.exists():
        return []
    loaded = _load_compiled_card(wiki_root / "projects" / f"{project}.md", root=resolved_root, base=wiki_root)
    if loaded is None:
        return []
    rel_path, text, _doc = loaded
    if not _profile_allows_compiled_path(rel_path, retrieval_profile):
        return []
    return [(rel_path, text)]


def collect_general_cards(
    root: Path,
    *,
    retrieval_profile: RetrievalProfileConfig | None = None,
    max_cards: int = 3,
    now: date | None = None,
) -> list[tuple[Path, str]]:
    """Collect fresh compiled cards under ``wiki/people/`` and ``wiki/concepts/``.

    Cards must be ``status: active`` and ``canonical: true``. ``temperature: hot``
    cards always qualify; ``warm`` cards must carry an ``updated`` date within the
    last ``_WARM_CARD_MAX_AGE_DAYS`` days so one-off captures age out of wake
    instead of staying eligible forever. Sorted by newest ``updated`` first, then
    path, so the cap is stable but not purely alphabetical.
    """
    if max_cards <= 0:
        return []
    resolved_root = root.resolve()
    wiki_root = (resolved_root / "wiki").resolve()
    if not wiki_root.exists():
        return []
    today = now or date.today()

    candidates: list[tuple[str, str, Path, str]] = []
    for subdir in ("people", "concepts"):
        dir_path = wiki_root / subdir
        if not dir_path.exists():
            continue
        for path in sorted(dir_path.glob("*.md")):
            loaded = _load_compiled_card(path, root=resolved_root, base=wiki_root)
            if loaded is None:
                continue
            rel_path, text, doc = loaded
            if not _profile_allows_compiled_path(rel_path, retrieval_profile):
                continue
            if not _is_wake_fresh_compiled_card(doc, today=today):
                continue
            updated = str(doc.frontmatter.get("updated", "")).strip()
            candidates.append((updated, rel_path.as_posix(), rel_path, text))

    return [(rel_path, text) for _updated, _path_key, rel_path, text in sorted(candidates, reverse=True)[:max_cards]]


def wake_staleness_note(text: str, *, max_age_days: int, now: date | None = None) -> str | None:
    """Compute a one-line stale marker from frontmatter dates at read time.

    Stored freshness labels rot silently; wake must derive staleness from the
    ``updated`` date every time it serves a page. Pages without a parseable
    ``updated`` don't track freshness (the normalizer stamps it on every real
    write), so they return ``None`` rather than a false alarm.
    """
    try:
        document = load_markdown_document(text)
    except ValueError:
        return None
    updated = _frontmatter_date(document.frontmatter.get("updated"))
    if updated is None:
        return None
    age_days = ((now or date.today()) - updated).days
    if age_days <= max_age_days:
        return None
    return (
        f"> [stale] last updated {updated.isoformat()} ({age_days} days ago) — "
        "verify against newer memory (dory_search) before trusting."
    )


def wake_card_excerpt(text: str) -> str:
    """Compact a compiled card for the wake block.

    Drops frontmatter and the Evidence/Timeline bookkeeping sections, and keeps
    Contradictions/Open questions only when they hold real entries.
    """
    try:
        document = load_markdown_document(text)
    except ValueError:
        return text
    title = str(document.frontmatter.get("title", "")).strip()
    kept: list[str] = []
    section: str | None = None
    section_lines: list[str] = []

    def flush() -> None:
        if section is None:
            kept.extend(section_lines)
            return
        if section in _WAKE_CARD_DROPPED_SECTIONS:
            return
        body_lines = [line.strip() for line in section_lines[1:] if line.strip()]
        if section in _WAKE_CARD_OPTIONAL_SECTIONS and all(line in {"- None", "None"} for line in body_lines):
            return
        kept.extend(section_lines)

    for line in document.body.strip().splitlines():
        if line.startswith("## "):
            flush()
            section = line[3:].strip().casefold()
            section_lines = [line]
        elif section is None:
            kept.append(line)
        else:
            section_lines.append(line)
    flush()

    excerpt = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    if title and not excerpt.startswith("#"):
        excerpt = f"# {title}\n\n{excerpt}".strip()
    return excerpt or text


def _load_compiled_card(path: Path, *, root: Path, base: Path) -> tuple[Path, str, MarkdownDocument] | None:
    """Safely load one compiled wiki card inside ``base`` and return a root-relative path."""
    try:
        resolved_root = root.resolve()
        resolved_base = base.resolve()
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
        resolved_path.relative_to(resolved_base)
    except (OSError, ValueError):
        return None
    if not resolved_path.is_file():
        return None
    text = resolved_path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        doc = load_markdown_document(text)
    except ValueError:
        return None
    return resolved_path.relative_to(resolved_root), text, doc


def _is_hot_compiled_card(document: MarkdownDocument) -> bool:
    frontmatter = document.frontmatter
    status = str(frontmatter.get("status", "")).strip().lower()
    canonical = frontmatter.get("canonical", False)
    temperature = str(frontmatter.get("temperature", "")).strip().lower()
    return status == "active" and canonical is True and temperature in {"hot", "warm"}


def _is_wake_fresh_compiled_card(document: MarkdownDocument, *, today: date) -> bool:
    if not _is_hot_compiled_card(document):
        return False
    temperature = str(document.frontmatter.get("temperature", "")).strip().lower()
    if temperature == "hot":
        return True
    updated = _frontmatter_date(document.frontmatter.get("updated"))
    if updated is None:
        return False
    return (today - updated).days <= _WARM_CARD_MAX_AGE_DAYS


def _frontmatter_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _profile_allows_compiled_path(path: Path, retrieval_profile: RetrievalProfileConfig | None) -> bool:
    if retrieval_profile is None:
        return True
    path_key = path.as_posix()
    if not retrieval_profile.allows_path(path_key, corpus="durable"):
        return False
    legacy_key = _legacy_compiled_card_path(path_key)
    if legacy_key is not None and not retrieval_profile.allows_path(legacy_key, corpus="durable"):
        return False
    return True


def _legacy_compiled_card_path(path: str) -> str | None:
    """Map generated wiki cards to their source-domain path for profile policy checks."""
    if path.startswith("wiki/people/"):
        return path.removeprefix("wiki/")
    if path.startswith("wiki/projects/"):
        return "projects/" + path.removeprefix("wiki/projects/").removesuffix(".md") + "/state.md"
    return None


def _event_detail(event: ClaimEvent, claim_lookup: dict[str, str]) -> str:
    statement = claim_lookup.get(event.claim_id, "").strip()
    reason = event.reason.strip() if isinstance(event.reason, str) and event.reason.strip() else ""
    if event.event_type == "added" and statement:
        return statement
    if event.event_type == "replaced" and statement:
        return f"Replaced: {statement}" + (f" ({reason})" if reason else "")
    if event.event_type == "retired" and statement:
        return f"Retired: {statement}" + (f" ({reason})" if reason else "")
    if event.event_type == "invalidated" and statement:
        return f"Invalidated: {statement}" + (f" ({reason})" if reason else "")
    if reason:
        return reason
    if statement:
        return f"{event.event_type}: {statement}"
    return event.event_type
