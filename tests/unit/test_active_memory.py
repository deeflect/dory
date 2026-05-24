from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

import pytest

from dory_core.active_memory import ActiveMemoryEngine, BudgetConfig
from dory_core.retrieval_planner import ActiveMemoryComposition, ActiveMemoryPlanningContext, ActiveMemoryRetrievalPlan
from dory_core.types import ActiveMemoryReq, SearchReq, SearchScope, WakeReq, WakeResp
from dory_core.wake import WakeBuilder


class _StubSearchEngine:
    def __init__(self) -> None:
        self.requests: list[SearchReq] = []

    def search(self, req: SearchReq):  # pragma: no cover - test stub
        self.requests.append(req)
        if req.corpus == "sessions":
            return _make_response(
                [
                    _make_result(
                        path="logs/sessions/claude/macbook/2026-04-12-s1.md",
                        snippet="Session note: Sample is still the active focus.",
                        score=0.61,
                    )
                ]
            )
        return _make_response(
            [
                _make_result(
                    path="core/active.md",
                    snippet="Sample is the active focus this week.",
                    score=0.92,
                )
            ]
        )


class _StubActiveMemoryPlanner:
    def plan_active_memory(
        self,
        *,
        prompt: str,
        context: ActiveMemoryPlanningContext,
    ) -> ActiveMemoryRetrievalPlan:
        del prompt, context
        return ActiveMemoryRetrievalPlan(
            durable_queries=("sample active focus", "sample pricing"),
            session_queries=("sample follow-up",),
            include_sessions=True,
            durable_limit=4,
            session_limit=2,
        )


class _StubActiveMemoryComposer:
    def compose_active_memory(
        self,
        *,
        prompt: str,
        context: ActiveMemoryPlanningContext,
        wake_summary: str,
        durable_results: tuple[tuple[str, str], ...],
        session_results: tuple[tuple[str, str], ...],
    ) -> ActiveMemoryComposition:
        del prompt, context, wake_summary, durable_results, session_results
        return ActiveMemoryComposition(
            summary="Sample remains the active focus.",
            bullets=(
                "Sample remains the active focus.",
                "Pricing follow-up is still active in the latest session.",
            ),
        )


class _CountingWakeBuilder:
    def __init__(self) -> None:
        self.requests: list[WakeReq] = []

    def build(self, req: WakeReq) -> WakeResp:  # pragma: no cover - test stub
        self.requests.append(req)
        return WakeResp(
            profile=req.profile,
            tokens_estimated=5,
            block="wake block should be omitted when include_wake is false",
            sources=["core/user.md"],
            frozen_at=datetime.now(tz=UTC),
        )


def _make_result(
    *,
    path: str,
    snippet: str,
    score: float,
    frontmatter: dict[str, object] | None = None,
    stale_warning: str | None = None,
    confidence: str | None = None,
):
    return type(
        "Result",
        (),
        {
            "path": path,
            "lines": "1:4",
            "score": score,
            "snippet": snippet,
            "frontmatter": frontmatter or {},
            "stale_warning": stale_warning,
            "confidence": confidence,
        },
    )()


def _make_response(results: list[object]):
    return type("Resp", (), {"results": results})()


def test_active_memory_runs_for_explicit_call_even_on_non_memory_prompt(tmp_path: Path) -> None:
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=_StubSearchEngine(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="format this file",
            agent="claude",
        )
    )

    assert result.kind == "memory"
    assert result.took_ms >= 1
    assert "## Durable evidence" in result.block
    assert "core/active.md" in result.sources


def test_active_memory_returns_none_when_no_context_or_results(tmp_path: Path) -> None:
    class EmptySearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            del req
            return _make_response([])

    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=EmptySearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what context matters for the empty test corpus?",
            agent="codex",
            include_wake=False,
        )
    )

    assert result.kind == "none"
    assert result.block == ""
    assert result.summary == ""
    assert result.sources == []
    assert result.partial is False


