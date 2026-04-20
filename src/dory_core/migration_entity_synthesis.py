"""Synthesize canonical entity pages from evidence.

For each entity in ``.dory/entities.json``, gather every evidence file
that mentions it (active bucket file, archive tombstone, daily/session
mentions) and feed them to the LLM with a strict synthesis schema.
The LLM returns compiled-truth content + timeline entries, which we
render into the canonical markdown page.

Key properties:
- No invention. Every claim must be cited from the evidence set.
- 150K-token cap per entity: trim per-file snippets if needed to stay
  under budget. Log trimmed entities.
- Idempotent: re-running overwrites the canonical page with the fresh
  synthesis. Source evidence files are never touched.
- Dedup by synthesis: when active + archive versions of the same
  entity both exist, both are fed as evidence and the LLM reconciles
  them into one truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.frontmatter import dump_markdown_document
from dory_core.llm.openrouter import OpenRouterClient, OpenRouterProviderError
from dory_core.metadata import normalize_frontmatter
from dory_core.migration_entity_discovery import CanonicalEntity
from dory_core.schema import TIMELINE_MARKER
from dory_core.token_counting import TokenCounter, build_token_counter


EntityFamily = Literal["person", "project", "concept", "decision"]

_MAX_ENTITY_INPUT_TOKENS = 120_000  # leave headroom for system prompt + output
_MAX_FILE_CHARS = 20_000  # trim oversized evidence files


@dataclass(frozen=True, slots=True)
class SynthesisSection:
    heading: str
    body: str


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    date: str
    note: str
    evidence_path: str | None = None


@dataclass(frozen=True, slots=True)
class SynthesizedEntity:
    entity: CanonicalEntity
    title: str
    summary: str
    sections: tuple[SynthesisSection, ...]
    timeline: tuple[TimelineEntry, ...]
    aliases: tuple[str, ...]
    evidence_cited: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class SynthesisReport:
    total_entities: int
    synthesized: int
    skipped_no_evidence: int
    skipped_llm_error: int
    written_paths: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = (
    "You produce the canonical page for a single personal-memory entity.\n\n"
    "You receive:\n"
    "- the entity's canonical slug, family, and one-liner\n"
    "- every evidence file in the corpus that mentions this entity\n\n"
    "Your job: synthesize the compiled TRUTH about this entity from the "
    "evidence. Sections for each family:\n"
    "- person   : summary, current_facts, preferences, goals, relationships\n"
    "- project  : summary, current_state, goals, open_work, key_decisions, dependencies\n"
    "- concept  : summary, definition, key_claims, current_understanding, open_questions\n"
    "- decision : summary, decision, rationale, context, alternatives, consequences\n\n"
    "Rules:\n"
    "1. Never invent. Every statement in the output must be grounded in the evidence.\n"
    "2. When evidence contradicts, prefer the most recent dated source. Note the conflict.\n"
    "3. Active-bucket files and recent dailies are higher trust than archive files.\n"
    "4. timeline_entries: chronological, date-prefixed notes distilled from the evidence.\n"
    "5. aliases: the set of names/spellings this entity appears under in the evidence. "
    "Include the canonical slug.\n"
    "6. evidence_cited: exact evidence paths you used. No fabricated paths.\n"
    "7. If the entity has only stale/legacy evidence, write summary accordingly but do "
    "not invent a 'current' state.\n"
    "8. Write in present tense for compiled truth, past tense in timeline.\n"
    "9. Be concise. One paragraph per section by default. Longer only if evidence warrants."
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["heading", "body"],
            },
        },
        "timeline_entries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "date": {"type": "string"},
                    "note": {"type": "string"},
                    "evidence_path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["date", "note", "evidence_path"],
            },
        },
        "aliases": {"type": "array", "items": {"type": "string"}},
        "evidence_cited": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title",
        "summary",
        "sections",
        "timeline_entries",
        "aliases",
        "evidence_cited",
    ],
}


class SynthesisProgress(Protocol):
    def __call__(self, *, index: int, total: int, slug: str, result: str) -> None: ...


def synthesize_entities(
    entities: Sequence[CanonicalEntity],
    *,
    corpus_root: Path,
    client: OpenRouterClient,
    progress: SynthesisProgress | None = None,
    counter: TokenCounter | None = None,
) -> SynthesisReport:
    """Run synthesis for every entity and write canonical markdown pages."""
    counter = counter or build_token_counter()
    total = len(entities)
    written: list[str] = []
    skipped_no_evidence = 0
    skipped_llm_error = 0
    synthesized = 0

    for index, entity in enumerate(entities, start=1):
        evidence = _gather_evidence(corpus_root, entity, counter=counter)
        if not evidence.files:
            skipped_no_evidence += 1
            if progress is not None:
                progress(index=index, total=total, slug=entity.slug, result="skipped-no-evidence")
            continue

        result = _synthesize_one(client, entity, evidence)
        if result is None:
            skipped_llm_error += 1
            if progress is not None:
                progress(index=index, total=total, slug=entity.slug, result="skipped-llm-error")
            continue

        destination = _destination_for(entity)
        markdown = _render_page(result, destination=destination)
        target = resolve_corpus_target(corpus_root, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, markdown, encoding="utf-8")
        written.append(destination.as_posix())
        synthesized += 1
        if progress is not None:
            progress(index=index, total=total, slug=entity.slug, result="written")

    return SynthesisReport(
        total_entities=total,
        synthesized=synthesized,
        skipped_no_evidence=skipped_no_evidence,
        skipped_llm_error=skipped_llm_error,
        written_paths=written,
    )


@dataclass(frozen=True, slots=True)
class _EvidenceBundle:
    files: list[tuple[Path, str]]  # (relative_path, text)
    total_tokens: int
    truncated: bool


def _gather_evidence(
    corpus_root: Path,
    entity: CanonicalEntity,
    *,
    counter: TokenCounter,
) -> _EvidenceBundle:
    """Collect every file referenced by the entity, respecting token budget."""
    candidates = _candidate_paths(corpus_root, entity)
    loaded: list[tuple[Path, str]] = []
    running_tokens = 0
    truncated = False

    for relative in candidates:
        absolute = corpus_root / relative
        if not absolute.exists():
            continue
        try:
            text = absolute.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > _MAX_FILE_CHARS:
            text = text[:_MAX_FILE_CHARS] + "\n\n… (truncated for entity synthesis)"
            truncated = True
        tokens = counter.count(text)
        if running_tokens + tokens > _MAX_ENTITY_INPUT_TOKENS:
            truncated = True
            break
        loaded.append((relative, text))
        running_tokens += tokens

    return _EvidenceBundle(files=loaded, total_tokens=running_tokens, truncated=truncated)


def _candidate_paths(corpus_root: Path, entity: CanonicalEntity) -> list[Path]:
    """Order: canonical active path, evidence_paths from discovery, archive variants."""
    seen: set[str] = set()
    ordered: list[Path] = []

    for explicit in (_canonical_path(entity), *_archive_paths(entity)):
        key = explicit.as_posix()
        if key in seen:
            continue
        if (corpus_root / explicit).exists():
            seen.add(key)
            ordered.append(explicit)

    for raw in entity.evidence_paths:
        candidate = Path(raw)
        key = candidate.as_posix()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)

    return ordered


def _canonical_path(entity: CanonicalEntity) -> Path:
    family = entity.family
    slug = entity.slug
    if family == "project":
        return Path("projects") / slug / "state.md"
    if family == "person":
        return Path("people") / f"{slug}.md"
    if family == "concept":
        return Path("concepts") / f"{slug}.md"
    if family == "decision":
        return Path("decisions") / f"{slug}.md"
    raise ValueError(f"unsupported family: {family}")


def _archive_paths(entity: CanonicalEntity) -> list[Path]:
    family = entity.family
    slug = entity.slug
    if family == "project":
        return [Path("archive") / "projects" / f"{slug}.md"]
    if family == "person":
        return [Path("archive") / "people" / f"{slug}.md"]
    if family == "concept":
        return [
            Path("archive") / "concepts" / f"{slug}.md",
            Path("archive") / "knowledge" / f"{slug}.md",
        ]
    if family == "decision":
        return [Path("archive") / "decisions" / f"{slug}.md"]
    return []


def _destination_for(entity: CanonicalEntity) -> Path:
    return _canonical_path(entity)


def _synthesize_one(
    client: OpenRouterClient,
    entity: CanonicalEntity,
    evidence: _EvidenceBundle,
) -> SynthesizedEntity | None:
    prompt = _build_prompt(entity, evidence)
    try:
        payload = client.generate_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=prompt,
            schema_name="dory_entity_synthesis",
            schema=_SCHEMA,
        )
    except OpenRouterProviderError:
        return None
    return _parse_synthesis(payload, entity=entity, truncated=evidence.truncated)


def _build_prompt(entity: CanonicalEntity, evidence: _EvidenceBundle) -> str:
    lines: list[str] = [
        f"Canonical slug: {entity.slug}",
        f"Family: {entity.family}",
        f"One-liner hint (from discovery): {entity.one_liner}",
        f"Status signal (from discovery): {entity.status_signal}",
        f"Aliases seen: {', '.join(entity.aliases) if entity.aliases else '(none)'}",
        "",
        "=== EVIDENCE FILES ===",
    ]
    for relative, text in evidence.files:
        lines.append(f"\n--- {relative.as_posix()} ---")
        lines.append(text)
    return "\n".join(lines)


def _parse_synthesis(
    payload: Any,
    *,
    entity: CanonicalEntity,
    truncated: bool,
) -> SynthesizedEntity | None:
    if not isinstance(payload, dict):
        return None
    title = payload.get("title")
    summary = payload.get("summary")
    raw_sections = payload.get("sections")
    raw_timeline = payload.get("timeline_entries")
    raw_aliases = payload.get("aliases")
    raw_cited = payload.get("evidence_cited")

    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(summary, str):
        return None
    if not isinstance(raw_sections, list):
        return None
    if not isinstance(raw_timeline, list):
        return None
    if not isinstance(raw_aliases, list):
        return None
    if not isinstance(raw_cited, list):
        return None

    sections: list[SynthesisSection] = []
    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        heading = item.get("heading")
        body = item.get("body")
        if not isinstance(heading, str) or not heading.strip():
            continue
        if not isinstance(body, str):
            continue
        sections.append(SynthesisSection(heading=heading.strip(), body=body.strip()))

    timeline: list[TimelineEntry] = []
    for item in raw_timeline:
        if not isinstance(item, dict):
            continue
        date = item.get("date")
        note = item.get("note")
        evidence_path = item.get("evidence_path")
        if not isinstance(date, str) or not isinstance(note, str):
            continue
        timeline.append(
            TimelineEntry(
                date=date.strip(),
                note=note.strip(),
                evidence_path=evidence_path.strip()
                if isinstance(evidence_path, str) and evidence_path.strip()
                else None,
            )
        )

    aliases = tuple(sorted({a.strip() for a in raw_aliases if isinstance(a, str) and a.strip()}))
    cited = tuple(sorted({c.strip() for c in raw_cited if isinstance(c, str) and c.strip()}))

    return SynthesizedEntity(
        entity=entity,
        title=title.strip(),
        summary=summary.strip(),
        sections=tuple(sections),
        timeline=tuple(timeline),
        aliases=aliases,
        evidence_cited=cited,
        truncated=truncated,
    )


def _render_page(synthesized: SynthesizedEntity, *, destination: Path) -> str:
    frontmatter = _frontmatter_for(synthesized, destination)
    body = _render_body(synthesized)
    normalized = normalize_frontmatter(frontmatter, target=destination)
    return dump_markdown_document(normalized, body)


def _frontmatter_for(
    synthesized: SynthesizedEntity,
    destination: Path,
) -> dict[str, Any]:
    entity = synthesized.entity
    frontmatter: dict[str, Any] = {
        "title": synthesized.title,
        "type": entity.family,
        "status": _status_for_entity(entity),
        "canonical": True,
        "source_kind": "distilled",
        "temperature": "warm",
        "slug": entity.slug,
        "aliases": list(synthesized.aliases),
        "created": _today(),
        "updated": _today(),
        "synthesis": {
            "evidence_count": len(synthesized.evidence_cited),
            "truncated": synthesized.truncated,
            "one_liner_hint": entity.one_liner,
        },
    }
    if entity.family == "project" and destination.name != "state.md":
        frontmatter["canonical"] = False
    return frontmatter


def _render_body(synthesized: SynthesizedEntity) -> str:
    lines: list[str] = [f"# {synthesized.title}", "", synthesized.summary, ""]
    for section in synthesized.sections:
        lines.append(f"## {section.heading}")
        lines.append("")
        lines.append(section.body)
        lines.append("")
    lines.append(TIMELINE_MARKER)
    lines.append("")
    for entry in sorted(synthesized.timeline, key=lambda e: e.date):
        evidence_suffix = f" ([[{entry.evidence_path}]])" if entry.evidence_path else ""
        lines.append(f"- {entry.date}: {entry.note}{evidence_suffix}")
    lines.append("")
    if synthesized.evidence_cited:
        lines.append("## Evidence")
        lines.append("")
        for citation in synthesized.evidence_cited:
            lines.append(f"- [[{citation}]]")
    return "\n".join(lines).rstrip() + "\n"


def _status_for_entity(entity: CanonicalEntity) -> str:
    if entity.status_signal == "active":
        return "active"
    if entity.status_signal == "done":
        return "done"
    if entity.status_signal == "paused":
        return "paused"
    if entity.status_signal == "stale":
        return "superseded"
    return "active"


def _today() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def format_synthesis_summary(report: SynthesisReport) -> dict[str, Any]:
    return {
        "total_entities": report.total_entities,
        "synthesized": report.synthesized,
        "skipped_no_evidence": report.skipped_no_evidence,
        "skipped_llm_error": report.skipped_llm_error,
        "written_paths_sample": report.written_paths[:10],
    }


def load_entities_from_json(path: Path) -> list[CanonicalEntity]:
    """Convenience loader for ``.dory/entities.json`` output from Pass B."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    entities: list[CanonicalEntity] = []
    for item in payload.get("entities", []):
        entities.append(
            CanonicalEntity(
                slug=item["slug"],
                family=item["family"],
                aliases=tuple(item.get("aliases", ())),
                one_liner=item.get("one_liner", ""),
                status_signal=item.get("status_signal", "unknown"),
                evidence_paths=tuple(item.get("evidence_paths", ())),
                mention_count=int(item.get("mention_count", 1)),
            )
        )
    return entities
