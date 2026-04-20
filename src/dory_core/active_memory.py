from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol

from dory_core.retrieval_planner import (
    ActiveMemoryComposer,
    ActiveMemoryPlanningContext,
    ActiveMemoryPlanner,
    ActiveMemoryRetrievalPlan,
    fallback_active_memory_plan,
)
from dory_core.types import ActiveMemoryReq, ActiveMemoryResp, SearchCorpus, SearchMode, SearchReq, WakeReq, WakeResp

_COMPOSER_SNIPPET_CHARS = 360
_SESSION_COMPOSER_SNIPPET_CHARS = 180
_RENDER_SNIPPET_CHARS = 300
_SESSION_RENDER_SNIPPET_CHARS = 160
_MEMORY_BULLET_CHARS = 220
_CHARS_PER_TOKEN = 4
_MAX_BLOCK_CHARS = 3200
_MIN_BLOCK_CHARS = 700
_PLANNER_MIN_REMAINING_MS = 1800
_COMPOSER_MIN_REMAINING_MS = 2200
_PromptContext = Literal["coding", "writing", "privacy", "personal", "general"]
_ActiveMemoryProfile = Literal["general", "coding", "writing", "privacy", "personal"]


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    profile: _ActiveMemoryProfile
    wake_profile: Literal["default", "coding", "writing", "privacy"]
    include_pinned_decisions: bool
    include_durable_context: bool
    include_session_context: bool
    use_helper_context: bool
    blocked_exact_paths: frozenset[str] = frozenset()
    blocked_path_prefixes: tuple[str, ...] = ()

    def allows_result_path(self, path: str, *, corpus: str) -> bool:
        if corpus == "sessions":
            return self.include_session_context
        if not self.include_durable_context:
            return False
        if path in self.blocked_exact_paths:
            return False
        return not path.startswith(self.blocked_path_prefixes)


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
        source_policy = _source_policy_for_request(req)
        helper = _load_wiki_helper_context(self.root) if source_policy.use_helper_context else _empty_wiki_helper_context()
        wake_block = ""
        wake_sources: list[str] = []
        if req.include_wake:
            wake = self.wake_builder.build(
                WakeReq(
                    budget_tokens=min(req.budget_tokens, 600),
                    agent=req.agent,
                    profile=source_policy.wake_profile,
                    include_recent_sessions=3 if source_policy.include_session_context else 0,
                    include_pinned_decisions=source_policy.include_pinned_decisions,
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
        durable_results = _filter_active_memory_results(
            durable_results,
            corpus="durable",
            source_policy=source_policy,
        )
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
            if source_policy.include_session_context and plan.include_sessions and plan.session_limit > 0
            else []
        )
        session_results = _filter_active_memory_results(
            session_results,
            corpus="sessions",
            source_policy=source_policy,
        )
        rendered_wake_block = _wake_block_for_rendering(wake_block, durable_results, session_results)
        sources = _dedupe_strings(
            [
                *(wake_sources if rendered_wake_block else []),
                *[_result_path(item) for item in durable_results[:4]],
                *[_result_path(item) for item in session_results[:3]],
            ]
        )
        composition = self._compose(
            req, planning_context, wake_block, durable_results, session_results, deadline=deadline
        )
        if _composition_conflicts_with_evidence(composition, durable_results):
            composition = None
        synthesized_bullets = _synthesized_bullets(helper, durable_results, session_results, root=self.root)
        memory_bullets = (
            list(composition.bullets) if composition is not None and composition.bullets else synthesized_bullets
        )
        summary = (
            composition.summary
            if composition is not None and composition.summary
            else _build_summary(helper, durable_results, session_results, wake_block, root=self.root)
        )
        block = _build_block(
            helper,
            rendered_wake_block,
            durable_results,
            session_results,
            memory_bullets=memory_bullets,
            budget_tokens=req.budget_tokens,
            root=self.root,
        )
        confidence = _confidence_for_results(durable_results, session_results)
        return ActiveMemoryResp(
            kind="memory" if block else "none",
            block=block,
            summary=summary,
            profile=source_policy.profile,
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
        if self.planner is None or deadline.remaining_ms < _PLANNER_MIN_REMAINING_MS:
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
        if not durable_results and not session_results:
            return None
        if self.composer is None or deadline.remaining_ms < _COMPOSER_MIN_REMAINING_MS:
            return None
        try:
            return self.composer.compose_active_memory(
                prompt=req.prompt,
                context=context,
                wake_summary=_first_non_empty_line(wake_block),
                durable_results=tuple(
                    (
                        _result_path(item),
                        _truncate_text(_result_evidence_text(item, root=self.root), _COMPOSER_SNIPPET_CHARS),
                    )
                    for item in durable_results[:4]
                ),
                session_results=tuple(
                    (
                        _result_path(item),
                        _truncate_text(_result_evidence_text(item, root=self.root), _SESSION_COMPOSER_SNIPPET_CHARS),
                    )
                    for item in session_results[:2]
                ),
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

    @property
    def remaining_ms(self) -> int:
        return max(0, int((self.expires_at - monotonic()) * 1000))


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
    *,
    root: Path | None,
) -> str:
    bullets = _synthesized_bullets(helper, durable_results, session_results, root=root)
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
    budget_tokens: int,
    root: Path | None,
) -> str:
    active_memory_section = _render_active_memory_section(
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
            _render_results_section(
                "Durable evidence",
                durable_results,
                max_results=3,
                snippet_chars=_RENDER_SNIPPET_CHARS,
                root=root,
            ),
            _render_results_section(
                "Session evidence",
                session_results,
                max_results=2,
                snippet_chars=_SESSION_RENDER_SNIPPET_CHARS,
                root=root,
            ),
        ]
        if section
    ]
    return _fit_block_to_budget("\n\n".join(sections).strip(), budget_tokens=budget_tokens)