def test_active_memory_builds_memory_block_for_state_question(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text(
        "Sample is the active focus this week.\n",
        encoding="utf-8",
    )
    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            cwd=str(tmp_path),
            timeout_ms=7000,
        )
    )

    assert result.kind == "memory"
    assert "## Active memory" in result.block
    assert "## Durable evidence" in result.block
    assert "## Session evidence" in result.block
    assert "core/active.md" in result.block
    assert "logs/sessions/claude/macbook/2026-04-12-s1.md" in result.block
    assert "core/active.md" in result.sources
    assert "logs/sessions/claude/macbook/2026-04-12-s1.md" in result.sources
    assert "Sample is the active focus this week." in result.summary
    assert "Session note: Sample is still the active focus." in result.summary
    assert "Session note: Sample is still the active focus." in result.block
    assert search_engine.requests[0].corpus == "durable"
    assert search_engine.requests[0].include_content is True
    assert search_engine.requests[0].rerank == "true"
    assert search_engine.requests[1].corpus == "sessions"
    assert search_engine.requests[1].include_content is False
    assert search_engine.requests[1].rerank == "false"


def test_active_memory_applies_scope_to_session_recall_only(tmp_path: Path) -> None:
    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
    )
    example_session = "codex-session-1"

    engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="codex",
            include_wake=False,
            scope=SearchScope(session_key=example_session, agent=["codex"], status=["active"]),
        )
    )

    durable_req = search_engine.requests[0]
    session_req = search_engine.requests[1]
    assert durable_req.corpus == "durable"
    assert durable_req.scope.session_key is None
    assert durable_req.scope.agent == []
    assert session_req.corpus == "sessions"
    assert session_req.scope.session_key == example_session
    assert session_req.scope.agent == ["codex"]
    assert session_req.scope.status == ["active"]


def test_active_memory_can_skip_wake_after_session_wake_was_loaded(tmp_path: Path) -> None:
    search_engine = _StubSearchEngine()
    wake_builder = _CountingWakeBuilder()
    engine = ActiveMemoryEngine(
        wake_builder=wake_builder,
        search_engine=search_engine,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
        )
    )

    assert wake_builder.requests == []
    assert "wake block should be omitted" not in result.block
    assert "core/user.md" not in result.sources
    assert "core/active.md" in result.sources


def test_active_memory_infers_project_from_cwd_and_includes_project_state(tmp_path: Path) -> None:
    class EmptySearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            del req
            return _make_response([])

    corpus_root = tmp_path / "corpus"
    workspace = tmp_path / "workspace"
    (workspace).mkdir()
    (workspace / "pyproject.toml").write_text('[project]\nname = "dory"\n', encoding="utf-8")
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text("Dory is active.\n", encoding="utf-8")
    (corpus_root / "projects" / "dory").mkdir(parents=True)
    (corpus_root / "projects" / "dory" / "state.md").write_text(
        """---
title: Dory
type: project
status: active
canonical: true
---

## Summary
- Dory is the shared memory substrate for agents.
""",
        encoding="utf-8",
    )
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=corpus_root),
        search_engine=EmptySearchEngine(),
        root=corpus_root,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what is the current memory work",
            agent="codex",
            cwd=str(workspace),
            include_wake=False,
        )
    )

    assert "projects/dory/state.md" in result.sources
    assert "Dory is the shared memory substrate for agents." in result.block


def test_active_memory_filters_low_trust_durable_evidence(tmp_path: Path) -> None:
    class NoisySearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            if req.corpus == "sessions":
                return _make_response([])
            return _make_response(
                [
                    _make_result(
                        path="logs/sessions/2026-04-14-identity-query.md",
                        snippet="Low-signal identity session.",
                        score=0.99,
                        confidence="high",
                    ),
                    _make_result(
                        path="projects/dory/state.md",
                        snippet="Dory is the active memory project.",
                        score=0.7,
                        confidence="high",
                    ),
                    _make_result(
                        path="projects/stale/state.md",
                        snippet="Stale project note.",
                        score=0.6,
                        confidence="high",
                        stale_warning="Timeline may be stale.",
                    ),
                ]
            )

    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=NoisySearchEngine(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
        )
    )

    assert "projects/dory/state.md" in result.sources
    assert "logs/sessions/2026-04-14-identity-query.md" not in result.sources
    assert "projects/stale/state.md" not in result.sources
    assert "Low-signal identity session." not in result.block
    assert "Stale project note." not in result.block


