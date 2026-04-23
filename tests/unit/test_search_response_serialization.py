from __future__ import annotations

from datetime import UTC, datetime

from dory_core.types import (
    ActiveMemoryResp,
    SearchResp,
    SearchResult,
    WakeResp,
    serialize_active_memory_response,
    serialize_search_response,
    serialize_wake_response,
)


def test_search_response_hides_debug_fields_by_default() -> None:
    response = SearchResp(
        query="dory",
        count=1,
        took_ms=12,
        results=[
            SearchResult(
                path="projects/dory/state.md",
                lines="1-4",
                score=0.006,
                score_normalized=0.1,
                rank_score=1.0,
                evidence_class="canonical",
                snippet="Dory retrieval state.",
                frontmatter={"title": "Dory", "type": "project"},
                confidence="high",
            )
        ],
    )

    payload = serialize_search_response(response)
    result = payload["results"][0]

    assert result == {
        "path": "projects/dory/state.md",
        "lines": "1-4",
        "evidence_class": "canonical",
        "snippet": "Dory retrieval state.",
        "stale_warning": None,
        "confidence": "high",
    }


def test_search_response_keeps_debug_fields_when_requested() -> None:
    response = SearchResp(
        query="dory",
        count=1,
        took_ms=12,
        results=[
            SearchResult(
                path="projects/dory/state.md",
                lines="1-4",
                score=0.006,
                score_normalized=0.1,
                rank_score=1.0,
                evidence_class="canonical",
                snippet="Dory retrieval state.",
                frontmatter={"title": "Dory", "type": "project"},
            )
        ],
    )

    result = serialize_search_response(response, debug=True)["results"][0]

    assert result["score"] == 0.006
    assert result["score_normalized"] == 0.1
    assert result["rank_score"] == 1.0
    assert result["frontmatter"] == {"title": "Dory", "type": "project"}


def test_wake_response_hides_debug_fields_by_default() -> None:
    response = WakeResp(
        profile="coding",
        tokens_estimated=123,
        block="## Wake",
        sources=["core/active.md"],
        frozen_at=datetime.now(tz=UTC),
    )

    assert serialize_wake_response(response) == {
        "profile": "coding",
        "block": "## Wake",
    }


def test_wake_response_keeps_debug_fields_when_requested() -> None:
    response = WakeResp(
        profile="coding",
        tokens_estimated=123,
        block="## Wake",
        sources=["core/active.md"],
        frozen_at=datetime.now(tz=UTC),
    )

    payload = serialize_wake_response(response, debug=True)

    assert payload["tokens_estimated"] == 123
    assert payload["sources"] == ["core/active.md"]
    assert "frozen_at" in payload


def test_active_memory_response_hides_debug_fields_by_default() -> None:
    response = ActiveMemoryResp(
        kind="memory",
        block="## Active memory",
        summary="Focus summary",
        took_ms=12,
        profile="coding",
        confidence="high",
        sources=["core/active.md"],
    )

    assert serialize_active_memory_response(response) == {
        "kind": "memory",
        "block": "## Active memory",
        "summary": "Focus summary",
        "sources": ["core/active.md"],
    }


def test_active_memory_response_keeps_debug_fields_when_requested() -> None:
    response = ActiveMemoryResp(
        kind="memory",
        block="## Active memory",
        summary="Focus summary",
        took_ms=12,
        profile="coding",
        confidence="high",
        sources=["core/active.md"],
    )

    payload = serialize_active_memory_response(response, debug=True)

    assert payload["took_ms"] == 12
    assert payload["profile"] == "coding"
    assert payload["confidence"] == "high"
