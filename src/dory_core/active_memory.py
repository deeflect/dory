from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

from dory_core.retrieval_planner import (
    ActiveMemoryComposer,
    ActiveMemoryPlanningContext,
    ActiveMemoryPlanner,
    ActiveMemoryRetrievalPlan,
    fallback_active_memory_plan,
)
from dory_core.types import ActiveMemoryReq, ActiveMemoryResp, SearchReq, WakeReq, WakeResp


class _WakeBuilder(Protocol):
    def build(self, req: WakeReq) -> WakeResp: ...


class _SearchEngine(Protocol):
    def search(self, req: SearchReq): ...


@dataclass(slots=True)
class ActiveMemoryEngine:
    wake_builder: _WakeBuilder
    search_engine: _SearchEngine
    root: Path | None = None
    planner: ActiveMemoryPlanner | None = None
    composer: ActiveMemoryComposer | None = None

    def build(self, req: ActiveMemoryReq) -> ActiveMemoryResp:
        deadline = _Deadline.from_timeout_ms(req.timeout_ms)
        helper = _load_wiki_helper_context(self.root)
        wake_block = ""
        wake_sources: list[str] = []
        if req.include_wake:
            wake = self.wake_builder.build(
                WakeReq(
                    budget_tokens=min(req.budget_tokens, 600),
                    agent=req.agent,
                    include_recent_sessions=3,
                    include_pinned_decisions=True,
                )
            )
            wake_block = wake.block
            wake_sources = wake.sources
        planning_context = ActiveMemoryPlanningContext(
            current_focus=helper.current_focus,
            recent_pages=helper.recent_pages,
            active_threads=helper.active_threads,
            index_hints=helper.index_hints,
        )
        plan = self._plan(req, planning_context, deadline=deadline)
        durable_results = _search_candidates(
            self.search_engine,
            queries=plan.durable_queries,
            k=plan.durable_limit,
            mode="hybrid",
            corpus="durable",
            include_content=True,
            rerank="true" if req.rerank == "auto" else req.rerank,
            deadline=deadline,
        )
        durable_results = _filter_active_memory_results(durable_results, corpus="durable")
        session_results = (
            _search_candidates(
                self.search_engine,
                queries=plan.session_queries,
                k=plan.session_limit,
                mode="recall",
                corpus="sessions",
                include_content=False,
                rerank="false",
                deadline=deadline,
            )
            if plan.include_sessions and plan.session_limit > 0
            else []
        )
        session_results = _filter_active_memory_results(session_results, corpus="sessions")
        sources = _dedupe_strings(
            [
                *helper.sources,
                *wake_sources,
                *[_result_path(item) for item in durable_results[:4]],
                *[_result_path(item) for item in session_results[:3]],
            ]
        )
        composition = self._compose(req, planning_context, wake_block, durable_results, session_results, deadline=deadline)
        if _composition_conflicts_with_evidence(composition, durable_results):
            composition = None
        synthesized_bullets = _synthesized_bullets(helper, durable_results, session_results)
        memory_bullets = list(composition.bullets) if composition is not None and composition.bullets else synthesized_bullets
        summary = (
            composition.summary
            if composition is not None and composition.summary
            else _build_summary(helper, durable_results, session_results, wake_block)
        )
        block = _build_block(
            helper,
            wake_block.strip(),
            durable_results,
            session_results,
            memory_bullets=memory_bullets,
        )
        confidence = _confidence_for_results(durable_results, session_results)
        return ActiveMemoryResp(
            kind="memory" if block else "none",
            block=block,
            summary=summary,
            confidence=confidence,
            sources=sources,
        )

    def _plan(
        self,
        req: ActiveMemoryReq,
        context: ActiveMemoryPlanningContext,
        *,
        deadline: "_Deadline",
    ) -> ActiveMemoryRetrievalPlan:
        if self.planner is None or deadline.expired:
            return fallback_active_memory_plan(prompt=req.prompt)
        try:
            return self.planner.plan_active_memory(prompt=req.prompt, context=context)
        except Exception:
            return fallback_active_memory_plan(prompt=req.prompt)

    def _compose(
        self,
        req: ActiveMemoryReq,
        context: ActiveMemoryPlanningContext,
        wake_block: str,
        durable_results: list[object],
        session_results: list[object],
        *,
        deadline: "_Deadline",
    ):
        if self.composer is None or deadline.expired:
            return None
        try:
            return self.composer.compose_active_memory(
                prompt=req.prompt,
                context=context,
                wake_summary=_first_non_empty_line(wake_block),
                durable_results=tuple((_result_path(item), _result_snippet(item).strip()) for item in durable_results[:5]),
                session_results=tuple((_result_path(item), _result_snippet(item).strip()) for item in session_results[:4]),
            )
        except Exception:
            return None