def test_active_memory_coding_prompt_excludes_personal_wake_and_search_hits(tmp_path: Path) -> None:
    class PersonalHeavySearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            if req.corpus == "sessions":
                return _make_response([])
            return _make_response(
                [
                    _make_result(
                        path="core/user.md",
                        snippet="Email placeholder@example.invalid and sensitive placeholder context.",
                        score=0.99,
                        confidence="high",
                    ),
                    _make_result(
                        path="projects/dory/state.md",
                        snippet="Dory agent integrations need active-memory hardening.",
                        score=0.7,
                        confidence="high",
                    ),
                ]
            )

    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text("Dory is active.\n", encoding="utf-8")
    (tmp_path / "core" / "env.md").write_text("Dory runs on the shared service.\n", encoding="utf-8")
    (tmp_path / "core" / "defaults.md").write_text("Use Python and pytest.\n", encoding="utf-8")
    (tmp_path / "core" / "user.md").write_text("Email placeholder@example.invalid.\n", encoding="utf-8")
    (tmp_path / "core" / "soul.md").write_text("Voice details.\n", encoding="utf-8")
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=PersonalHeavySearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="Before answering a coding question about Dory agent integrations, retrieve only the memory that matters.",
            agent="codex",
            include_wake=True,
            timeout_ms=7000,
        )
    )

    assert "projects/dory/state.md" in result.sources
    assert "core/user.md" not in result.sources
    assert "placeholder@example.invalid" not in result.block
    assert "sensitive placeholder context" not in result.block
    assert "Dory agent integrations need active-memory hardening." in result.block


def test_active_memory_explicit_privacy_profile_overrides_prompt_heuristics(tmp_path: Path) -> None:
    class MixedSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            if req.corpus == "sessions":
                return _make_response(
                    [
                        _make_result(
                            path="logs/sessions/codex/2026-04-20.md",
                            snippet="Session detail that should not enter privacy profile memory.",
                            score=0.9,
                        )
                    ]
                )
            return _make_response(
                [
                    _make_result(
                        path="core/identity.md",
                        snippet="Placeholder identifier context.",
                        score=0.9,
                        confidence="high",
                    ),
                    _make_result(
                        path="core/defaults.md",
                        snippet="Privacy requests should use boundary-only context.",
                        score=0.7,
                        confidence="high",
                    ),
                ]
            )

    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "user.md").write_text(
        "## Privacy Boundaries\n- Keep placeholder identifiers private.\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "identity.md").write_text("Email placeholder@example.invalid.\n", encoding="utf-8")
    (tmp_path / "core" / "defaults.md").write_text("Use boundary-only context.\n", encoding="utf-8")
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=MixedSearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="coding integration question from a recent session",
            agent="codex",
            profile="privacy",
            include_wake=True,
        )
    )

    assert result.profile == "privacy"
    assert "core/user.md" in result.sources
    assert "core/identity.md" not in result.sources
    assert "logs/sessions/codex/2026-04-20.md" not in result.sources
    assert "## Durable evidence" not in result.block
    assert result.summary.startswith("# Privacy Boundaries")
    assert "placeholder@example.invalid" not in result.block
    assert "Session detail" not in result.block


def test_active_memory_uses_wiki_as_helper_not_durable_evidence(tmp_path: Path) -> None:
    class WikiHeavySearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            if req.corpus == "sessions":
                return _make_response([])
            return _make_response(
                [
                    _make_result(
                        path="wiki/hot.md",
                        snippet="Generated wiki cache should not be rendered as durable evidence.",
                        score=0.99,
                    ),
                    _make_result(
                        path="projects/dory/state.md",
                        snippet="Dory active-memory local LLM work is current.",
                        score=0.4,
                    ),
                ]
            )

    (tmp_path / "wiki").mkdir(parents=True)
    (tmp_path / "wiki" / "hot.md").write_text(
        "# Hot\n\n## Current Focus\n- Dory active-memory tuning.\n",
        encoding="utf-8",
    )
    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=WikiHeavySearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what matters for Dory active memory?",
            agent="codex",
            include_wake=False,
        )
    )

    assert "wiki/hot.md" not in result.sources
    assert "projects/dory/state.md" in result.sources
    assert "Generated wiki cache should not be rendered" not in result.block
    assert "projects/dory/state.md" in result.block


