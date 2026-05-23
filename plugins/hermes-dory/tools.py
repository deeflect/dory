from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from config import (
    ActiveMemoryProfile,
    RerankMode,
    ResearchCorpus,
    ResearchKind,
    ResearchPublishVisibility,
    SearchCorpus,
    SearchMode,
    WakeProfile,
    _as_optional_string,
)
from client import DoryProviderError


# ── argument coercion helpers ─────────────────────────────────────────────


def _as_optional_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return default


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _as_optional_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    from config import _safe_bool

    return _safe_bool(str(value), default=default if default is not None else False)


def _as_optional_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    raise TypeError("value must be an object")


def _as_optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if not isinstance(value, list):
        raise TypeError("value must be an array of strings")
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def _as_optional_search_mode(value: Any) -> SearchMode | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"hybrid", "lexical", "text", "keyword", "semantic", "recall", "bm25", "vector", "exact"}:
        return string_value  # type: ignore[return-value]
    return None


def _as_optional_rerank_mode(value: Any) -> RerankMode | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"auto", "true", "false"}:
        return string_value  # type: ignore[return-value]
    return None


def _as_optional_wake_profile(value: Any) -> WakeProfile | None:
    return _as_optional_string(value)


def _as_optional_active_memory_profile(value: Any) -> ActiveMemoryProfile | None:
    return _as_optional_string(value)


def _as_optional_research_kind(value: Any) -> ResearchKind | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"report", "briefing", "wiki-note", "proposal"}:
        return string_value  # type: ignore[return-value]
    return None


def _as_optional_research_corpus(value: Any) -> ResearchCorpus | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"durable", "sessions", "all"}:
        return string_value  # type: ignore[return-value]
    return None


def _as_optional_research_publish_visibility(value: Any) -> ResearchPublishVisibility | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"internal", "public", "private"}:
        return string_value  # type: ignore[return-value]
    return None


def _as_optional_search_corpus(value: Any) -> SearchCorpus | None:
    string_value = _as_optional_string(value)
    if string_value is None:
        return None
    if string_value in {"durable", "sessions", "all"}:
        return string_value  # type: ignore[return-value]
    return None


def _require_string(args: dict[str, Any], key: str) -> str:
    value = _as_optional_string(args.get(key))
    if value is None:
        raise ValueError(f"missing required argument: {key}")
    return value


# ── list/collection helpers ───────────────────────────────────────────────


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return _dedupe_strings(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return []


def _search_result_paths(payload: dict[str, Any]) -> list[str]:
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    paths: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        path = str(result.get("path", "")).strip()
        if path:
            paths.append(path)
    return _dedupe_strings(paths)


def _dedupe_strings(values: list[str] | tuple[str, ...] | Any) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        deduped.append(text)
        seen.add(text)
    return deduped


# ── error payload helper ──────────────────────────────────────────────────


def _tool_error_payload(err: DoryProviderError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": err.message,
        "error_type": err.error_type,
    }
    if err.status_code is not None:
        payload["status_code"] = err.status_code
    return payload


# ── built-in memory mapping ───────────────────────────────────────────────


def _map_builtin_memory_action(action: str) -> Literal["write", "replace", "forget"] | None:
    if action == "add":
        return "write"
    if action == "replace":
        return "replace"
    if action == "remove":
        return "forget"
    return None


def _format_builtin_memory_mirror(*, action: str, target: str, content: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"[{timestamp}] action={action} target={target}\n{content.strip()}\n"


# ── research / knowledge note rendering ───────────────────────────────────


def _render_research_knowledge_note(
    *,
    title: str,
    body: str,
    question: str | None,
    sources: list[str],
) -> str:
    lines = [f"# {title}", ""]
    if question and question.strip():
        lines.extend(["## Question", question.strip(), ""])
    lines.extend(["## Research", body.strip(), "", "## Sources"])
    cleaned_sources = [source.strip() for source in sources if source.strip()]
    lines.extend(f"- {source}" for source in cleaned_sources)
    if not cleaned_sources:
        lines.append("- None")
    return "\n".join(lines).strip() + "\n"


def _slugify(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    compact = "-".join(part for part in cleaned.split("-") if part)
    return compact or "session"


# ── session turn rendering ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SessionTurn:
    role: Literal["user", "assistant"]
    content: str


def _render_session_turns(turns: list[SessionTurn]) -> str:
    lines: list[str] = []
    for turn in turns:
        content = turn.content.strip()
        if not content:
            continue
        lines.append("## User" if turn.role == "user" else "## Assistant")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def _session_turns_from_messages(messages: list[dict[str, Any]]) -> list[SessionTurn]:
    turns: list[SessionTurn] = []
    for message in messages:
        role = str(message.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        rendered = _render_message_content(message.get("content"))
        if not rendered:
            continue
        turns.append(SessionTurn(role=role, content=rendered))
    return turns


def _render_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    chunks.append(text)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_value = item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    chunks.append(text_value.strip())
                    continue
            text_value = item.get("content") or item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value.strip())
        return "\n\n".join(chunks).strip()
    if isinstance(content, dict):
        text_value = content.get("text") or content.get("content")
        if isinstance(text_value, str):
            return text_value.strip()
    return str(content).strip()
