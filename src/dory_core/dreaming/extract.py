from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dory_core.config import DorySettings
from dory_core.dreaming.events import SessionClosedEvent
from dory_core.llm.json_client import JSONGenerationClient


@dataclass(frozen=True, slots=True)
class DistilledSession:
    summary: str
    key_facts: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()


class SessionDistiller(Protocol):
    def distill(self, event: SessionClosedEvent, session_text: str) -> Path: ...


_DISTILLATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "followups": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "key_facts", "decisions", "followups", "entities"],
}


class DistillationWriter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(self, event: SessionClosedEvent, summary: str | DistilledSession) -> Path:
        target = self.root / event.output_path
        target.parent.mkdir(parents=True, exist_ok=True)
        distilled = summary if isinstance(summary, DistilledSession) else DistilledSession(summary=summary)
        body = _render_distilled_document(event, distilled)
        target.write_text(body, encoding="utf-8")
        return target


@dataclass(frozen=True, slots=True)
class LLMSessionDistiller:
    client: JSONGenerationClient
    writer: DistillationWriter

    def distill(self, event: SessionClosedEvent, session_text: str) -> Path:
        payload = self.client.generate_json(
            system_prompt=(
                "You distill personal agent session logs into grounded memory notes. "
                "Use only facts present in the session text. "
                "Do not invent missing details. "
                "Keep bullets compact and factual."
            ),
            user_prompt=(
                f"Agent: {event.agent}\n"
                f"Session path: {event.session_path}\n"
                f"Closed at: {event.closed_at.isoformat()}\n\n"
                "Produce a distilled memory note from this session:\n\n"
                f"{session_text}"
            ),
            schema_name="distilled_session",
            schema=_DISTILLATION_SCHEMA,
        )
        distilled = DistilledSession(
            summary=_coerce_string(payload.get("summary")),
            key_facts=_coerce_string_tuple(payload.get("key_facts")),
            decisions=_coerce_string_tuple(payload.get("decisions")),
            followups=_coerce_string_tuple(payload.get("followups")),
            entities=_coerce_string_tuple(payload.get("entities")),
        )
        return self.writer.write(event, distilled)


def resolve_dream_backend(settings: DorySettings) -> str:
    if settings.dream_llm_provider == "local":
        return "local"
    if settings.dream_llm_provider == "auto":
        return "auto"
    return "ollama" if settings.sovereign_mode else "openrouter"


OpenRouterSessionDistiller = LLMSessionDistiller


def _render_distilled_document(event: SessionClosedEvent, distilled: DistilledSession) -> str:
    sections: list[str] = [
        "---",
        f"title: Distilled {event.agent} session",
        f"created: {event.closed_at.date().isoformat()}",
        "type: capture",
        "status: raw",
        "source_kind: distilled",
        "temperature: warm",
        "---",
        "",
        f"Source session: {event.session_path}",
        "",
        "## Summary",
        distilled.summary.strip() or "No summary captured.",
    ]
    sections.extend(_render_bullets("## Key Facts", distilled.key_facts))
    sections.extend(_render_bullets("## Decisions", distilled.decisions))
    sections.extend(_render_bullets("## Follow-ups", distilled.followups))
    sections.extend(_render_bullets("## Entities", distilled.entities))
    sections.append("")
    return "\n".join(sections)


def _render_bullets(title: str, items: tuple[str, ...]) -> list[str]:
    rendered = ["", title]
    if not items:
        rendered.append("- None")
        return rendered
    rendered.extend(f"- {item}" for item in items)
    return rendered


def _coerce_string(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "No summary captured."


def _coerce_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        candidate = " ".join(item.split())
        if not candidate or candidate in items:
            continue
        items.append(candidate)
    return tuple(items)