def test_active_memory_truncates_large_snippets_for_bounded_blocks(tmp_path: Path) -> None:
    class LongSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            del req
            return _make_response(
                [
                    _make_result(
                        path="projects/dory/state.md",
                        snippet="Important current Dory detail. " + ("extra context " * 80),
                        score=0.9,
                    )
                ]
            )

    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=LongSearchEngine(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what matters for Dory?",
            agent="codex",
            include_wake=False,
        )
    )

    assert "Important current Dory detail." in result.block
    assert (
        "extra context extra context extra context extra context extra context extra context extra context"
        in result.block
    )
    assert len(result.block) < 1400


def test_active_memory_combines_focused_snippet_with_canonical_excerpt(tmp_path: Path) -> None:
    class FocusedSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            del req
            return _make_response(
                [
                    _make_result(
                        path="projects/dory/state.md",
                        snippet="Docker MCP setup fails when the daemon URL is stale.",
                        score=0.9,
                        confidence="high",
                    )
                ]
            )

    (tmp_path / "projects" / "dory").mkdir(parents=True)
    (tmp_path / "projects" / "dory" / "state.md").write_text(
        """---
title: Dory
type: project
status: active
canonical: true
---

# Dory

## Current State

- Dory runs a shared MCP and HTTP memory service.
- Search and active-memory use the same runtime.
""",
        encoding="utf-8",
    )
    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=FocusedSearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="debug Dory Docker MCP setup",
            agent="codex",
            include_wake=False,
            timeout_ms=7000,
        )
    )

    assert "Docker MCP setup fails when the daemon URL is stale." in result.block
    assert "Dory runs a shared MCP and HTTP memory service." in result.block


def test_active_memory_triggers_for_recent_work_question(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text(
        "Sample is the active focus this week.\n",
        encoding="utf-8",
    )
    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what did I work on last?",
            agent="claude",
            cwd=str(tmp_path),
        )
    )

    assert result.kind == "memory"
    assert len(search_engine.requests) == 2


def test_active_memory_reads_wiki_hot_and_index_first(tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir(parents=True)
    (tmp_path / "wiki" / "hot.md").write_text(
        "---\ntitle: Hot Cache\n---\n\n# Recent Context\n\n## Summary\nSample remains the active focus.\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "index.md").write_text(
        "---\ntitle: Wiki\n---\n\n# Wiki\n\n## Summary\nCompiled wiki entry point.\n",
        encoding="utf-8",
    )
    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            cwd=str(tmp_path),
        )
    )

    assert "wiki/hot.md" not in result.sources
    assert "wiki/index.md" not in result.sources
    assert "## Active memory" in result.block
    assert "## Hot Cache" not in result.block
    assert "## Wiki Index" not in result.block


def test_active_memory_synthesizes_current_focus_and_evidence(tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir(parents=True)
    (tmp_path / "wiki" / "hot.md").write_text(
        "---\n"
        "title: Hot Cache\n"
        "---\n\n"
        "# Recent Context\n\n"
        "## Summary\n"
        "Sample remains the active focus.\n\n"
        "## Current Focus\n"
        "- Sample remains the active focus.\n\n"
        "## Active Threads\n"
        "- logs/sessions/claude/macbook/2026-04-12-s1.md: pricing follow-up\n",
        encoding="utf-8",
    )
    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            cwd=str(tmp_path),
            timeout_ms=5000,
        )
    )

    assert result.summary.startswith("Sample remains the active focus.")
    assert "- Session note: Sample is still the active focus." in result.block


