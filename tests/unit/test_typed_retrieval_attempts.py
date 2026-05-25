"""Tests for Slice 7 — Typed Retrieval Attempts.

Covers:
- Schema validation for all six attempt kinds.
- Invalid schema rejection.
- Fallback conversion from legacy query-list plans to typed plans.
- Deterministic fallback plans for search and active-memory.
- KernelRetrievalEngine execution with and without backends.
- Session gating in fallback plans.
- Typed retrieval planner protocol shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dory_core.kernel_retrieval import KernelAttemptResult, KernelRetrievalEngine, flatten_kernel_results
from dory_core.retrieval_planner import (
    ActiveMemoryRetrievalPlan,
    ClaimLookup,
    DurableSearch,
    EntityLookup,
    LinkNeighbors,
    ObservationLookup,
    SearchRetrievalPlan,
    SessionRecall,
    TypedRetrievalPlan,
    TypedRetrievalPlanner,
    _typed_fallback_active_memory,
    _typed_fallback_search,
    plan_from_active_memory_plan,
    plan_from_search_retrieval_plan,
    validate_typed_attempt,
    validate_typed_plan,
)

# ===================================================================
# Typed attempt construction and schema validation
# ===================================================================


class TestValidateTypedAttempt:
    """validate_typed_attempt should accept valid payloads and reject invalid ones."""

    def test_entity_lookup_valid(self) -> None:
        attempt = validate_typed_attempt({"kind": "entity_lookup", "name": "palace"})
        assert isinstance(attempt, EntityLookup)
        assert attempt.name == "palace"
        assert attempt.family is None

    def test_entity_lookup_with_family(self) -> None:
        attempt = validate_typed_attempt({"kind": "entity_lookup", "name": "palace", "family": "project"})
        assert isinstance(attempt, EntityLookup)
        assert attempt.name == "palace"
        assert attempt.family == "project"

    def test_claim_lookup_valid(self) -> None:
        attempt = validate_typed_attempt({"kind": "claim_lookup", "entity_id": "project:palace"})
        assert isinstance(attempt, ClaimLookup)
        assert attempt.entity_id == "project:palace"

    def test_claim_lookup_with_kind(self) -> None:
        attempt = validate_typed_attempt({"kind": "claim_lookup", "entity_id": "e1", "kind_filter": "status"})
        assert isinstance(attempt, ClaimLookup)
        assert attempt.kind == "status"

    def test_observation_lookup_minimal(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="Missing required field"):
            validate_typed_attempt({"kind": "observation_lookup"})

    def test_observation_lookup_with_entity(self) -> None:
        attempt = validate_typed_attempt({"kind": "observation_lookup", "entity_id": "e1", "query": "state"})
        assert isinstance(attempt, ObservationLookup)
        assert attempt.entity_id == "e1"
        assert attempt.query == "state"

    def test_session_recall_valid(self) -> None:
        attempt = validate_typed_attempt({"kind": "session_recall", "query": "what did I work on"})
        assert isinstance(attempt, SessionRecall)
        assert attempt.query == "what did I work on"

    def test_durable_search_valid(self) -> None:
        attempt = validate_typed_attempt({"kind": "durable_search", "query": "palace configuration"})
        assert isinstance(attempt, DurableSearch)
        assert attempt.query == "palace configuration"
        assert attempt.mode == "hybrid"

    def test_durable_search_with_mode(self) -> None:
        attempt = validate_typed_attempt({"kind": "durable_search", "query": "test", "mode": "bm25"})
        assert isinstance(attempt, DurableSearch)
        assert attempt.mode == "bm25"

    def test_link_neighbors_valid(self) -> None:
        attempt = validate_typed_attempt({"kind": "link_neighbors", "path": "projects/palace/state.md"})
        assert isinstance(attempt, LinkNeighbors)
        assert attempt.path == "projects/palace/state.md"
        assert attempt.direction == "out"

    def test_link_neighbors_with_direction(self) -> None:
        attempt = validate_typed_attempt(
            {"kind": "link_neighbors", "path": "projects/palace/state.md", "direction": "in"}
        )
        assert isinstance(attempt, LinkNeighbors)
        assert attempt.direction == "in"

    def test_invalid_kind_rejected(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="Unknown or missing attempt kind"):
            validate_typed_attempt({"kind": "invalid_kind", "name": "x"})

    def test_missing_required_field_rejected(self) -> None:
        import pytest
        # entity_lookup requires 'name'
        with pytest.raises(ValueError, match="Missing required field"):
            validate_typed_attempt({"kind": "entity_lookup"})

    def test_session_recall_missing_query_rejected(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="Missing required field"):
            validate_typed_attempt({"kind": "session_recall"})

    def test_extra_field_rejected(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="Unexpected field"):
            validate_typed_attempt({"kind": "entity_lookup", "name": "palace", "extra": "nope"})

    def test_invalid_enum_rejected(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="must be one of"):
            validate_typed_attempt({"kind": "durable_search", "query": "palace", "mode": "bogus"})

    def test_empty_required_string_rejected(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="non-empty string"):
            validate_typed_attempt({"kind": "session_recall", "query": "   "})

    def test_non_dict_rejected(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="must be a dict"):
            validate_typed_plan("not a dict")


class TestValidateTypedPlan:
    """validate_typed_plan should accept single attempts and attempt lists."""

    def test_single_attempt(self) -> None:
        plan = validate_typed_plan({"kind": "entity_lookup", "name": "palace", "family": "project"})
        assert not plan.fallback
        assert len(plan.attempts) == 1
        assert isinstance(plan.attempts[0], EntityLookup)

    def test_attempt_list(self) -> None:
        plan = validate_typed_plan(
            {
                "attempts": [
                    {"kind": "entity_lookup", "name": "palace"},
                    {"kind": "durable_search", "query": "palace configuration"},
                ]
            }
        )
        assert len(plan.attempts) == 2
        assert isinstance(plan.attempts[0], EntityLookup)
        assert isinstance(plan.attempts[1], DurableSearch)


# ===================================================================
# Fallback conversion — legacy plans → typed plans
# ===================================================================


class TestPlanFromSearchRetrievalPlan:
    """plan_from_search_retrieval_plan should bridge old plan types."""

    def test_none_plan_uses_fallback(self) -> None:
        plan = plan_from_search_retrieval_plan(None, query="hello", corpus="durable")
        assert plan.fallback
        assert len(plan.attempts) == 1
        assert isinstance(plan.attempts[0], DurableSearch)
        assert plan.attempts[0].query == "hello"

    def test_none_plan_all_includes_session(self) -> None:
        plan = plan_from_search_retrieval_plan(None, query="hello", corpus="all")
        assert plan.fallback
        assert len(plan.attempts) == 2
        assert isinstance(plan.attempts[0], DurableSearch)
        assert isinstance(plan.attempts[1], SessionRecall)

    def test_plan_with_durable_queries(self) -> None:
        old = SearchRetrievalPlan(
            durable_queries=("palace", "dory"), session_queries=(), include_session_results=False
        )
        plan = plan_from_search_retrieval_plan(old, query="x", corpus="durable")
        assert plan.fallback
        assert len(plan.attempts) == 2
        assert all(isinstance(a, DurableSearch) for a in plan.attempts)

    def test_plan_with_session_queries(self) -> None:
        old = SearchRetrievalPlan(
            durable_queries=("palace",),
            session_queries=("recent session",),
            include_session_results=True,
        )
        plan = plan_from_search_retrieval_plan(old, query="x", corpus="all")
        assert len(plan.attempts) == 2
        assert isinstance(plan.attempts[0], DurableSearch)
        assert isinstance(plan.attempts[1], SessionRecall)


class TestPlanFromActiveMemoryPlan:
    """plan_from_active_memory_plan should bridge old active-memory plans."""

    def test_none_plan_uses_fallback(self) -> None:
        plan = plan_from_active_memory_plan(None, prompt="what did I work on")
        assert plan.fallback
        assert len(plan.attempts) >= 1

    def test_none_plan_general_prompt(self) -> None:
        plan = plan_from_active_memory_plan(None, prompt="project status")
        # General prompt → no session queries
        assert any(isinstance(a, DurableSearch) for a in plan.attempts)
        # Session recall should NOT be present for general prompts
        assert not any(isinstance(a, SessionRecall) for a in plan.attempts)

    def test_with_plan(self) -> None:
        old = ActiveMemoryRetrievalPlan(
            durable_queries=("palace state",),
            session_queries=(),
            include_sessions=False,
            durable_limit=6,
            session_limit=0,
        )
        plan = plan_from_active_memory_plan(old, prompt="x")
        assert len(plan.attempts) == 1
        assert isinstance(plan.attempts[0], DurableSearch)


# ===================================================================
# Deterministic fallback plans (pure functions)
# ===================================================================


class TestTypedFallbackSearch:
    """_typed_fallback_search should produce correct fallback plans."""

    def test_durable_only(self) -> None:
        plan = _typed_fallback_search(query="test", corpus="durable")
        assert plan.fallback
        assert len(plan.attempts) == 1
        assert isinstance(plan.attempts[0], DurableSearch)
        assert plan.attempts[0].query == "test"
        assert plan.attempts[0].mode == "hybrid"

    def test_all_corpus_includes_session(self) -> None:
        plan = _typed_fallback_search(query="test", corpus="all")
        assert len(plan.attempts) == 2
        assert isinstance(plan.attempts[0], DurableSearch)
        assert isinstance(plan.attempts[1], SessionRecall)

    def test_empty_query(self) -> None:
        plan = _typed_fallback_search(query="", corpus="durable")
        assert len(plan.attempts) == 1
        assert plan.attempts[0].query == ""


class TestTypedFallbackActiveMemory:
    """_typed_fallback_active_memory should respect session gating keywords."""

    def test_general_prompt(self) -> None:
        plan = _typed_fallback_active_memory(prompt="project status")
        assert plan.fallback
        assert len(plan.attempts) == 1
        assert isinstance(plan.attempts[0], DurableSearch)

    def test_session_keyword_triggers_session(self) -> None:
        plan = _typed_fallback_active_memory(prompt="what did I work on last session")
        assert len(plan.attempts) == 2
        assert isinstance(plan.attempts[0], DurableSearch)
        assert isinstance(plan.attempts[1], SessionRecall)

    def test_yesterday_triggers_session(self) -> None:
        plan = _typed_fallback_active_memory(prompt="what did I work on yesterday")
        assert len(plan.attempts) == 2
        assert isinstance(plan.attempts[1], SessionRecall)

    def test_today_triggers_session(self) -> None:
        plan = _typed_fallback_active_memory(prompt="today's tasks")
        assert len(plan.attempts) == 2
        assert isinstance(plan.attempts[1], SessionRecall)

    def test_empty_prompt(self) -> None:
        plan = _typed_fallback_active_memory(prompt="")
        assert plan.empty


# ===================================================================
# KernelRetrievalEngine
# ===================================================================


class TestKernelRetrievalEngine:
    """KernelRetrievalEngine should dispatch attempts correctly."""

    def test_empty_plan(self) -> None:
        engine = KernelRetrievalEngine()
        results = engine.execute(TypedRetrievalPlan(attempts=()))
        assert results == []

    def test_entity_lookup_with_no_backend(self) -> None:
        engine = KernelRetrievalEngine()
        plan = TypedRetrievalPlan(attempts=(EntityLookup(name="palace"),))
        results = engine.execute(plan)
        assert len(results) == 1
        assert results[0].kind == "entity_lookup"
        # No root/registry → None payload
        assert results[0].payload is None
        assert results[0].error is None

    def test_claim_lookup_no_backend(self) -> None:
        engine = KernelRetrievalEngine()
        plan = TypedRetrievalPlan(attempts=(ClaimLookup(entity_id="e1"),))
        results = engine.execute(plan)
        assert len(results) == 1
        assert results[0].kind == "claim_lookup"
        assert results[0].error is not None
        assert "claim_store not configured" in results[0].error

    def test_observation_lookup_no_backend(self) -> None:
        engine = KernelRetrievalEngine()
        plan = TypedRetrievalPlan(attempts=(ObservationLookup(),))
        results = engine.execute(plan)
        assert len(results) == 1
        assert results[0].kind == "observation_lookup"
        assert results[0].error is not None
        assert "observation_retrieval not configured" in results[0].error

    def test_session_recall_no_backend(self) -> None:
        engine = KernelRetrievalEngine()
        plan = TypedRetrievalPlan(attempts=(SessionRecall(query="test"),))
        results = engine.execute(plan)
        assert len(results) == 1
        assert results[0].kind == "session_recall"
        assert "search_engine not configured" in results[0].error

    def test_durable_search_no_backend(self) -> None:
        engine = KernelRetrievalEngine()
        plan = TypedRetrievalPlan(attempts=(DurableSearch(query="test"),))
        results = engine.execute(plan)
        assert len(results) == 1
        assert results[0].kind == "durable_search"
        assert "search_engine not configured" in results[0].error

    def test_link_neighbors_no_backend(self) -> None:
        engine = KernelRetrievalEngine()
        plan = TypedRetrievalPlan(attempts=(LinkNeighbors(path="test.md"),))
        results = engine.execute(plan)
        assert len(results) == 1
        assert results[0].kind == "link_neighbors"
        assert "link_service not configured" in results[0].error

    def test_max_attempts_enforced(self) -> None:
        engine = KernelRetrievalEngine()
        attempts = tuple(
            DurableSearch(query=f"q{i}") for i in range(10)
        )
        plan = TypedRetrievalPlan(attempts=attempts)
        results = engine.execute(plan, max_attempts=3)
        assert len(results) == 3

    def test_multiple_attempts_in_plan(self) -> None:
        engine = KernelRetrievalEngine()
        plan = TypedRetrievalPlan(
            attempts=(
                EntityLookup(name="palace", family="project"),
                DurableSearch(query="palace config"),
                LinkNeighbors(path="projects/palace/state.md"),
            )
        )
        results = engine.execute(plan)
        assert len(results) == 3
        assert results[0].kind == "entity_lookup"
        assert results[1].kind == "durable_search"
        assert results[2].kind == "link_neighbors"

    def test_unknown_attempt_handled_gracefully(self) -> None:
        engine = KernelRetrievalEngine()
        # All attempts without their required backends return a graceful error result
        plan = TypedRetrievalPlan(attempts=(DurableSearch(query="test"),))
        results = engine.execute(plan)
        assert len(results) == 1
        # Should fail gracefully with a descriptive error (search engine not configured)
        assert results[0].error is not None
        assert "search_engine not configured" in results[0].error

    def test_durable_search_with_mock_engine(self) -> None:
        """DurableSearch with a mock search_engine should return results."""
        engine = KernelRetrievalEngine(search_engine=_MockSearchEngine())
        plan = TypedRetrievalPlan(attempts=(DurableSearch(query="test", mode="hybrid"),))
        results = engine.execute(plan)
        assert len(results) == 1
        assert results[0].error is None
        assert results[0].kind == "durable_search"
        payload = results[0].payload
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0].path == "mock/path.md"

    def test_flatten_kernel_results(self) -> None:
        """flatten_kernel_results should extract search-like payloads."""
        from dory_core.types import SearchResult

        r1 = SearchResult(path="a.md", lines="1-1", score=1.0, snippet="a")
        r2 = SearchResult(path="b.md", lines="1-1", score=0.5, snippet="b")
        results = [
            KernelAttemptResult(kind="durable_search", payload=[r1, r2]),
            KernelAttemptResult(kind="entity_lookup", payload=None),
            KernelAttemptResult(kind="session_recall", payload=[r1]),
            KernelAttemptResult(kind="claim_lookup", payload=({"c": "d"},)),
        ]
        flat = flatten_kernel_results(results)
        assert len(flat) == 3  # durable_search(2) + session_recall(1)
        assert flat[0] is r1
        assert flat[1] is r2

    def test_flatten_skips_errors(self) -> None:
        results = [
            KernelAttemptResult(kind="durable_search", error="boom"),
            KernelAttemptResult(kind="durable_search", payload=[]),
        ]
        flat = flatten_kernel_results(results)
        assert len(flat) == 0

    def test_entity_lookup_with_root(self) -> None:
        """Entity lookup with a root path but no registry should return None gracefully."""
        engine = KernelRetrievalEngine(root=Path("/tmp"))
        plan = TypedRetrievalPlan(attempts=(EntityLookup(name="nonexistent"),))
        results = engine.execute(plan)
        assert len(results) == 1
        # Should not crash — returns None cleanly
        assert results[0].payload is None


# ===================================================================
# TypedRetrievalPlanner protocol (duck-typing test)
# ===================================================================


class TestTypedRetrievalPlannerProtocol:
    """TypedRetrievalPlanner should accept any object with the right methods."""

    def test_duck_type_compliance(self) -> None:
        @dataclass
        class FakePlanner:
            def plan_search(self, *, query: str, corpus: str) -> TypedRetrievalPlan:
                return TypedRetrievalPlan(
                    attempts=(DurableSearch(query=query),),
                    fallback=True,
                )

            def plan_active_memory(
                self,
                *,
                prompt: str,
                context: Any,
            ) -> TypedRetrievalPlan:
                return TypedRetrievalPlan(
                    attempts=(DurableSearch(query=prompt),),
                    fallback=True,
                )

        planner: TypedRetrievalPlanner = FakePlanner()
        plan = planner.plan_search(query="hello", corpus="durable")
        assert isinstance(plan, TypedRetrievalPlan)
        assert len(plan.attempts) == 1
        assert isinstance(plan.attempts[0], DurableSearch)


# ===================================================================
# TypedRetrievalPlan properties
# ===================================================================


class TestTypedRetrievalPlan:
    def test_empty_property(self) -> None:
        assert TypedRetrievalPlan(attempts=()).empty
        assert not TypedRetrievalPlan(attempts=(DurableSearch(query="x"),)).empty


# ===================================================================
# Mocks for engine tests
# ===================================================================


class _MockSearchEngine:
    """Minimal search engine stub for testing kernel retrieval."""

    @staticmethod
    def search(req: object) -> object:
        from dory_core.types import SearchResult

        class FakeSearchResp:
            results = [SearchResult(
                path="mock/path.md",
                lines="1-1",
                score=1.0,
                snippet="mock content",
            )]
            warnings = []
            took_ms = 1
            query = getattr(req, 'query', '') if req else ''

        return FakeSearchResp()
