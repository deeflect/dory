from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import sleep

from dory_core.active_memory import ActiveMemoryEngine
from dory_core.retrieval_planner import ActiveMemoryComposition, ActiveMemoryPlanningContext, ActiveMemoryRetrievalPlan
from dory_core.types import ActiveMemoryReq, SearchReq, WakeReq, WakeResp
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
                        snippet="Session note: Rooster is still the active focus.",
                        score=0.61,
                    )
                ]
            )
        return _make_response(
            [
                _make_result(
                    path="core/active.md",
                    snippet="Rooster is the active focus this week.",
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
            durable_queries=("rooster active focus", "rooster pricing"),
            session_queries=("rooster follow-up",),
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
            summary="Rooster remains the active focus.",
            bullets=(
                "Rooster remains the active focus.",
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
    assert "## Durable evidence" in result.block
    assert "core/active.md" in result.sources


def test_active_memory_builds_memory_block_for_state_question(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text(
        "Rooster is the active focus this week.\n",
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
    assert "Rooster is the active focus this week." in result.summary
    assert "Session note: Rooster is still the active focus." in result.summary
    assert "Session note: Rooster is still the active focus." in result.block
    assert search_engine.requests[0].corpus == "durable"
    assert search_engine.requests[0].include_content is True
    assert search_engine.requests[0].rerank == "true"
    assert search_engine.requests[1].corpus == "sessions"
    assert search_engine.requests[1].include_content is False
    assert search_engine.requests[1].rerank == "false"


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

    assert "wiki/hot.md" in result.sources
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


def test_active_memory_triggers_for_recent_work_question(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "active.md").write_text(
        "Rooster is the active focus this week.\n",
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
        "---\ntitle: Hot Cache\n---\n\n# Recent Context\n\n## Summary\nRooster remains the active focus.\n",
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

    assert "wiki/hot.md" in result.sources
    assert "wiki/index.md" in result.sources
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
        "Rooster remains the active focus.\n\n"
        "## Current Focus\n"
        "- Rooster remains the active focus.\n\n"
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

    assert result.summary.startswith("Rooster remains the active focus.")
    assert "- Session note: Rooster is still the active focus." in result.block


def test_active_memory_uses_planner_queries_and_llm_composition(tmp_path: Path) -> None:
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
            timeout_ms=5000,
        )
    )

    assert result.summary == "Rooster remains the active focus."
    assert "- Pricing follow-up is still active in the latest session." in result.block
    assert search_engine.requests[0].query == "rooster active focus"
    assert search_engine.requests[1].query == "rooster pricing"
    assert search_engine.requests[2].query == "rooster follow-up"


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
    assert "Rooster is the active focus this week." in result.block
    assert result.summary.startswith("Rooster is the active focus this week.")


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