def test_active_memory_filters_unrelated_helper_context_for_coding_prompts(tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir(parents=True)
    (tmp_path / "wiki" / "hot.md").write_text(
        "---\n"
        "title: Hot Cache\n"
        "---\n\n"
        "# Recent Context\n\n"
        "## Summary\n"
        "Marketing launch notes are recent.\n\n"
        "## Current Focus\n"
        "- X Growth System copy experiments.\n\n"
        "## Recent Pages\n"
        "- CCC writing calendar and tweet hooks.\n\n"
        "## Active Threads\n"
        "- Newsletter positioning and launch copy.\n",
        encoding="utf-8",
    )

    class DorySearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            del req
            return _make_response(
                [
                    _make_result(
                        path="projects/dory/state.md",
                        snippet="Dory Docker MCP deployment needs canonical search and active-memory fixes.",
                        score=0.9,
                        frontmatter={"type": "project", "status": "active", "canonical": True},
                    )
                ]
            )

    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=DorySearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="Fix Dory Docker MCP active memory issue",
            agent="codex",
            profile="coding",
            include_wake=False,
        )
    )

    assert "Dory Docker MCP deployment" in result.block
    assert "X Growth System" not in result.block
    assert "tweet hooks" not in result.block
    assert "Newsletter positioning" not in result.block


def test_active_memory_coding_profile_filters_denied_paths_before_top_k_truncation(tmp_path: Path) -> None:
    class WindowedSearchEngine:
        def __init__(self) -> None:
            self.requests: list[SearchReq] = []

        def search(self, req: SearchReq):  # pragma: no cover - test stub
            self.requests.append(req)
            denied_people = [
                _make_result(
                    path=f"people/demo-{index}.md",
                    snippet=f"Personal note {index} should not enter coding memory.",
                    score=1.0 - (index * 0.01),
                    confidence="high",
                )
                for index in range(8)
            ]
            denied_voice = _make_result(
                path="knowledge/personal/writing-voice.md",
                snippet="Personal writing voice should not enter coding memory.",
                score=0.9,
                confidence="high",
            )
            allowed_project = _make_result(
                path="projects/dory/state.md",
                snippet="Dory coding context survives profile filtering.",
                score=0.2,
                confidence="high",
            )
            return _make_response([*denied_people, denied_voice, allowed_project][: req.k])

    search_engine = WindowedSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="fix Dory coding tests",
            agent="codex",
            profile="coding",
            include_wake=False,
        )
    )

    assert search_engine.requests[0].k > 6
    assert "Dory coding context survives profile filtering." in result.block
    assert "Personal note" not in result.block
    assert "Personal writing voice" not in result.block
    assert result.sources == ["projects/dory/state.md"]


