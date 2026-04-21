from __future__ import annotations

from dory_core.types import SearchResp, SearchResult, serialize_search_response


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
