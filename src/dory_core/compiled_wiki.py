from __future__ import annotations

from datetime import date

from dory_core.claim_store import ClaimEvent, ClaimRecord
from dory_core.claims import Claim
from dory_core.claims import EvidenceRef


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