def test_active_memory_custom_profile_applies_wake_and_retrieval_policy(tmp_path: Path) -> None:
    class MixedSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            del req
            return _make_response(
                [
                    _make_result(
                        path="projects/dory/state.md",
                        snippet="Dory admin ops should stay out of brand work.",
                        score=0.99,
                        confidence="high",
                    ),
                    _make_result(
                        path="profiles/brand/default.md",
                        snippet="Brand profile says use artifact-led written launches.",
                        score=0.4,
                        confidence="high",
                    ),
                ]
            )

    (tmp_path / "profiles" / "brand").mkdir(parents=True)
    (tmp_path / "projects" / "dory").mkdir(parents=True)
    (tmp_path / "profiles" / "brand" / "active.md").write_text("Brand active wake context.\n", encoding="utf-8")
    (tmp_path / "profiles" / "brand" / "default.md").write_text("Brand defaults.\n", encoding="utf-8")
    (tmp_path / "projects" / "dory" / "state.md").write_text("Dory operations.\n", encoding="utf-8")
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  brand:
    wake:
      sections:
        - profiles/brand/active.md
    retrieval:
      allow:
        - profiles/brand/**
      deny:
        - projects/dory/**
      boosts:
        profiles/brand/**: 0.8
      sessions: never
""".strip(),
        encoding="utf-8",
    )

    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=MixedSearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="draft brand launch copy",
            agent="codex",
            profile="brand",
            include_wake=True,
        )
    )

    assert result.profile == "brand"
    assert "profiles/brand/default.md" in result.sources
    assert "projects/dory/state.md" not in result.sources
    assert "Brand profile says use artifact-led written launches." in result.block
    assert "Dory admin ops" not in result.block


def test_active_memory_custom_profile_filters_before_top_k_truncation(tmp_path: Path) -> None:
    class WindowedSearchEngine:
        def __init__(self) -> None:
            self.requests: list[SearchReq] = []

        def search(self, req: SearchReq):  # pragma: no cover - test stub
            self.requests.append(req)
            disallowed = [
                _make_result(
                    path=f"projects/dory/high-score-{index}.md",
                    snippet=f"Disallowed project result {index}.",
                    score=1.0 - (index * 0.01),
                    confidence="high",
                )
                for index in range(10)
            ]
            allowed = _make_result(
                path="profiles/brand/default.md",
                snippet="Allowed brand profile context survives filtering.",
                score=0.1,
                confidence="high",
            )
            return _make_response([*disallowed, allowed][: req.k])

    (tmp_path / "profiles" / "brand").mkdir(parents=True)
    (tmp_path / "profiles" / "brand" / "default.md").write_text("Brand defaults.\n", encoding="utf-8")
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  brand:
    retrieval:
      allow:
        - profiles/brand/**
      sessions: never
""".strip(),
        encoding="utf-8",
    )
    search_engine = WindowedSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="draft brand launch copy",
            agent="codex",
            profile="brand",
            include_wake=False,
        )
    )

    assert search_engine.requests[0].k > 6
    assert "Allowed brand profile context survives filtering." in result.block
    assert "Disallowed project result" not in result.block


def test_active_memory_uses_planner_queries_and_llm_composition_when_budget_allows(tmp_path: Path) -> None:
    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        planner=_StubActiveMemoryPlanner(),
        composer=_StubActiveMemoryComposer(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            cwd=str(tmp_path),
            timeout_ms=7000,
        )
    )

    assert result.summary == "Sample remains the active focus."
    assert "- Pricing follow-up is still active in the latest session." in result.block
    assert search_engine.requests[0].query == "sample active focus"
    assert search_engine.requests[1].query == "sample pricing"
    assert search_engine.requests[2].query == "sample follow-up"


def test_active_memory_logs_planner_failure_and_uses_fallback(tmp_path: Path, caplog) -> None:
    class ExplodingPlanner:
        def plan_active_memory(
            self,
            *,
            prompt: str,
            context: ActiveMemoryPlanningContext,
        ) -> ActiveMemoryRetrievalPlan:
            del prompt, context
            raise RuntimeError("planner unavailable")

    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        planner=ExplodingPlanner(),
    )
    caplog.set_level("ERROR", logger="dory_core.active_memory")

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
            timeout_ms=5000,
        )
    )

    assert result.kind == "memory"
    assert search_engine.requests[0].query == "what are we working on today"
    assert "active-memory planner failed; using deterministic fallback plan" in caplog.text


def test_active_memory_logs_composer_failure_and_uses_synthesis(tmp_path: Path, caplog) -> None:
    class ExplodingComposer:
        def compose_active_memory(
            self,
            *,
            prompt: str,
            context: ActiveMemoryPlanningContext,
            wake_summary: str,
            durable_results: tuple[tuple[str, str], ...],
            session_results: tuple[tuple[str, str], ...],
        ) -> ActiveMemoryComposition:
            del prompt, context, wake_summary, durable_results, session_results
            raise RuntimeError("composer unavailable")

    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        composer=ExplodingComposer(),
    )
    caplog.set_level("ERROR", logger="dory_core.active_memory")

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
            timeout_ms=7000,
        )
    )

    assert result.summary.startswith("Sample is the active focus this week.")
    assert "active-memory composer failed; using deterministic synthesis" in caplog.text


def test_active_memory_rejects_composer_no_active_claim_when_core_active_was_found(tmp_path: Path) -> None:
    class ConflictingComposer:
        def compose_active_memory(
            self,
            *,
            prompt: str,
            context: ActiveMemoryPlanningContext,
            wake_summary: str,
            durable_results: tuple[tuple[str, str], ...],
            session_results: tuple[tuple[str, str], ...],
        ) -> ActiveMemoryComposition:
            del prompt, context, wake_summary, durable_results, session_results
            return ActiveMemoryComposition(
                summary="There is currently no active focus or designated task.",
                bullets=("No active focus is documented.",),
            )

    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        composer=ConflictingComposer(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
        )
    )

    assert "No active focus" not in result.block
    assert "Sample is the active focus this week." in result.block
    assert result.summary.startswith("Sample is the active focus this week.")


def test_active_memory_stops_new_work_after_timeout(tmp_path: Path) -> None:
    class SlowWakeBuilder(_CountingWakeBuilder):
        def build(self, req: WakeReq) -> WakeResp:
            sleep(0.12)
            return super().build(req)

    search_engine = _StubSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=SlowWakeBuilder(),
        search_engine=search_engine,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            timeout_ms=100,
        )
    )

    assert search_engine.requests == []
    assert result.summary.startswith("wake block")

def test_active_memory_skips_composer_when_timeout_cannot_absorb_llm_call(tmp_path: Path) -> None:
    class RecordingComposer(_StubActiveMemoryComposer):
        calls = 0

        def compose_active_memory(self, **kwargs) -> ActiveMemoryComposition:
            self.calls += 1
            return super().compose_active_memory(**kwargs)

    search_engine = _StubSearchEngine()
    composer = RecordingComposer()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        composer=composer,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
            timeout_ms=5000,
        )
    )

    assert composer.calls == 0
    assert result.kind == "memory"
    assert "Sample is the active focus this week." in result.block



def test_active_memory_low_timeout_uses_snippets_without_loading_file_content(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text(
        "FILE CONTENT SHOULD NOT BE LOADED FOR LOW TIMEOUTS\n",
        encoding="utf-8",
    )

    class SnippetSearchEngine(_StubSearchEngine):
        def search(self, req: SearchReq):
            self.requests.append(req)
            return _make_response(
                [
                    _make_result(
                        path="core/active.md",
                        snippet="Snippet says Sample is the active focus this week.",
                        score=0.92,
                    )
                ]
            )

    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=SnippetSearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
            timeout_ms=5000,
        )
    )

    assert "Snippet says Sample is the active focus this week." in result.block
    assert "FILE CONTENT SHOULD NOT BE LOADED" not in result.block
    assert "FILE CONTENT SHOULD NOT BE LOADED" not in result.summary


def test_active_memory_budgets_search_queries_to_deadline(tmp_path: Path) -> None:
    class ManyQueryPlanner:
        def plan_active_memory(
            self,
            *,
            prompt: str,
            context: ActiveMemoryPlanningContext,
        ) -> ActiveMemoryRetrievalPlan:
            del prompt, context
            return ActiveMemoryRetrievalPlan(
                durable_queries=("one", "two", "three", "four"),
                session_queries=(),
                include_sessions=False,
                durable_limit=8,
                session_limit=0,
            )

    class SlowSearchEngine(_StubSearchEngine):
        def search(self, req: SearchReq):
            sleep(0.04)
            return super().search(req)

    search_engine = SlowSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        planner=ManyQueryPlanner(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
            timeout_ms=120,
        ).model_copy(update={"timeout_ms": 2300})
    )

    assert 1 <= len(search_engine.requests) < 4
    assert result.kind == "memory"


def test_active_memory_disables_rerank_when_total_timeout_cannot_absorb_it(tmp_path: Path) -> None:
    class ManyQueryPlanner:
        def plan_active_memory(
            self,
            *,
            prompt: str,
            context: ActiveMemoryPlanningContext,
        ) -> ActiveMemoryRetrievalPlan:
            del prompt, context
            return ActiveMemoryRetrievalPlan(
                durable_queries=("one", "two", "three"),
                session_queries=(),
                include_sessions=False,
                durable_limit=8,
                session_limit=0,
            )

    class RerankSensitiveSearchEngine(_StubSearchEngine):
        def search(self, req: SearchReq):
            self.requests.append(req)
            if req.rerank != "false":
                sleep(0.08)
            return super().search(req)

    search_engine = RerankSensitiveSearchEngine()
    engine = ActiveMemoryEngine(
        wake_builder=WakeBuilder(root=tmp_path),
        search_engine=search_engine,
        planner=ManyQueryPlanner(),
    )

    started = monotonic()
    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
            timeout_ms=5000,
        )
    )
    elapsed = monotonic() - started

    assert result.kind == "memory"
    assert search_engine.requests
    assert {req.rerank for req in search_engine.requests} == {"false"}
    assert elapsed < 0.08


def test_active_memory_request_accepts_larger_timeout_for_slow_local_models() -> None:
    req = ActiveMemoryReq(
        prompt="what are we working on today",
        agent="claude",
        timeout_ms=12000,
    )

    assert req.timeout_ms == 12000


def test_active_memory_partial_ok_returns_partial_when_search_errors_after_wake(tmp_path: Path) -> None:
    class ExplodingSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            raise RuntimeError("search provider unavailable")

    class EmptyWakeBuilder:
        def build(self, req: WakeReq) -> WakeResp:  # pragma: no cover - test stub
            return WakeResp(
                profile=req.profile,
                tokens_estimated=5,
                block="Wake block gathered before search error.",
                sources=["core/user.md"],
                frozen_at=datetime.now(tz=UTC),
            )

    engine = ActiveMemoryEngine(
        wake_builder=EmptyWakeBuilder(),
        search_engine=ExplodingSearchEngine(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=True,
        )
    )

    assert result.kind == "memory"
    assert result.partial is True
    assert len(result.warnings) >= 1
    assert "error" in result.warnings[0].lower()
    assert "Wake block gathered before search error." in result.block


def test_active_memory_partial_ok_false_raises_on_provider_error(tmp_path: Path) -> None:
    class ExplodingSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            raise RuntimeError("search provider unavailable")

    class EmptyWakeBuilder:
        def build(self, req: WakeReq) -> WakeResp:  # pragma: no cover - test stub
            return WakeResp(
                profile=req.profile,
                tokens_estimated=5,
                block="Wake block.",
                sources=[],
                frozen_at=datetime.now(tz=UTC),
            )

    engine = ActiveMemoryEngine(
        wake_builder=EmptyWakeBuilder(),
        search_engine=ExplodingSearchEngine(),
        budget=BudgetConfig(partial_ok=False),
    )

    with pytest.raises(RuntimeError, match="search provider unavailable"):
        engine.build(
            ActiveMemoryReq(
                prompt="what are we working on today",
                agent="claude",
                include_wake=False,
                partial_ok=False,
            )
        )


def test_active_memory_request_partial_ok_false_raises_even_when_engine_allows_partial(tmp_path: Path) -> None:
    class ExplodingSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            raise RuntimeError("search provider unavailable")

    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=ExplodingSearchEngine(),
        budget=BudgetConfig(partial_ok=True),
    )

    with pytest.raises(RuntimeError, match="search provider unavailable"):
        engine.build(
            ActiveMemoryReq(
                prompt="what are we working on today",
                agent="claude",
                include_wake=False,
                partial_ok=False,
            )
        )


def test_active_memory_engine_partial_ok_false_raises_even_when_request_allows_partial(tmp_path: Path) -> None:
    class ExplodingSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            raise RuntimeError("search provider unavailable")

    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=ExplodingSearchEngine(),
        budget=BudgetConfig(partial_ok=False),
    )

    with pytest.raises(RuntimeError, match="search provider unavailable"):
        engine.build(
            ActiveMemoryReq(
                prompt="what are we working on today",
                agent="claude",
                include_wake=False,
                partial_ok=True,
            )
        )


def test_active_memory_partial_ok_returns_partial_from_helper_and_wake(tmp_path: Path) -> None:
    """When search errors but wake and helper context were gathered, partial returns both."""
    (tmp_path / "wiki").mkdir(parents=True)
    (tmp_path / "wiki" / "hot.md").write_text(
        "---\ntitle: Hot Cache\n---\n\n## Summary\nHelper context test.\n\n## Current Focus\n- Active testing work.\n",
        encoding="utf-8",
    )

    class ExplodingSearchEngine:
        def search(self, req: SearchReq):  # pragma: no cover - test stub
            raise RuntimeError("search provider error")

    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=ExplodingSearchEngine(),
        root=tmp_path,
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=True,
        )
    )

    assert result.partial is True
    assert len(result.warnings) >= 1
    assert "error" in result.warnings[0].lower()
    assert result.kind == "memory"
    # Should have some content from helper context and wake block
    assert "Active testing work" in result.block or "wake block" in result.block


def test_active_memory_normal_response_not_partial(tmp_path: Path) -> None:
    engine = ActiveMemoryEngine(
        wake_builder=_CountingWakeBuilder(),
        search_engine=_StubSearchEngine(),
    )

    result = engine.build(
        ActiveMemoryReq(
            prompt="what are we working on today",
            agent="claude",
            include_wake=False,
        )
    )

    assert result.partial is False
    assert result.warnings == []