@dataclass(frozen=True, slots=True)
class _Deadline:
    expires_at: float

    @classmethod
    def from_timeout_ms(cls, timeout_ms: int) -> "_Deadline":
        return cls(expires_at=monotonic() + (timeout_ms / 1000))

    @property
    def expired(self) -> bool:
        return monotonic() >= self.expires_at


def _result_path(result: object) -> str:
    path = getattr(result, "path", "")
    return str(path)


def _result_snippet(result: object) -> str:
    snippet = getattr(result, "snippet", "")
    return str(snippet)


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


def _build_summary(
    helper: "WikiHelperContext",
    durable_results: list[object],
    session_results: list[object],
    wake_block: str,
) -> str:
    bullets = _synthesized_bullets(helper, durable_results, session_results)
    if bullets:
        return " | ".join(bullets[:2])[:280]
    if durable_results:
        summary = _result_snippet(durable_results[0]).strip()
        if summary:
            return summary[:280]
    if session_results:
        summary = _result_snippet(session_results[0]).strip()
        if summary:
            return summary[:280]
    return _first_non_empty_line(wake_block)[:280]


def _build_block(
    helper: "WikiHelperContext",
    wake_block: str,
    durable_results: list[object],
    session_results: list[object],
    *,
    memory_bullets: list[str] | None = None,
) -> str:
    active_memory_section = _render_active_memory_section(
        helper,
        durable_results,
        session_results,
        memory_bullets=memory_bullets,
    )
    sections = [
        section
        for section in [
            active_memory_section,
            helper.block,
            wake_block,
            _render_results_section("Durable evidence", durable_results),
            _render_results_section("Session evidence", session_results),
        ]
        if section
    ]
    return "\n\n".join(sections).strip()


def _render_results_section(title: str, results: list[object]) -> str:
    if not results:
        return ""
    lines = [f"## {title}"]
    for result in results[:5]:
        path = _result_path(result)
        if not path:
            continue
        snippet = _result_snippet(result)
        lines.append(f"- {path}")
        if snippet:
            lines.append(f"  {snippet}")
    return "\n".join(lines)


def _confidence_for_results(durable_results: list[object], session_results: list[object]) -> str | None:
    if durable_results and session_results:
        return "high"
    if durable_results or session_results:
        return "medium"
    return None


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


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


def _search_candidates(
    search_engine: _SearchEngine,
    *,
    queries: tuple[str, ...],
    k: int,
    mode: str,
    corpus: str,
    include_content: bool,
    rerank: str,
    deadline: _Deadline | None = None,
) -> list[object]:
    scored_results: dict[str, tuple[float, object]] = {}
    for query_index, query in enumerate(query for query in queries if query.strip()):
        if deadline is not None and deadline.expired:
            break
        response = search_engine.search(
            SearchReq(
                query=query,
                k=k,
                mode=mode,
                corpus=corpus,
                include_content=include_content,
                rerank=rerank,
            )
        )
        for result_index, result in enumerate(list(getattr(response, "results", [])), start=1):
            path = _result_path(result)
            if not path:
                continue
            raw_score = float(getattr(result, "score", 0.0) or 0.0)
            score = raw_score - (query_index * 0.1) - (result_index * 0.01)
            existing = scored_results.get(path)
            if existing is None or score > existing[0]:
                scored_results[path] = (score, result)
    ordered = sorted(scored_results.values(), key=lambda item: (-item[0], _result_path(item[1])))
    return [result for _score, result in ordered[:k]]


def _composition_conflicts_with_evidence(composition: object | None, durable_results: list[object]) -> bool:
    if composition is None:
        return False
    has_active_source = any(_result_path(result) == "core/active.md" for result in durable_results)
    if not has_active_source:
        return False
    summary = str(getattr(composition, "summary", "") or "").lower()
    bullets = " ".join(str(item) for item in getattr(composition, "bullets", ()) or ()).lower()
    text = f"{summary} {bullets}"
    return any(
        phrase in text
        for phrase in (
            "no active focus",
            "no active project",
            "no active work",
            "no current focus",
            "no designated task",
        )
    )


def _filter_active_memory_results(results: list[object], *, corpus: str) -> list[object]:
    return [
        result
        for result in results
        if _is_active_memory_candidate(result, corpus=corpus)
    ]