def _render_results_section(
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
        path = _result_path(result)
        if not path:
            continue
        snippet = _result_evidence_text(result, root=root)
        lines.append(f"- {path}")
        if snippet:
            lines.append(f"  {_truncate_text(snippet, snippet_chars)}")
    return "\n".join(lines)


def _confidence_for_results(
    durable_results: list[object], session_results: list[object]
) -> Literal["low", "medium", "high"] | None:
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
    mode: SearchMode,
    corpus: SearchCorpus,
    include_content: bool,
    rerank: Literal["auto", "true", "false"],
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
            normalized_score = getattr(result, "score_normalized", None)
            base_score = float(normalized_score) if isinstance(normalized_score, (int, float)) else raw_score
            score = base_score + _active_memory_path_weight(path) - (query_index * 0.06) - (result_index * 0.01)
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


def _filter_active_memory_results(
    results: list[object],
    *,
    corpus: str,
    source_policy: SourcePolicy,
) -> list[object]:
    return [
        result
        for result in results
        if _is_active_memory_candidate(result, corpus=corpus)
        and source_policy.allows_result_path(_result_path(result), corpus=corpus)
    ]


def _is_active_memory_candidate(result: object, *, corpus: str) -> bool:
    path = _result_path(result)
    if not path or path.endswith(".tombstone.md"):
        return False
    if corpus == "durable" and path.startswith("logs/sessions/"):
        return False
    if corpus == "durable" and path.startswith("wiki/"):
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


def _source_policy_for_request(req: ActiveMemoryReq) -> SourcePolicy:
    profile = _resolve_active_memory_profile(req)
    include_session_context = _prompt_needs_session_context(req.prompt)
    if profile == "privacy":
        return SourcePolicy(
            profile="privacy",
            wake_profile="privacy",
            include_pinned_decisions=False,
            include_durable_context=False,
            include_session_context=False,
            use_helper_context=False,
        )
    if profile == "coding":
        return SourcePolicy(
            profile="coding",
            wake_profile="coding",
            include_pinned_decisions=False,
            include_durable_context=True,
            include_session_context=include_session_context,
            use_helper_context=True,
            blocked_exact_paths=frozenset({"core/user.md", "core/soul.md", "core/identity.md"}),
            blocked_path_prefixes=("people/", "knowledge/personal/"),
        )
    if profile == "writing":
        return SourcePolicy(
            profile="writing",
            wake_profile="writing",
            include_pinned_decisions=True,
            include_durable_context=True,
            include_session_context=include_session_context,
            use_helper_context=True,
            blocked_exact_paths=frozenset({"core/user.md", "core/identity.md"}),
            blocked_path_prefixes=("people/",),
        )
    if profile == "personal":
        return SourcePolicy(
            profile="personal",
            wake_profile="default",
            include_pinned_decisions=True,
            include_durable_context=True,
            include_session_context=include_session_context,
            use_helper_context=False,
        )
    return SourcePolicy(
        profile="general",
        wake_profile="default",
        include_pinned_decisions=True,
        include_durable_context=True,
        include_session_context=include_session_context,
        use_helper_context=True,
    )


def _resolve_active_memory_profile(req: ActiveMemoryReq) -> _ActiveMemoryProfile:
    if req.profile != "auto":
        return req.profile
    return _prompt_context(req.prompt)


def _prompt_context(prompt: str) -> _PromptContext:
    lowered = prompt.casefold()
    if _contains_any(
        lowered,
        (
            "privacy",
            "private",
            "sensitive",
            "boundary",
            "boundaries",
            "redact",
            "public-safe",
            "do not share",
        ),
    ):
        return "privacy"
    if _contains_any(
        lowered,
        (
            "who am i",
            "about me",
            "my profile",
            "personal",
            "preference",
            "preferences",
            "how should you talk to me",
        ),
    ):
        return "personal"
    if _contains_any(
        lowered,
        (
            "writing",
            "voice",
            "draft",
            "copy",
            "post",
            "essay",
            "tone",
            "style",
            "blog",
        ),
    ):
        return "writing"
    if _contains_any(
        lowered,
        (
            "code",
            "coding",
            "repo",
            "implementation",
            "bug",
            "test",
            "tests",
            "api",
            "integration",
            "integrations",
            "schema",
            "mcp",
            "module",
        ),
    ):
        return "coding"
    return "general"


def _prompt_needs_session_context(prompt: str) -> bool:
    lowered = prompt.casefold()
    return _contains_any(
        lowered,
        (
            "last worked",
            "worked on last",
            "what did i work",
            "recent session",
            "latest session",
            "previous session",
            "session context",
            "conversation",
            "yesterday",
            "today",
            "this morning",
            "last night",
        ),
    )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _wake_block_for_rendering(wake_block: str, durable_results: list[object], session_results: list[object]) -> str:
    if durable_results or session_results:
        return ""
    return wake_block.strip()


def _active_memory_path_weight(path: str) -> float:
    if path == "core/active.md":
        return 0.9
    if path in {"core/env.md", "core/defaults.md", "core/user.md", "core/soul.md"}:
        return 0.45
    if path.startswith("projects/") and path.endswith("/state.md"):
        return 0.65
    if path.startswith("decisions/canonical/"):
        return 0.5
    if path.startswith("people/"):
        return 0.25
    if path.startswith("knowledge/"):
        return 0.15
    if path.startswith("wiki/"):
        return -1.0
    if path.startswith("logs/"):
        return -0.8
    if path.startswith(("inbox/", "archive/")):
        return -0.5
    return 0.0


def _result_frontmatter(result: object) -> dict[str, object]:
    frontmatter = getattr(result, "frontmatter", {})
    return frontmatter if isinstance(frontmatter, dict) else {}


def _load_wiki_helper_context(root: Path | None) -> WikiHelperContext:
    if root is None:
        return _empty_wiki_helper_context()
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


def _empty_wiki_helper_context() -> WikiHelperContext:
    return WikiHelperContext(
        block="",
        sources=[],
        current_focus="",
        recent_pages=(),
        active_threads=(),
        index_hints=(),
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
    root: Path | None,
) -> str:
    bullets = memory_bullets or _synthesized_bullets(helper, durable_results, session_results, root=root)
    if not bullets:
        return ""
    lines = ["## Active memory"]
    lines.extend(f"- {bullet}" for bullet in bullets[:5])
    return "\n".join(lines)


def _synthesized_bullets(
    helper: WikiHelperContext,
    durable_results: list[object],
    session_results: list[object],
    *,
    root: Path | None,
) -> list[str]:
    candidates: list[MemoryCandidate] = []
    if helper.current_focus:
        candidates.append(MemoryCandidate(text=_truncate_text(helper.current_focus, _MEMORY_BULLET_CHARS), weight=6.0))
    for position, item in enumerate(helper.recent_pages[:3], start=1):
        candidates.append(
            MemoryCandidate(text=_truncate_text(item, _MEMORY_BULLET_CHARS), weight=4.5 - (position * 0.3))
        )
    for position, item in enumerate(helper.active_threads[:2], start=1):
        candidates.append(
            MemoryCandidate(text=_truncate_text(item, _MEMORY_BULLET_CHARS), weight=3.4 - (position * 0.2))
        )
    for position, item in enumerate(helper.index_hints[:2], start=1):
        candidates.append(
            MemoryCandidate(text=_truncate_text(item, _MEMORY_BULLET_CHARS), weight=3.0 - (position * 0.2))
        )
    for position, result in enumerate(durable_results[:4], start=1):
        snippet = _truncate_text(_result_evidence_text(result, root=root), _MEMORY_BULLET_CHARS)
        if not snippet:
            continue
        score = float(getattr(result, "score", 0.0) or 0.0)
        candidates.append(MemoryCandidate(text=snippet, weight=5.0 + score - (position * 0.25)))
    for position, result in enumerate(session_results[:3], start=1):
        snippet = _truncate_text(_result_evidence_text(result, root=root), _MEMORY_BULLET_CHARS)
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


def _clean_helper_items(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(cleaned for item in items if (cleaned := _clean_helper_item(item)))


def _clean_helper_item(item: str) -> str:
    cleaned = _clean_markdown_content_line(item)
    if not cleaned:
        return ""
    return _truncate_text(cleaned, _MEMORY_BULLET_CHARS)


def _extract_markdown_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    if marker not in text:
        return ""
    section = text.split(marker, 1)[1]
    if "\n## " in section:
        section = section.split("\n## ", 1)[0]
    return section


def _truncate_text(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _result_evidence_text(result: object, *, root: Path | None) -> str:
    path = _result_path(result)
    if root is not None and path:
        excerpt = _canonical_file_excerpt(root, path)
        if excerpt:
            return excerpt
    return _safe_evidence_text(_result_snippet(result))


def _canonical_file_excerpt(root: Path, rel_path: str) -> str:
    if rel_path.startswith(("logs/", "inbox/", "archive/", "wiki/")):
        return ""
    try:
        path = (root / rel_path).resolve()
        root_resolved = root.resolve()
        path.relative_to(root_resolved)
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    body = _strip_frontmatter(text)
    for heading in ("Summary", "Current Focus", "Current State", "Open Work", "Topology", "Defaults"):
        excerpt = _section_list_excerpt(body, heading)
        if excerpt:
            return excerpt
    return _first_content_excerpt(body)


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    parts = text.split("\n---\n", 1)
    return parts[1] if len(parts) == 2 else text


def _section_list_excerpt(text: str, heading: str) -> str:
    section = _extract_markdown_section(text, heading)
    if not section:
        return ""
    items: list[str] = []
    for raw_line in section.splitlines():
        line = _clean_markdown_content_line(raw_line)
        if not line:
            continue
        items.append(line)
        if len(items) >= 2:
            break
    return _truncate_text(" ".join(items), _COMPOSER_SNIPPET_CHARS)


def _first_content_excerpt(text: str) -> str:
    items: list[str] = []
    for raw_line in text.splitlines():
        line = _clean_markdown_content_line(raw_line)
        if not line:
            continue
        items.append(line)
        if len(items) >= 2:
            break
    return _truncate_text(" ".join(items), _COMPOSER_SNIPPET_CHARS)


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


def _safe_evidence_text(text: str) -> str:
    normalized_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        lowered = line.lower()
        if lowered.startswith(("system:", "developer:", "assistant:", "tool:")):
            continue
        if lowered.startswith("user:"):
            line = line[5:].strip()
        normalized_lines.append(line)
    return " ".join(" ".join(normalized_lines).split())


def _fit_block_to_budget(block: str, *, budget_tokens: int) -> str:
    char_limit = min(_MAX_BLOCK_CHARS, max(_MIN_BLOCK_CHARS, budget_tokens * _CHARS_PER_TOKEN))
    if len(block) <= char_limit:
        return block
    truncated = block[: max(0, char_limit - 1)].rstrip()
    if "\n## " in truncated:
        section_safe = truncated.rsplit("\n## ", 1)[0].rstrip()
        if len(section_safe) >= _MIN_BLOCK_CHARS:
            return section_safe
    return truncated + "…"
