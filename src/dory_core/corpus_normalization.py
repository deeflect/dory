from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dory_core.slug import slugify_path_segment

DecisionMarker = Literal["section", "line"]
ProjectAction = Literal[
    "project_state",
    "project_support",
    "knowledge",
    "decision",
    "leave_for_review",
]
KnowledgeAction = Literal[
    "knowledge",
    "project_state",
    "project_support",
    "decision",
    "leave_for_review",
]
KnowledgeArea = Literal[
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
    "general",
]

_HEADING_PATTERN = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$", re.MULTILINE)
_DECISION_SECTION_TITLES = {
    "decision",
    "decisions",
    "decisions made",
    "decisions & outcomes",
    "architecture decisions",
    "key decisions",
    "important design decision",
}
_DECISION_LINE_PATTERN = re.compile(r"^.*\[DECISION\].*$", re.MULTILINE)
_HEADLESS_JSON_KEYS = {"session_id", "response", "stats"}
_NON_ALNUM_PREFIX_PATTERN = re.compile(r"^[^a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DecisionSnippet:
    marker: DecisionMarker
    heading: str | None
    content: str


@dataclass(frozen=True, slots=True)
class RootDocument:
    source_rel: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def title(self) -> str:
        raw = self.frontmatter.get("title")
        return str(raw).strip() if raw else self.source_rel.stem.replace("-", " ")

    @property
    def stem(self) -> str:
        return self.source_rel.stem

    @property
    def excerpt(self) -> str:
        lines = [line.rstrip() for line in self.body.splitlines()]
        kept = [line for line in lines if line.strip()]
        return "\n".join(kept[:12])[:1000]


@dataclass(frozen=True, slots=True)
class ExtractedDecision:
    source_rel: Path
    target_rel: Path
    frontmatter: dict[str, Any]
    body: str


@dataclass(frozen=True, slots=True)
class ProjectClassification:
    source_rel: Path
    action: ProjectAction
    target_slug: str | None
    knowledge_area: KnowledgeArea | None
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class KnowledgeClassification:
    source_rel: Path
    action: KnowledgeAction
    target_slug: str | None
    knowledge_area: KnowledgeArea | None
    confidence: float
    reason: str


def extract_decision_snippets(body: str) -> list[DecisionSnippet]:
    snippets: list[DecisionSnippet] = []
    seen: set[str] = set()

    matches = list(_HEADING_PATTERN.finditer(body))
    for index, match in enumerate(matches):
        heading = match.group("title").strip()
        normalized = heading.casefold().strip()
        normalized = _NON_ALNUM_PREFIX_PATTERN.sub("", normalized)
        if normalized not in _DECISION_SECTION_TITLES and not normalized.startswith("decision:"):
            continue

        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        if content and content not in seen:
            seen.add(content)
            snippets.append(DecisionSnippet(marker="section", heading=heading, content=content))

    decision_lines = [line.strip() for line in _DECISION_LINE_PATTERN.findall(body) if line.strip()]
    if decision_lines:
        content = "## Extracted [DECISION] lines\n\n" + "\n".join(decision_lines)
        if content not in seen:
            snippets.append(DecisionSnippet(marker="line", heading="Extracted [DECISION] lines", content=content))

    return snippets


def build_extracted_decision(
    source_rel: Path,
    frontmatter: dict[str, Any],
    body: str,
) -> ExtractedDecision | None:
    snippets = extract_decision_snippets(body)
    if not snippets:
        return None

    date_value = _coerce_date(frontmatter.get("date")) or _coerce_date(frontmatter.get("created")) or "undated"
    title = str(frontmatter.get("title") or source_rel.stem.replace("-", " ")).strip()
    slug_source = "-".join(source_rel.with_suffix("").parts[-2:])
    slug = slugify_path_segment(slug_source) or "memory"
    target_rel = Path("decisions") / "extracted" / f"{date_value}-{slug}.md"

    extracted_frontmatter = {
        "title": f"{title} — Decisions",
        "created": date_value,
        "type": "decision",
        "status": "active",
        "canonical": False,
        "source_kind": "extracted",
        "temperature": "warm",
        "sources": [source_rel.as_posix()],
    }

    sections = "\n\n".join(snippet.content for snippet in snippets)
    extracted_body = (
        f"# {title} — Decisions\n\n"
        f"Extracted verbatim from explicit decision markers in `{source_rel.as_posix()}`.\n\n"
        f"{sections}\n"
    )
    return ExtractedDecision(
        source_rel=source_rel,
        target_rel=target_rel,
        frontmatter=extracted_frontmatter,
        body=extracted_body,
    )


def parse_headless_json_response(raw_output: str) -> Any:
    payload = json.loads(raw_output)
    if isinstance(payload, dict) and _HEADLESS_JSON_KEYS.issubset(payload):
        return json.loads(payload["response"])
    return payload


def parse_project_classifications(raw_output: str) -> list[ProjectClassification]:
    parsed = parse_headless_json_response(raw_output)
    if not isinstance(parsed, list):
        raise ValueError("expected a list of project classifications")

    results: list[ProjectClassification] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("expected classification item to be an object")
        action = _require_literal(
            value=item.get("action"),
            allowed={"project_state", "project_support", "knowledge", "decision", "leave_for_review"},
            field_name="action",
        )
        results.append(
            ProjectClassification(
                source_rel=Path(_require_str(item.get("source_rel"), "source_rel")),
                action=action,
                target_slug=_normalize_optional_slug(item.get("target_slug")),
                knowledge_area=_normalize_optional_area(item.get("knowledge_area")),
                confidence=_normalize_confidence(item.get("confidence")),
                reason=_optional_reason(item.get("reason")),
            )
        )
    return results


def parse_knowledge_classifications(raw_output: str) -> list[KnowledgeClassification]:
    parsed = parse_headless_json_response(raw_output)
    if not isinstance(parsed, list):
        raise ValueError("expected a list of knowledge classifications")

    results: list[KnowledgeClassification] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("expected classification item to be an object")
        action = _require_literal(
            value=item.get("action"),
            allowed={"knowledge", "project_state", "project_support", "decision", "leave_for_review"},
            field_name="action",
        )
        results.append(
            KnowledgeClassification(
                source_rel=Path(_require_str(item.get("source_rel"), "source_rel")),
                action=action,
                target_slug=_normalize_optional_slug(item.get("target_slug")),
                knowledge_area=_normalize_optional_area(item.get("knowledge_area")),
                confidence=_normalize_confidence(item.get("confidence")),
                reason=_optional_reason(item.get("reason")),
            )
        )
    return results


def render_project_batch_prompt(
    documents: list[RootDocument],
) -> str:
    docs_json = json.dumps(
        [
            {
                "source_rel": document.source_rel.as_posix(),
                "title": document.title,
                "status": document.frontmatter.get("status"),
                "type": document.frontmatter.get("type"),
                "excerpt": document.excerpt,
            }
            for document in documents
        ],
        ensure_ascii=False,
        indent=2,
    )
    return (
        "You are classifying personal-agent memory docs for structural normalization.\n"
        "Use only the provided evidence. Do not invent facts or rewrite content.\n"
        "Return JSON only: a list with one object per document.\n"
        "Every object must include exactly these keys: source_rel, action, target_slug, knowledge_area, confidence, reason.\n\n"
        "Allowed actions:\n"
        '- "project_state": canonical overview/current state for a project. Use target_slug.\n'
        '- "project_support": supporting note/spec/plan/research that belongs under a project folder. Use target_slug.\n'
        '- "knowledge": reusable knowledge that should leave projects/. Use knowledge_area.\n'
        '- "decision": durable decision record. Use no knowledge_area.\n'
        '- "leave_for_review": ambiguous.\n\n'
        "Rules:\n"
        "- If the document is clearly about one project, prefer project_state or project_support.\n"
        "- Use the project name stated in the document when available.\n"
        "- Use leave_for_review when evidence is mixed.\n"
        "- target_slug must be kebab-case.\n"
        "- knowledge_area must be one of: coding, writing, marketing, product, design, ops, personal, health, finance, relationships, sales, general.\n"
        "- confidence must be a float from 0 to 1.\n\n"
        f"Documents:\n{docs_json}\n"
    )


def render_knowledge_batch_prompt(documents: list[RootDocument]) -> str:
    docs_json = json.dumps(
        [
            {
                "source_rel": document.source_rel.as_posix(),
                "title": document.title,
                "status": document.frontmatter.get("status"),
                "type": document.frontmatter.get("type"),
                "excerpt": document.excerpt,
            }
            for document in documents
        ],
        ensure_ascii=False,
        indent=2,
    )
    return (
        "You are classifying loose knowledge docs for structural normalization.\n"
        "Use only the provided evidence. Do not invent facts or rewrite content.\n"
        "Return JSON only: a list with one object per document.\n"
        "Every object must include exactly these keys: source_rel, action, target_slug, knowledge_area, confidence, reason.\n\n"
        "Allowed actions:\n"
        '- "knowledge": keep in knowledge/, but assign one knowledge_area.\n'
        '- "project_state": this is really a project state doc. Use target_slug.\n'
        '- "project_support": this is project-specific support material. Use target_slug.\n'
        '- "decision": durable decision record.\n'
        '- "leave_for_review": ambiguous.\n\n'
        "Rules:\n"
        "- Prefer knowledge only for reusable playbooks, preferences, frameworks, or internal protocols.\n"
        "- Use project_state/project_support when the file is clearly about one initiative.\n"
        "- Use decision only when the file itself reads like a durable choice record.\n"
        "- knowledge_area must be one of: coding, writing, marketing, product, design, ops, personal, health, finance, relationships, sales, general.\n"
        "- confidence must be a float from 0 to 1.\n\n"
        f"Documents:\n{docs_json}\n"
    )


def _coerce_date(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"expected non-empty string for {field_name}")
    return value.strip()


def _require_literal(value: object, *, allowed: set[str], field_name: str) -> str:
    rendered = _require_str(value, field_name)
    if rendered not in allowed:
        raise ValueError(f"invalid {field_name}: {rendered}")
    return rendered


def _normalize_optional_slug(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not rendered:
        return None
    slug = slugify_path_segment(rendered)
    return slug or None


def _normalize_optional_area(value: object) -> KnowledgeArea | None:
    if value is None:
        return None
    rendered = str(value).strip().lower()
    allowed: set[str] = {
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
        "general",
    }
    if rendered not in allowed:
        raise ValueError(f"invalid knowledge_area: {rendered}")
    return rendered  # type: ignore[return-value]


def _normalize_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError("invalid confidence") from err
    if confidence < 0 or confidence > 1:
        raise ValueError(f"confidence out of range: {confidence}")
    return confidence


def _optional_reason(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