def _is_active_memory_candidate(result: object, *, corpus: str) -> bool:
    path = _result_path(result)
    if not path or path.endswith(".tombstone.md"):
        return False
    if corpus == "durable" and path.startswith("logs/sessions/"):
        return False
    if corpus == "durable" and path.startswith(("inbox/quarantine/", "archive/")):
        return False
    frontmatter = _result_frontmatter(result)
    status = str(frontmatter.get("status", "")).strip().lower()
    if status in {"stale", "superseded", "quarantined", "quarantine"}:
        return False
    if corpus == "durable" and status == "raw":
        return False
    confidence = str(getattr(result, "confidence", "") or "").strip().lower()
    if corpus == "durable" and confidence == "low":
        return False
    stale_warning = str(getattr(result, "stale_warning", "") or "").strip()
    if corpus == "durable" and stale_warning:
        return False
    return True


def _result_frontmatter(result: object) -> dict[str, object]:
    frontmatter = getattr(result, "frontmatter", {})
    return frontmatter if isinstance(frontmatter, dict) else {}


def _load_wiki_helper_context(root: Path | None) -> WikiHelperContext:
    if root is None:
        return WikiHelperContext(
            block="",
            sources=[],
            current_focus="",
            recent_pages=(),
            active_threads=(),
            index_hints=(),
        )
    sections: list[str] = []
    sources: list[str] = []
    current_focus = ""
    recent_pages: tuple[str, ...] = ()
    active_threads: tuple[str, ...] = ()
    index_hints: tuple[str, ...] = ()

    hot_path = root / "wiki" / "hot.md"
    if hot_path.exists():
        current_focus = _first_list_item_in_section(hot_path, "Current Focus")
        recent_pages = _list_items_in_section(hot_path, "Recent Pages")[:3]
        active_threads = _list_items_in_section(hot_path, "Active Threads")[:3]
        hot_summary = _wiki_helper_summary(hot_path)
        if hot_summary:
            sections.append(f"## Hot Cache\n- {hot_summary}")
            sources.append("wiki/hot.md")

    index_path = root / "wiki" / "index.md"
    if index_path.exists():
        index_hints = _list_items_in_section(index_path, "Recent Pages")[:4]
        index_summary = _wiki_helper_summary(index_path)
        if index_summary:
            sections.append(f"## Wiki Index\n- {index_summary}")
            sources.append("wiki/index.md")

    return WikiHelperContext(
        block="\n\n".join(sections).strip(),
        sources=sources,
        current_focus=current_focus,
        recent_pages=recent_pages,
        active_threads=active_threads,
        index_hints=index_hints,
    )


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


def _render_active_memory_section(
    helper: WikiHelperContext,
    durable_results: list[object],
    session_results: list[object],
    *,
    memory_bullets: list[str] | None = None,
) -> str:
    bullets = memory_bullets or _synthesized_bullets(helper, durable_results, session_results)
    if not bullets:
        return ""
    lines = ["## Active memory"]
    lines.extend(f"- {bullet}" for bullet in bullets[:5])
    return "\n".join(lines)


def _synthesized_bullets(
    helper: WikiHelperContext,
    durable_results: list[object],
    session_results: list[object],
) -> list[str]:
    candidates: list[MemoryCandidate] = []
    if helper.current_focus:
        candidates.append(MemoryCandidate(text=helper.current_focus, weight=6.0))
    for position, item in enumerate(helper.recent_pages[:3], start=1):
        candidates.append(MemoryCandidate(text=item, weight=4.5 - (position * 0.3)))
    for position, item in enumerate(helper.active_threads[:2], start=1):
        candidates.append(MemoryCandidate(text=item, weight=3.4 - (position * 0.2)))
    for position, item in enumerate(helper.index_hints[:2], start=1):
        candidates.append(MemoryCandidate(text=item, weight=3.0 - (position * 0.2)))
    for position, result in enumerate(durable_results[:4], start=1):
        snippet = _result_snippet(result).strip()
        if not snippet:
            continue
        score = float(getattr(result, "score", 0.0) or 0.0)
        candidates.append(MemoryCandidate(text=snippet, weight=5.0 + score - (position * 0.25)))
    for position, result in enumerate(session_results[:3], start=1):
        snippet = _result_snippet(result).strip()
        if not snippet:
            continue
        score = float(getattr(result, "score", 0.0) or 0.0)
        candidates.append(MemoryCandidate(text=snippet, weight=4.0 + score - (position * 0.2)))
    ordered = sorted(candidates, key=lambda item: (-item.weight, item.text.casefold()))
    return _dedupe_strings([candidate.text for candidate in ordered])


def _list_items_in_section(path: Path, heading: str) -> tuple[str, ...]:
    section = _extract_markdown_section(path.read_text(encoding="utf-8"), heading)
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


def _extract_markdown_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    if marker not in text:
        return ""
    section = text.split(marker, 1)[1]
    if "\n## " in section:
        section = section.split("\n## ", 1)[0]
    return section
