from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dory_core.active_memory_policy import SourcePolicy, text_matches_topic, topic_tokens
from dory_core.markdown_excerpt import (
    extract_markdown_section,
    result_evidence_text,
    truncate_text,
)
from dory_core.retrieval_planner import ActiveMemoryPlanningContext

_RENDER_SNIPPET_CHARS = 300
_SESSION_RENDER_SNIPPET_CHARS = 160
_MEMORY_BULLET_CHARS = 220
_CHARS_PER_TOKEN = 4  # placeholder, actual value varies
_MAX_BLOCK_CHARS = 3200
_MIN_BLOCK_CHARS = 700


@dataclass(frozen=True, slots=True)
class WikiHelperContext:
    block: str
    sources: list[str]
    current_focus: str
    recent_pages: tuple[str, ...]
    active_threads: tuple[str, ...]
    index_hints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    text: str
    weight: float


def empty_wiki_helper_context() -> WikiHelperContext:
    return WikiHelperContext(
        block="",
        sources=[],
        current_focus="",
        recent_pages=(),
        active_threads=(),
        index_hints=(),
    )


def planning_context_from_helper(helper: WikiHelperContext) -> ActiveMemoryPlanningContext:
    return ActiveMemoryPlanningContext(
        current_focus=helper.current_focus,
        recent_pages=helper.recent_pages,
        active_threads=helper.active_threads,
        index_hints=helper.index_hints,
        entity_names=(),
        entity_source_refs=(),
    )


def load_wiki_helper_context(root: Path | None) -> WikiHelperContext:
    if root is None:
        return empty_wiki_helper_context()
    sections: list[str] = []
    sources: list[str] = []
    current_focus = ""
    recent_pages: tuple[str, ...] = ()
    active_threads: tuple[str, ...] = ()
    index_hints: tuple[str, ...] = ()

    hot_path = root / "wiki" / "hot.md"
    if hot_path.exists():
        current_focus = _clean_helper_item(_first_list_item_in_section(hot_path, "Current Focus"))
        recent_pages = _clean_helper_items(_list_items_in_section(hot_path, "Recent Pages"))[:3]
        active_threads = _clean_helper_items(_list_items_in_section(hot_path, "Active Threads"))[:3]
        hot_summary = _wiki_helper_summary(hot_path)
        if hot_summary:
            sections.append(f"## Hot Cache\n- {hot_summary}")
        if hot_summary or current_focus or recent_pages or active_threads:
            sources.append("wiki/hot.md")

    index_path = root / "wiki" / "index.md"
    if index_path.exists():
        index_hints = _clean_helper_items(_list_items_in_section(index_path, "Recent Pages"))[:4]
        index_summary = _wiki_helper_summary(index_path)
        if index_summary:
            sections.append(f"## Wiki Index\n- {index_summary}")
        if index_summary or index_hints:
            sources.append("wiki/index.md")

    return WikiHelperContext(
        block="\n\n".join(sections).strip(),
        sources=sources,
        current_focus=current_focus,
        recent_pages=recent_pages,
        active_threads=active_threads,
        index_hints=index_hints,
    )


def topic_scoped_helper_context(
    helper: WikiHelperContext,
    *,
    prompt: str,
    source_policy: SourcePolicy,
) -> WikiHelperContext:
    if source_policy.profile not in {"coding", "writing"}:
        return helper

    prompt_tokens = topic_tokens(prompt)
    if not prompt_tokens:
        return helper

    current_focus = helper.current_focus if text_matches_topic(helper.current_focus, prompt_tokens) else ""
    recent_pages = tuple(item for item in helper.recent_pages if text_matches_topic(item, prompt_tokens))
    active_threads = tuple(item for item in helper.active_threads if text_matches_topic(item, prompt_tokens))
    index_hints = tuple(item for item in helper.index_hints if text_matches_topic(item, prompt_tokens))
    if current_focus or recent_pages or active_threads or index_hints:
        return WikiHelperContext(
            block=helper.block,
            sources=helper.sources,
            current_focus=current_focus,
            recent_pages=recent_pages,
            active_threads=active_threads,
            index_hints=index_hints,
        )
    return empty_wiki_helper_context()


def render_active_memory_section(
    helper: WikiHelperContext,
    durable_results: list[object],
    session_results: list[object],
    *,
    memory_bullets: list[str] | None = None,
    root: Path | None,
) -> str:
    bullets = memory_bullets or synthesized_bullets(helper, durable_results, session_results, root=root)
    if not bullets:
        return ""
    lines = ["## Active memory"]
    lines.extend(f"- {bullet}" for bullet in bullets[:5])
    return "\n".join(lines)


def synthesized_bullets(
    helper: WikiHelperContext,
    durable_results: list[object],
    session_results: list[object],
    *,
    root: Path | None,
) -> list[str]:
    candidates: list[MemoryCandidate] = []
    if helper.current_focus:
        candidates.append(MemoryCandidate(text=truncate_text(helper.current_focus, _MEMORY_BULLET_CHARS), weight=6.0))
    for position, item in enumerate(helper.recent_pages[:3], start=1):
        candidates.append(
            MemoryCandidate(text=truncate_text(item, _MEMORY_BULLET_CHARS), weight=4.5 - (position * 0.3))
        )
    for position, item in enumerate(helper.active_threads[:2], start=1):
        candidates.append(
            MemoryCandidate(text=truncate_text(item, _MEMORY_BULLET_CHARS), weight=3.4 - (position * 0.2))
        )
    for position, item in enumerate(helper.index_hints[:2], start=1):
        candidates.append(
            MemoryCandidate(text=truncate_text(item, _MEMORY_BULLET_CHARS), weight=3.0 - (position * 0.2))
        )
    for position, result in enumerate(durable_results[:4], start=1):
        snippet = truncate_text(result_evidence_text(result, root=root), _MEMORY_BULLET_CHARS)
        if not snippet:
            continue
        score = float(getattr(result, "score", 0.0) or 0.0)
        candidates.append(MemoryCandidate(text=snippet, weight=5.0 + score - (position * 0.25)))
    for position, result in enumerate(session_results[:3], start=1):
        snippet = truncate_text(result_evidence_text(result, root=root), _MEMORY_BULLET_CHARS)
        if not snippet:
            continue
        score = float(getattr(result, "score", 0.0) or 0.0)
        candidates.append(MemoryCandidate(text=snippet, weight=4.0 + score - (position * 0.2)))
    ordered = sorted(candidates, key=lambda item: (-item.weight, item.text.casefold()))
    return _dedupe_strings([candidate.text for candidate in ordered])


def build_summary(
    helper: WikiHelperContext,
    durable_results: list[object],
    session_results: list[object],
    wake_block: str,
    *,
    root: Path | None,
) -> str:
    bullets = synthesized_bullets(helper, durable_results, session_results, root=root)
    if bullets:
        return " | ".join(bullets[:2])[:280]
    if durable_results:
        summary = str(getattr(durable_results[0], "snippet", "") or "").strip()
        if summary:
            return summary[:280]
    if session_results:
        summary = str(getattr(session_results[0], "snippet", "") or "").strip()
        if summary:
            return summary[:280]
    return first_non_empty_line(wake_block)[:280]


def build_block(
    helper: WikiHelperContext,
    wake_block: str,
    durable_results: list[object],
    session_results: list[object],
    *,
    memory_bullets: list[str] | None = None,
    budget_tokens: int,
    root: Path | None,
) -> str:
    active_memory_section = render_active_memory_section(
        helper,
        durable_results,
        session_results,
        memory_bullets=memory_bullets,
        root=root,
    )
    sections = [
        section
        for section in [
            active_memory_section,
            wake_block,
            render_results_section(
                "Durable evidence",
                durable_results,
                max_results=3,
                snippet_chars=_RENDER_SNIPPET_CHARS,
                root=root,
            ),
            render_results_section(
                "Session evidence",
                session_results,
                max_results=2,
                snippet_chars=_SESSION_RENDER_SNIPPET_CHARS,
                root=root,
            ),
        ]
        if section
    ]
    return fit_block_to_budget("\n\n".join(sections).strip(), budget_tokens=budget_tokens)


def render_results_section(
    title: str,
    results: list[object],
    *,
    max_results: int,
    snippet_chars: int,
    root: Path | None,
) -> str:
    if not results:
        return ""
    lines = [f"## {title}"]
    for result in results[:max_results]:
        path = str(getattr(result, "path", "") or "")
        if not path:
            continue
        snippet = result_evidence_text(result, root=root)
        lines.append(f"- {path}")
        if snippet:
            lines.append(f"  {truncate_text(snippet, snippet_chars)}")
    return "\n".join(lines)


def confidence_for_results(
    durable_results: list[object], session_results: list[object]
):
    if durable_results and session_results:
        return "high"
    if durable_results or session_results:
        return "medium"
    return None


def first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def wake_block_for_rendering(wake_block: str, durable_results: list[object], session_results: list[object]) -> str:
    if durable_results or session_results:
        return ""
    return wake_block.strip()


def fit_block_to_budget(block: str, *, budget_tokens: int) -> str:
    char_limit = min(_MAX_BLOCK_CHARS, max(_MIN_BLOCK_CHARS, budget_tokens * _CHARS_PER_TOKEN))
    if len(block) <= char_limit:
        return block
    truncated = block[: max(0, char_limit - 1)].rstrip()
    if "\n## " in truncated:
        section_safe = truncated.rsplit("\n## ", 1)[0].rstrip()
        if len(section_safe) >= _MIN_BLOCK_CHARS:
            return section_safe
    return truncated + "…"


def _wiki_helper_summary(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    in_summary = False
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_summary and lines:
                break
            continue
        if line == "## Summary":
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            break
        if in_summary:
            lines.append(line.lstrip("- ").strip())
    return " ".join(lines)[:240].strip()


def _list_items_in_section(path: Path, heading: str) -> tuple[str, ...]:
    section = extract_markdown_section(path.read_text(encoding="utf-8"), heading)
    if not section:
        return ()
    items: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
    return tuple(items)


def _first_list_item_in_section(path: Path, heading: str) -> str:
    items = _list_items_in_section(path, heading)
    return items[0] if items else ""


def _clean_helper_items(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(cleaned for item in items if (cleaned := _clean_helper_item(item)))


def _clean_helper_item(item: str) -> str:
    cleaned = _clean_markdown_content_line(item)
    if not cleaned:
        return ""
    return truncate_text(cleaned, _MEMORY_BULLET_CHARS)


def _clean_markdown_content_line(raw_line: str) -> str:
    line = raw_line.strip()
    if not line or line == "---":
        return ""
    if line.startswith("#"):
        return ""
    if line.startswith(">"):
        if "shared hot-context" in line.casefold():
            return ""
        return line.lstrip("> ").strip()
    if line.startswith("- "):
        line = line[2:].strip()
    if "shared hot-context loaded into every agent" in line.casefold():
        return ""
    return line


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        value = item.strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped
